"""Finish one collected notice after the evaluation parser has committed its results."""

import argparse
import json
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone

from ai import llm_documents, llm_evaluation, rule_documents
from ai.document_staging import ITEM_FIELDS, digest
from ai.env import load_env
from backend.db.init_db import DEFAULT_DB_PATH, init_db


UNCERTAIN = re.compile(r"또는|및|[;,·]|제외|생략|면제|갈음|경우|해당|필요\s*시|예시|별도|등(?:\s|$)")
SUBMISSION = re.compile(r"제출\s*(?:서류|자료|하[여는]|해야)|[가-힣]+(?:증명서|확인서).*제출")
DOC_FIELDS = (*ITEM_FIELDS, "source_quote", "source_sha256", "updated_by")


def load_sources(db, notice):
    return [dict(row) for row in db.execute("""
        SELECT a.id AS attachment_id,a.filename,a.doc_kind,a.sha256,c.read_status,c.text AS content
        FROM bid_attachment a LEFT JOIN bid_document_content c ON c.attachment_id=a.id
        WHERE a.notice_no=? ORDER BY a.id
    """, (notice,))]


def document_version(sources):
    return digest(json.dumps(sources, ensure_ascii=False, sort_keys=True))


def rule_documents_for(sources):
    candidates = rule_documents.extract_items(s for s in sources if s["doc_kind"] == "CRITERIA")
    confirmed = []
    gaps = []
    for item in candidates:
        quote = item["source_quote"]
        if (item["condition_note"] or UNCERTAIN.search(quote)
                or not rule_documents.LIST_ITEM.match(quote)):
            gaps.append({"attachment_id": item["source_attachment_id"], "quote": quote})
        else:
            confirmed.append(item)
    for source in sources:
        attachment_id = source["attachment_id"]
        covered = {item["source_quote"] for item in confirmed
                   if item["source_attachment_id"] == attachment_id}
        lines = source["content"].splitlines()
        for index, line in enumerate(lines):
            if not SUBMISSION.search(line) or line in covered:
                continue
            if (rule_documents.LIST_START.search(line) and not UNCERTAIN.search(line)
                    and any(next_line in covered for next_line in lines[index + 1:index + 4])):
                continue
            gaps.append({"attachment_id": attachment_id, "quote": line})
    if not confirmed and not gaps:
        gaps.append({"reason": "No explicit qualification-document list was found"})
    return confirmed, gaps


@contextmanager
def save_transaction(db):
    db.execute("BEGIN IMMEDIATE")
    with db:
        yield


def upsert_documents(db, notice, sources, items, updated_by):
    """Keep stable row IDs and the user's checks when an existing item matches."""
    source_map = {source["attachment_id"]: source for source in sources}
    saved = 0
    for item in items:
        source = source_map[item["source_attachment_id"]]
        values = {**item, "notice_no": notice, "source_sha256": source["sha256"],
                  "verification_status": "REVIEW", "updated_by": updated_by}
        rows = db.execute("""SELECT * FROM required_document_item
                           WHERE notice_no=? AND stage=? ORDER BY position""",
                          (notice, item["stage"])).fetchall()
        matches = [row for row in rows
                   if llm_documents.normalize_name(row["name"]) == llm_documents.normalize_name(item["name"])
                   and ((row["condition_note"], row["validity_note"], row["alternative_group"]) ==
                        (item["condition_note"], item["validity_note"], item["alternative_group"])
                        or (row["source_attachment_id"] == item["source_attachment_id"]
                            and row["source_quote"] == item["source_quote"]))]
        if len(matches) > 1:
            raise ValueError("Multiple existing document items match the same result")
        if matches:
            row = matches[0]
            if (row["updated_by"] not in ("PARSER", "LLM") or row["verification_status"] == "VERIFIED"
                    or (updated_by == "PARSER" and row["updated_by"] == "LLM"
                        and row["source_sha256"] == source["sha256"])):
                continue
            values["position"] = row["position"]
        else:
            values["position"] = max((row["position"] for row in rows), default=0) + 1
        if matches and all(matches[0][field] == values[field] for field in DOC_FIELDS):
            continue
        keys = ",".join(DOC_FIELDS)
        placeholders = ",".join("?" for _ in DOC_FIELDS)
        updates = ",".join(f"{field}=excluded.{field}" for field in DOC_FIELDS
                           if field not in ("notice_no", "stage", "position"))
        db.execute(f"""INSERT INTO required_document_item ({keys}) VALUES ({placeholders})
            ON CONFLICT(notice_no,stage,position) DO UPDATE SET {updates}""",
                   [values[field] for field in DOC_FIELDS])
        saved += 1
    return saved


def set_document_status(db, notice, version, status):
    db.execute("""INSERT INTO notice_analysis_cache
        (notice_no,document_version,analysis_status,analyzed_at) VALUES (?,?,?,?)
        ON CONFLICT(notice_no) DO UPDATE SET document_version=excluded.document_version,
        analysis_status=excluded.analysis_status, analyzed_at=excluded.analyzed_at, invalidated_at=NULL""",
        (notice, version, status, datetime.now(timezone.utc).isoformat()))


def documents_done(db, notice, version):
    return db.execute("""SELECT 1 FROM notice_analysis_cache
        WHERE notice_no=? AND document_version=? AND analysis_status='DONE'""",
        (notice, version)).fetchone() is not None


