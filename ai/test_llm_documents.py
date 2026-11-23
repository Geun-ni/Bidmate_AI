import copy
import json
import os
import io
import unittest
from unittest.mock import patch

from ai import test_document_staging as fixtures
from ai.llm_documents import extract, load_notice, parse_response, validate_output, call_ollama, request_payload
from ai.evaluate_documents import compare_gold, judge


class LLMDocumentsTest(unittest.TestCase):
    write_sources = fixtures.StagingTest.write_sources
    tearDown = fixtures.StagingTest.tearDown

    def setUp(self):
        fixtures.StagingTest.setUp(self)
        # Add both source kinds and a document name the regex cannot recognize.
        self.db.execute(
            """INSERT INTO staging_source SELECT 2,notice_no,'공고.hwp','NOTICE',
            source_sha256,text_sha256,read_status,content,metadata FROM staging_source WHERE attachment_id=1"""
        )
        self.db.execute(
            "UPDATE staging_source SET content=? WHERE attachment_id=2",
            ("제3조 제출서류\n참여감리원 수첩 사본을 제출한다.",),
        )
        self.db.commit()
        self.result = {
            "covered_attachment_ids": [1, 2], "issues": [], "items": [{
                "stage": "QUALIFICATION", "submit_to": "CONTRACT_DEPT",
                "name": "참여감리원 수첩 사본", "requirement_type": "REQUIRED",
                "alternative_group": None, "condition_note": None, "validity_note": None,
                "source_attachment_id": 2, "source_location": "제3조 제출서류",
                "source_quote": "참여감리원 수첩 사본을 제출한다.",
            }],
        }

    def response(self, result):
        return {"id": "test-response", "status": "completed", "usage": {"input_tokens": 100},
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": json.dumps(result, ensure_ascii=False)}
                ]}]}

    def test_full_source_cached_and_staged_as_review(self):
        captured = []

        def fake(payload, key):
            captured.append(payload)
            return self.response(self.result)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-fixture-key"}):
            first = extract(self.db, "E001", self.folder / "llm", "test-model",
                            300000, 16000, True, fake)
            second = extract(self.db, "E001", self.folder / "llm", "test-model",
                             300000, 16000, True, fake)
        self.assertEqual(len(captured), 1)
        sent = json.loads(captured[0]["input"][0]["content"])
        self.assertEqual([s["attachment_id"] for s in sent["sources"]], [1, 2])
        self.assertEqual(sent["sources"][0]["content"], self.text)
        self.assertEqual(first["status"], "needs_review")
        self.assertEqual(second["items"], 1)
        row = self.db.execute("SELECT * FROM required_document_item").fetchone()
        self.assertEqual(row["name"], "참여감리원 수첩 사본")
        self.assertEqual(row["verification_status"], "REVIEW")
        self.assertEqual(self.db.execute("SELECT extractor_version FROM staging_evidence").fetchone()[0],
                         "openai-documents-v1")

    def test_invalid_outputs_preserve_existing_rows(self):
        _, sources = load_notice(self.db, "E001")
        for change in (
            {"source_quote": "발명한 근거"}, {"source_attachment_id": 999},
            {"stage": "SUPERVISOR"}, {"requirement_type": "CONDITIONAL"},
            {"name": ""}, {"alternative_group": "one-item-group"},
        ):
            result = copy.deepcopy(self.result)
            result["items"][0].update(change)
            with self.assertRaises(ValueError):
                validate_output(result, sources)
        result = copy.deepcopy(self.result)
        result["covered_attachment_ids"] = [1]
        with self.assertRaises(ValueError):
            validate_output(result, sources)
        old = [tuple(r) for r in self.db.execute("SELECT * FROM required_document_item")]
        invalid = copy.deepcopy(self.result)
        invalid["items"][0]["source_quote"] = "허위 근거"
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-fixture-key"}):
            with self.assertRaises(ValueError):
                extract(self.db, "E001", self.folder / "invalid", "test-model", 300000, 16000,
                        True, lambda p, k: self.response(invalid))
        self.assertEqual(old, [tuple(r) for r in self.db.execute("SELECT * FROM required_document_item")])

    def test_incomplete_and_refused_responses(self):
        for response in (
            {"status": "incomplete", "output": []},
            {"status": "completed", "output": [{"content": [{"type": "refusal"}]}]},
        ):
            with self.assertRaises(ValueError):
                parse_response(response)

    def test_ollama_schema_and_native_response(self):
        _, sources = load_notice(self.db, "E001")
        native = {"done": True, "done_reason": "stop", "model": "qwen3.5:4b",
                  "message": {"content": json.dumps(self.result)},
                  "prompt_eval_count": 100, "eval_count": 50}
        captured = []

        def fake_open(request, timeout):
            captured.append(json.loads(request.data))
            self.assertFalse(request.has_header("Authorization"))
            return io.BytesIO(json.dumps(native).encode())

        with patch("ai.llm_documents.urlopen", fake_open):
            response = call_ollama(request_payload("E001", sources, "qwen3.5:4b", 12000))
        self.assertEqual(parse_response(response), self.result)
        self.assertEqual(captured[0]["options"]["num_ctx"], 65536)
        self.assertIn("properties", captured[0]["format"])
        native["done_reason"] = "length"
        with patch("ai.llm_documents.urlopen", fake_open):
            with self.assertRaises(ValueError):
                parse_response(call_ollama(request_payload("E001", sources, "qwen3.5:4b", 12000)))

    def test_dry_run_does_not_call_api_and_limits_do_not_truncate(self):
        def forbidden(*args):
            self.fail("API must not be called")
        self.assertEqual(extract(self.db, "E001", self.folder / "dry", "test-model",
                                 300000, 16000, False, forbidden)["status"], "dry_run")
        with self.assertRaises(ValueError):
            extract(self.db, "E001", self.folder / "dry", "test-model", 10, 16000, True, forbidden)

    def test_gold_counts_and_version_guard(self):
        gold = {"E001": {"reviewer": "human", "review_complete": True, "document_version": "v",
                        "items": [self.result["items"][0], {**self.result["items"][0], "name": "사업자등록증"}]}}
        predictions = {"E001": {"document_version": "v", "items": [
            {**self.result["items"][0], "requirement_type": "OPTIONAL"},
            {**self.result["items"][0], "name": "허위서류"},
        ]}}
        result = compare_gold(gold, predictions)
        self.assertEqual((result["true_positive"], result["false_positive"], result["false_negative"]), (1, 1, 1))
        self.assertEqual(result["precision"], .5)
        self.assertIn("requirement_type", result["details"][0]["matched"][0]["field_mismatches"])
        predictions["E001"]["document_version"] = "stale"
        with self.assertRaises(ValueError):
            compare_gold(gold, predictions)

    def test_llm_judge_does_not_approve_and_checks_evidence(self):
        cache, _ = load_notice(self.db, "E001")
        prediction = {"notice_no": "E001", "document_version": cache["document_version"],
                      "items": self.result["items"]}
        audit = {"covered_attachment_ids": [1, 2], "assessment": "NEEDS_REVIEW",
                 "summary": "사업자등록증 누락", "findings": [{
                     "type": "MISSING", "document_name": "사업자등록증",
                     "explanation": "제출 문구 있음", "source_attachment_id": 1,
                     "source_quote": "사업자등록증 사본을 제출한다.",
                 }]}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-fixture-key"}):
            report = judge(self.db, prediction, self.folder / "judge", "test-model", True,
                           lambda p, k: self.response(audit))
        self.assertTrue(report["human_review_required"])
        self.assertEqual(self.db.execute("SELECT analysis_status FROM notice_analysis_cache").fetchone()[0], "PENDING")
        audit["findings"][0]["source_quote"] = "가짜 인용"
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-fixture-key"}):
            with self.assertRaises(ValueError):
                judge(self.db, prediction, self.folder / "badjudge", "test-model", True,
                      lambda p, k: self.response(audit))
