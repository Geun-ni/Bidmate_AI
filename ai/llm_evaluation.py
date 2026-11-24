"""Generate and validate LLM evaluation-rule drafts; apply only on explicit request."""

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ai.env import load_env
from ai.llm_documents import call_openai, parse_response
from backend.db.init_db import DEFAULT_DB_PATH


PROMPT = Path(__file__).with_name("evaluation_prompt.txt")
CATEGORIES = ["PRICE", "PERFORMANCE", "MANAGEMENT", "OFFICE", "BONUS", "PENALTY", "PASS_SCORE"]
TYPES = ["GROUP", "FORMULA", "TABLE", "FIXED", "THRESHOLD"]
ROW_FIELDS = {
    "row_id": {"type": ["integer", "null"]},
    "parent_position": {"type": ["integer", "null"]},
    "category": {"type": "string", "enum": CATEGORIES},
    "item_name": {"type": "string"},
    "rule_type": {"type": "string", "enum": TYPES},
    "max_score": {"type": ["number", "null"]},
    "formula": {"type": ["string", "null"]},
    "parameters": {"type": ["string", "null"]},
    "input_key": {"type": ["string", "null"]},
    "rounding_digits": {"type": ["integer", "null"]},
    "rounding_mode": {"type": ["string", "null"], "enum": ["ROUND", "FLOOR", "CEIL", None]},
    "attachment_id": {"type": "integer"},
    "source_location": {"type": "string"},
    "source_quote": {"type": "string"},
    "verification_status": {"type": "string", "enum": ["UNVERIFIED", "REVIEW"]},
}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["issues", "rows"],
          "properties": {"issues": {"type": "array", "items": {"type": "string"}},
                         "rows": {"type": "array", "items": {"type": "object",
                                  "additionalProperties": False, "required": list(ROW_FIELDS),
                                  "properties": ROW_FIELDS}}}}


def source_version(sources, rows=()):
    payload = [(s["attachment_id"], s["sha256"], s["read_status"], s["rule_extracted"],
                hashlib.sha256((s["content"] or "").encode()).hexdigest()) for s in sources]
    state = [payload, [{key: row[key] for key in ("id", "position", "verification_status",
             "updated_by", "formula", "source_quote", "updated_at")} for row in rows]]
    return hashlib.sha256(json.dumps(state, ensure_ascii=False).encode()).hexdigest()


def needs_evaluation_fallback(db, notice):
    failed = db.execute("""SELECT 1 FROM bid_attachment a
        JOIN bid_document_content c ON c.attachment_id=a.id
        WHERE a.notice_no=? AND a.doc_kind='CRITERIA' AND c.rule_extracted=0 LIMIT 1""",
        (notice,)).fetchone()
    if not failed:
        return False
    rows = db.execute("SELECT updated_by,verification_status FROM evaluation_rule WHERE notice_no=?",
                      (notice,)).fetchall()
    return not rows or any(tuple(row) == ("PARSER", "REVIEW") for row in rows)


def load_case(db, notice):
    sources = [dict(r) for r in db.execute("""SELECT a.id attachment_id, a.filename, a.doc_kind,
        a.sha256, c.read_status, c.text content, c.rule_extracted FROM bid_attachment a
        LEFT JOIN bid_document_content c ON c.attachment_id=a.id
        WHERE a.notice_no=? ORDER BY a.id""", (notice,))]
    if not sources:
        raise ValueError("No attachments for notice")
    criteria = [s for s in sources if s["doc_kind"] == "CRITERIA"]
    if not criteria or not any(s["rule_extracted"] == 0 for s in criteria):
        raise ValueError("Notice is not marked for evaluation LLM fallback")
    rows = [dict(r) for r in db.execute("SELECT * FROM evaluation_rule WHERE notice_no=? ORDER BY position", (notice,))]
    review_rows = [r for r in rows if r["updated_by"] == "PARSER" and r["verification_status"] == "REVIEW"]
    if not review_rows and any(r["updated_by"] != "PARSER" for r in rows):
        raise ValueError("Existing LLM or user edits require manual review")
    mode = "REVIEW" if review_rows else "FULL"
    if mode == "FULL" and rows:
        raise ValueError("Existing rows have no REVIEW item; inspect parser result")
    usable = [s for s in sources if s["read_status"] == "SUCCESS" and s["content"]]
    if not usable:
        raise ValueError("No readable source; reparse or OCR before LLM")
    if mode == "FULL" and not any(s["doc_kind"] == "CRITERIA" for s in usable):
        raise ValueError("No readable criteria document")
    return mode, sources, rows