def run_documents(db, notice, request, model, max_output_tokens):
    sources = load_sources(db, notice)
    version = document_version(sources)
    if documents_done(db, notice, version):
        return {"status": "already_processed"}
    readable = [s for s in sources if s["read_status"] == "SUCCESS" and (s["content"] or "").strip()]
    candidates, gaps = rule_documents_for(readable)
    complete_source = {"NOTICE", "CRITERIA"}.issubset({s["doc_kind"] for s in readable})
    with save_transaction(db):
        if document_version(load_sources(db, notice)) != version:
            raise ValueError("Document source changed before saving rule results")
        if documents_done(db, notice, version):
            return {"status": "already_processed"}
        rule_count = upsert_documents(db, notice, readable, candidates, "PARSER")
        set_document_status(db, notice, version,
                            "PENDING" if gaps or not complete_source else "DONE")
    if complete_source and not gaps:
        return {"status": "rule_only", "rule_saved": rule_count}
    try:
        if not complete_source:
            raise ValueError("Readable notice and criteria are required before document fallback")
        payload = llm_documents.request_payload(notice, readable, model, max_output_tokens, candidates)
        data = json.loads(payload["input"][0]["content"])
        data["unresolved_rule_evidence"] = gaps
        payload["input"][0]["content"] = json.dumps(data, ensure_ascii=False)
        payload["instructions"] += (
            "\n이번 요청 범위는 적격심사 단계(QUALIFICATION)만이다. "
            "rule_candidates는 이미 저장된 항목이다. 원문 전체 맥락에서 누락된 서류나 "
            "보완할 조건이 있는 항목만 반환하고, 변경 없는 후보는 반복하지 않는다. "
            "확정 못 한 사항은 issues에 남긴다."
        )
        response, result = request(payload)
        llm_documents.validate_output(result, readable)
        if any(item["stage"] != "QUALIFICATION" for item in result["items"]):
            raise ValueError("Fallback returned documents outside the qualification stage")
        with save_transaction(db):
            if document_version(load_sources(db, notice)) != version:
                raise ValueError("Document source changed during LLM extraction")
            if documents_done(db, notice, version):
                return {"status": "already_processed", "usage": response.get("usage")}
            saved = upsert_documents(db, notice, readable, result["items"], "LLM")
            set_document_status(db, notice, version, "DONE")
    except (RuntimeError, ValueError, sqlite3.Error, OSError):
        with save_transaction(db):
            if (document_version(load_sources(db, notice)) == version
                    and not documents_done(db, notice, version)):
                set_document_status(db, notice, version, "FAILED")
        raise
    return {"status": "review", "rule_saved": rule_count, "llm_saved": saved,
            "issues": result["issues"], "usage": response.get("usage")}


def process_notice_extraction(db, notice, *, caller=llm_documents.call_openai,
                              max_chars=300000, max_output_tokens=16000):
    """Run evaluation fallback, then save rule documents and fill their gaps.

    Call with a collected notice and a connection with no active transaction.
    """
    if db.in_transaction:
        raise ValueError("Commit the collector's evaluation parser results first")
    load_env()
    model = os.environ.get("OPENAI_MODEL", llm_documents.DEFAULT_MODEL)

    def request(payload):
        if len(json.dumps(payload, ensure_ascii=False)) > max_chars:
            raise ValueError("LLM input exceeds the configured size limit")
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key and caller is llm_documents.call_openai:
            raise ValueError("OPENAI_API_KEY is required for LLM fallback")
        response = caller(payload, key)
        return response, llm_documents.parse_response(response)

    row_factory = db.row_factory
    db.row_factory = sqlite3.Row
    result = {"notice_no": notice, "evaluation": {"status": "skipped"}}
    try:
        try:
            if llm_evaluation.needs_evaluation_fallback(db, notice):
                mode, sources, rows = llm_evaluation.load_case(db, notice)
                version = llm_evaluation.source_version(sources, rows)
                response, draft = request(llm_evaluation.payload_for(
                    notice, mode, sources, rows, model, max_output_tokens))
                llm_evaluation.apply(db, notice, version, mode, draft)
                result["evaluation"] = {"status": "review", "saved": len(draft["rows"]),
                                        "issues": draft["issues"], "usage": response.get("usage")}
        except (RuntimeError, ValueError, sqlite3.Error, OSError) as error:
            result["evaluation"] = {"status": "failed", "error": str(error)}
        try:
            result["documents"] = run_documents(db, notice, request, model, max_output_tokens)
        except (RuntimeError, ValueError, sqlite3.Error, OSError) as error:
            result["documents"] = {"status": "failed", "error": str(error)}
        return result
    finally:
        db.row_factory = row_factory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--notice", required=True)
    parser.add_argument("--execute", action="store_true", required=True, help="Enable paid API and DB writes")
    args = parser.parse_args()
    with closing(init_db(args.db)) as db:
        if not db.execute("SELECT 1 FROM bid_notice WHERE notice_no=?", (args.notice,)).fetchone():
            parser.error("Notice must already have been collected")
        result = process_notice_extraction(db, args.notice)
        print(json.dumps(result, ensure_ascii=False))
        if any(result[task]["status"] == "failed" for task in ("evaluation", "documents")):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
