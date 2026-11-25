import json
import unittest
from unittest.mock import patch

from ai.notice_extraction import process_notice_extraction
from ai.test_llm_evaluation import row
from backend.db.init_db import init_db


class NoticeExtractionTest(unittest.TestCase):
    def setUp(self):
        self.db = init_db(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("""INSERT INTO bid_notice
            (notice_no,title,api_raw,first_seen_at,updated_at) VALUES ('N1','공고','{}','now','now')""")
        for attachment, kind, body, flag in (
            (1, "CRITERIA", "제출서류는 다음과 같다\n1. 실적증명서 1부\n\n가격 50점", 0),
            (2, "NOTICE", "가점 신청 시 특허증 제출서류", None),
        ):
            self.db.execute("""INSERT INTO bid_attachment
                (id,notice_no,doc_kind,filename,source,download_status,fetched_at,sha256)
                VALUES (?,'N1',?,?,'OPEN_API','SUCCESS','now',?)""",
                (attachment, kind, kind, f"hash{attachment}"))
            self.db.execute("""INSERT INTO bid_document_content
                (attachment_id,read_status,text,read_at,rule_extracted)
                VALUES (?,'SUCCESS',?,'now',?)""", (attachment, body, flag))
        self.db.commit()
        env_patch = patch("ai.notice_extraction.load_env")
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def document_rows(self):
        return self.db.execute("""SELECT name,updated_by,verification_status,source_quote
            FROM required_document_item ORDER BY position""").fetchall()

    def test_separate_requests_in_order_and_idempotent_db_writes(self):
        calls = []

        def fake_call(payload, key):
            self.assertFalse(self.db.in_transaction)
            purpose = payload["text"]["format"]["name"]
            calls.append(purpose)
            if purpose == "evaluation_rules":
                self.assertEqual(self.document_rows(), [])
                result = {"rows": [row()], "issues": []}
            else:
                self.assertEqual(self.db.execute("SELECT updated_by FROM evaluation_rule").fetchone()[0], "LLM")
                self.assertEqual([tuple(item)[:2] for item in self.document_rows()], [("실적증명서", "PARSER")])
                result = {"covered_attachment_ids": [1, 2], "issues": [], "items": [
                    dict(stage="QUALIFICATION", submit_to="CONTRACT_DEPT", name="특허증",
                         requirement_type="CONDITIONAL", alternative_group=None, validity_note=None,
                         condition_note="가점 신청 시", source_attachment_id=2, source_location="가점 안내",
                         source_quote="가점 신청 시 특허증 제출서류"),
                ]}
            return {"status": "completed", "output": [{"content": [
                {"type": "output_text", "text": json.dumps(result)},
            ]}]}

        first = process_notice_extraction(self.db, "N1", caller=fake_call)
        self.assertEqual(first["evaluation"]["saved"], 1)
        self.assertEqual(first["documents"]["llm_saved"], 1)
        self.assertEqual([tuple(item)[:3] for item in self.document_rows()],
                         [("실적증명서", "PARSER", "REVIEW"), ("특허증", "LLM", "REVIEW")])
        self.assertIsNone(self.db.row_factory)
        second = process_notice_extraction(self.db, "N1", caller=fake_call)
        self.assertEqual(second["evaluation"]["status"], "skipped")
        self.assertEqual(second["documents"]["status"], "already_processed")
        self.assertEqual(calls, ["evaluation_rules", "required_documents"])

    def test_rule_only_notice_has_no_api_calls(self):
        self.db.execute("UPDATE bid_document_content SET rule_extracted=1 WHERE attachment_id=1")
        self.db.execute("UPDATE bid_document_content SET text='공고 기본 정보' WHERE attachment_id=2")
        self.db.commit()

        def fail_if_called(payload, key):
            self.fail("Rule-only notice should not call the API")

        result = process_notice_extraction(self.db, "N1", caller=fail_if_called)
        self.assertEqual(result["evaluation"]["status"], "skipped")
        self.assertEqual(result["documents"]["status"], "rule_only")
        self.assertEqual(len(self.document_rows()), 1)

    def test_api_failure_preserves_rule_results_and_retry_reuses_rows(self):
        def unavailable(payload, key):
            raise RuntimeError("API unavailable")

        result = process_notice_extraction(self.db, "N1", caller=unavailable)
        self.assertEqual([result[part]["status"] for part in ("evaluation", "documents")], ["failed", "failed"])
        self.assertEqual(self.db.execute("SELECT analysis_status FROM notice_analysis_cache").fetchone()[0], "FAILED")
        self.assertEqual(len(self.document_rows()), 1)
        item_id = self.db.execute("SELECT id FROM required_document_item").fetchone()[0]
        process_notice_extraction(self.db, "N1", caller=unavailable)
        self.assertEqual(self.db.execute("SELECT id FROM required_document_item").fetchall(), [(item_id,)])