def payload_for(notice, mode, sources, rows, model, max_output_tokens):
    if mode == "FULL":
        selected = [dict(attachment_id=s["attachment_id"], filename=s["filename"],
                         doc_kind=s["doc_kind"], content=s["content"]) for s in sources
                    if s["read_status"] == "SUCCESS" and s["content"]]
    else:
        selected = []
        for source in sources:
            if source["read_status"] != "SUCCESS" or not source["content"]:
                continue
            content = source["content"]
            spans = []
            for row in rows:
                if (row["updated_by"] != "PARSER" or row["verification_status"] != "REVIEW"
                        or row["attachment_id"] != source["attachment_id"]):
                    continue
                quote = row["source_quote"] or ""
                at = content.find(quote) if quote else -1
                if at >= 0:
                    spans.append(content[max(0, at - 2500):min(len(content), at + len(quote) + 2500)])
            if spans:
                selected.append(dict(attachment_id=source["attachment_id"], filename=source["filename"],
                                     doc_kind=source["doc_kind"], content="\n[...]\n".join(spans)))
        if not selected:
            raise ValueError("REVIEW source quote not found; inspect original before LLM")
    inputs = {"notice_no": notice, "mode": mode, "sources": selected,
              "existing_rows": [{key: r[key] for key in ("id", "position", "parent_id", "category",
                  "item_name", "rule_type", "max_score", "formula", "source_location",
                  "source_quote", "verification_status")} for r in rows],
              "review_rows": [r["id"] for r in rows
                              if r["updated_by"] == "PARSER" and r["verification_status"] == "REVIEW"]}
    return {"model": model, "store": False, "max_output_tokens": max_output_tokens,
            "instructions": PROMPT.read_text(encoding="utf-8"),
            "input": [{"role": "user", "content": json.dumps(inputs, ensure_ascii=False)}],
            "text": {"format": {"type": "json_schema", "name": "evaluation_rules",
                                "strict": True, "schema": SCHEMA}}}


def validate(result, mode, sources, existing):
    if not isinstance(result, dict) or set(result) != {"issues", "rows"}:
        raise ValueError("Invalid LLM result")
    if not isinstance(result["issues"], list) or any(not isinstance(x, str) for x in result["issues"]):
        raise ValueError("Invalid issues")
    if not isinstance(result["rows"], list):
        raise ValueError("Invalid rows")
    source_map = {s["attachment_id"]: s for s in sources}
    review_ids = {r["id"] for r in existing
                  if r["updated_by"] == "PARSER" and r["verification_status"] == "REVIEW"}
    seen = set()
    for position, row in enumerate(result["rows"], 1):
        if not isinstance(row, dict) or set(row) != set(ROW_FIELDS):
            raise ValueError("Invalid row fields")
        if row["category"] not in CATEGORIES or row["rule_type"] not in TYPES:
            raise ValueError("Unknown evaluation type")
        if not row["item_name"].strip() or not row["source_quote"].strip() or not row["source_location"].strip():
            raise ValueError("Name and source evidence required")
        source = source_map.get(row["attachment_id"])
        if not source or source["read_status"] != "SUCCESS" or row["source_quote"] not in source["content"]:
            raise ValueError("Source quote is not in the original document")
        if row["verification_status"] not in ("UNVERIFIED", "REVIEW"):
            raise ValueError("LLM cannot verify its own result")
        if row["rule_type"] == "FORMULA" and not row["formula"]:
            raise ValueError("FORMULA row needs a formula")
        if row["parameters"] is not None:
            if not isinstance(row["parameters"], str) or not isinstance(json.loads(row["parameters"]), dict):
                raise ValueError("parameters must be a JSON object string")
        if row["max_score"] is not None and (type(row["max_score"]) not in (int, float)):
            raise ValueError("Invalid max_score")
        if row["rounding_mode"] not in (None, "ROUND", "FLOOR", "CEIL"):
            raise ValueError("Invalid rounding mode")
        if mode == "REVIEW":
            if row["row_id"] not in review_ids or row["row_id"] in seen or row["parent_position"] is not None:
                raise ValueError("REVIEW may update each existing REVIEW row once")
            seen.add(row["row_id"])
        elif row["row_id"] is not None or (row["parent_position"] is not None and
                                           not 1 <= row["parent_position"] < position):
            raise ValueError("Invalid FULL row hierarchy")
    if mode == "REVIEW" and seen != review_ids:
        raise ValueError("LLM did not cover every REVIEW row")
    if mode == "FULL" and (not result["rows"] or result["issues"]):
        raise ValueError("Full extraction incomplete; draft cannot be saved")


def apply(db, notice, version, mode, result):
    db.execute("BEGIN IMMEDIATE")
    try:
        current_mode, sources, existing = load_case(db, notice)
        if current_mode != mode or source_version(sources, existing) != version:
            raise ValueError("Source or parser results changed during LLM call")
        validate(result, mode, sources, existing)
        now = datetime.now(timezone.utc).isoformat()
        if mode == "FULL":
            ids = []
            for position, row in enumerate(result["rows"], 1):
                parent = ids[row["parent_position"] - 1] if row["parent_position"] else None
                source = next(s for s in sources if s["attachment_id"] == row["attachment_id"])
                cur = db.execute("""INSERT INTO evaluation_rule
                    (notice_no,parent_id,position,category,item_name,rule_type,max_score,formula,
                     formula_source,parameters,input_key,rounding_digits,rounding_mode,attachment_id,
                     source_sha256,source_location,source_quote,verification_status,updated_by,extracted_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'REVIEW','LLM',?,?)""",
                    (notice,parent,position,row["category"],row["item_name"],row["rule_type"],
                     row["max_score"],row["formula"],"TEXT" if row["formula"] else None,
                     row["parameters"],
                     row["input_key"],row["rounding_digits"],row["rounding_mode"],row["attachment_id"],
                     source["sha256"],row["source_location"],row["source_quote"],now,now))
                ids.append(cur.lastrowid)
        else:
            for row in result["rows"]:
                source = next(s for s in sources if s["attachment_id"] == row["attachment_id"])
                db.execute("""UPDATE evaluation_rule SET category=?,item_name=?,rule_type=?,max_score=?,
                    formula=?,formula_source=?,parameters=?,input_key=?,rounding_digits=?,rounding_mode=?,
                    attachment_id=?,source_sha256=?,source_location=?,source_quote=?,
                    verification_status='REVIEW',updated_by='LLM',updated_at=? WHERE id=? AND notice_no=?""",
                    (row["category"],row["item_name"],row["rule_type"],row["max_score"],row["formula"],
                     "TEXT" if row["formula"] else None,
                     row["parameters"],
                     row["input_key"],row["rounding_digits"],row["rounding_mode"],row["attachment_id"],
                     source["sha256"],row["source_location"],row["source_quote"],now,row["row_id"],notice))
        db.commit()
    except Exception:
        db.rollback()
        raise


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--notice", required=True)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-6-luna"))
    parser.add_argument("--output", type=Path, default=Path(".local/llm-evaluation"))
    parser.add_argument("--max-chars", type=int, default=300000)
    parser.add_argument("--max-output-tokens", type=int, default=16000)
    parser.add_argument("--execute", action="store_true", help="Call paid API")
    parser.add_argument("--apply-draft", type=Path, help="Save an already generated JSON draft without another API call")
    args = parser.parse_args()
    if args.apply_draft and args.execute:
        parser.error("Use --execute or --apply-draft, not both")
    db = sqlite3.connect(f"file:{args.db}?mode={'rw' if args.apply_draft else 'ro'}", uri=True)
    db.row_factory = sqlite3.Row
    try:
        mode, sources, rows = load_case(db, args.notice)
        version = source_version(sources, rows)
        if args.apply_draft:
            draft = json.loads(args.apply_draft.read_text(encoding="utf-8"))
            if (draft.get("notice_no"), draft.get("mode"), draft.get("version")) != (args.notice, mode, version):
                raise ValueError("Draft does not match current notice, mode, and source version")
            apply(db, args.notice, version, mode, draft["result"])
            print(json.dumps({"notice_no": args.notice, "mode": mode, "rows": len(draft["result"]["rows"]),
                              "applied": True}, ensure_ascii=False))
            return
        payload = payload_for(args.notice, mode, sources, rows, args.model, args.max_output_tokens)
        chars = len(json.dumps(payload, ensure_ascii=False))
        if chars > args.max_chars:
            raise ValueError(f"Input exceeds --max-chars ({chars}); no text was truncated")
        if not args.execute:
            print(json.dumps({"notice_no": args.notice, "mode": mode, "input_chars": chars,
                              "review_rows": sum(r["verification_status"] == "REVIEW" for r in rows)},
                             ensure_ascii=False))
            return
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ValueError("Set OPENAI_API_KEY before --execute")
        response = call_openai(payload, key)
        result = parse_response(response)
        validate(result, mode, sources, rows)
        args.output.mkdir(parents=True, exist_ok=True)
        path = args.output / f"{args.notice}-{version[:12]}.json"
        path.write_text(json.dumps({"notice_no": args.notice, "mode": mode, "version": version,
                                    "result": result, "usage": response.get("usage")},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"notice_no": args.notice, "mode": mode, "rows": len(result["rows"]),
                          "issues": result["issues"], "applied": False, "review_file": str(path),
                          "usage": response.get("usage")}, ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
