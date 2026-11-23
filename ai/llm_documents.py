"""Extract full notices using OpenAI Structured Outputs, then validate and stage."""

import argparse
import json
import os
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai.document_staging import digest, insert_item, open_database
from ai.env import load_env


DEFAULT_MODEL = "gpt-6-luna"
PROMPT_PATH = Path(__file__).with_name("required_documents_prompt.txt")
STAGES = ["BASIC", "ELIGIBILITY", "QUALIFICATION", "SUPERVISOR"]
DEPARTMENTS = ["CONTRACT_DEPT", "SUPERVISOR_DEPT"]
REQUIREMENTS = ["REQUIRED", "CONDITIONAL", "OPTIONAL"]
ITEM_PROPERTIES = {
    "stage": {"type": "string", "enum": STAGES},
    "submit_to": {"type": "string", "enum": DEPARTMENTS},
    "name": {"type": "string"},
    "requirement_type": {"type": "string", "enum": REQUIREMENTS},
    "alternative_group": {"type": ["string", "null"]},
    "condition_note": {"type": ["string", "null"]},
    "validity_note": {"type": ["string", "null"]},
    "source_attachment_id": {"type": "integer"},
    "source_location": {"type": "string"},
    "source_quote": {"type": "string"},
}
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["covered_attachment_ids", "issues", "items"],
    "properties": {
        "covered_attachment_ids": {"type": "array", "items": {"type": "integer"}},
        "issues": {"type": "array", "items": {"type": "string"}},
        "items": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False,
                      "required": list(ITEM_PROPERTIES), "properties": ITEM_PROPERTIES},
        },
    },
}


def load_notice(connection, notice):
    cache = connection.execute(
        "SELECT * FROM notice_analysis_cache WHERE notice_no=?", (notice,)
    ).fetchone()
    sources = [dict(row) for row in connection.execute(
        "SELECT * FROM staging_source WHERE notice_no=? ORDER BY attachment_id", (notice,)
    )]
    if not cache or not sources:
        raise ValueError(f"Unknown notice: {notice}")
    if any(s["read_status"] != "SUCCESS" or not s["content"].strip() for s in sources):
        raise ValueError(f"Missing/empty source content: {notice}")
    if not {"NOTICE", "CRITERIA"}.issubset({s["doc_kind"] for s in sources}):
        raise ValueError(f"Both NOTICE and CRITERIA documents are needed: {notice}")
    return dict(cache), sources


def request_payload(notice, sources, model, max_output_tokens):
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    # All text is provided; regex candidates are intentionally excluded.
    inputs = {
        "notice_no": notice,
        "sources": [{key: s[key] for key in
                     ("attachment_id", "filename", "doc_kind", "content")} for s in sources],
    }
    return {
        "model": model, "store": False, "max_output_tokens": max_output_tokens,
        "instructions": prompt + "\n응답 구조는 지정된 JSON Schema를 따른다. "
        "covered_attachment_ids에 실제 검토한 모든 첨부 ID를 기록한다. "
        "해석이 불확실하거나 원문이 훼손된 부분은 issues에 기록한다. "
        "각 서류는 한 항목으로 분리하고 적용 조건을 보존한다.",
        "input": [{"role": "user", "content": json.dumps(inputs, ensure_ascii=False)}],
        "text": {"format": {"type": "json_schema", "name": "required_documents",
                            "strict": True, "schema": OUTPUT_SCHEMA}},
    }


def call_openai(payload, api_key, attempts=3):
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=180) as response:
                return json.load(response)
        except HTTPError as error:
            # Avoid printing remote response bodies or credentials.
            if error.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise RuntimeError(f"OpenAI HTTP {error.code}") from None
            time.sleep(2 ** attempt)
        except (URLError, TimeoutError):
            # Ambiguous network failures may have been billed; do not auto-repeat.
            raise RuntimeError("OpenAI connection failed; inspect before retrying") from None


def parse_response(response):
    if response.get("status") != "completed":
        raise ValueError("Incomplete/failed response; result was not staged")
    texts = []
    for output in response.get("output", []):
        for part in output.get("content", []):
            if part.get("type") == "refusal":
                raise ValueError("Model refused extraction; result was not staged")
            if part.get("type") == "output_text":
                texts.append(part["text"])
    if not texts:
        raise ValueError("Response has no structured output")
    return json.loads("".join(texts))


def call_ollama(payload, api_key=""):
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    request = Request(
        base_url + "/api/chat",
        data=json.dumps({
            "model": payload["model"], "stream": False, "think": False,
            "messages": [
                {"role": "system", "content": payload["instructions"]},
                *payload["input"],
            ],
            "format": payload["text"]["format"]["schema"],
            "options": {"temperature": 0, "num_ctx": 65536,
                        "num_predict": payload["max_output_tokens"]},
        }, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=1800) as response:
            native = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"Ollama HTTP {error.code}") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Ollama connection failed") from None
    return {
        "provider": "ollama", "model": native.get("model"),
        "status": "completed" if native.get("done") and native.get("done_reason") == "stop" else "incomplete",
        "output": [{"content": [{"type": "output_text",
                                "text": native.get("message", {}).get("content", "")}]}],
        "usage": {"input_tokens": native.get("prompt_eval_count"),
                  "output_tokens": native.get("eval_count"),
                  "total_duration_ns": native.get("total_duration")},
        "native_response": native,
    }


def normalize_name(name):
    return re.sub(r"[\s·ㆍ]", "", name).casefold()


def validate_output(result, sources):
    if not isinstance(result, dict) or set(result) != set(OUTPUT_SCHEMA["required"]):
        raise ValueError("Invalid extraction object")
    covered = result["covered_attachment_ids"]
    if not isinstance(covered, list) or any(type(i) is not int for i in covered):
        raise ValueError("Invalid covered attachment IDs")
    source_map = {s["attachment_id"]: s for s in sources}
    if len(covered) != len(set(covered)) or set(covered) != set(source_map):
        raise ValueError("Not all source documents were covered")
    if not isinstance(result["issues"], list) or any(
        not isinstance(issue, str) for issue in result["issues"]
    ):
        raise ValueError("Invalid issues")
    if not isinstance(result["items"], list):
        raise ValueError("items must be an array")
    seen = set()
    for item in result["items"]:
        if not isinstance(item, dict) or set(item) != set(ITEM_PROPERTIES):
            raise ValueError("Invalid item fields")
        for field in ("name", "source_location", "source_quote"):
            if not isinstance(item[field], str) or not item[field].strip():
                raise ValueError(f"Missing {field}")
        if len(item["name"]) > 200 or "\n" in item["name"]:
            raise ValueError("Document name must be a concise single item")
        if item["stage"] not in STAGES or item["submit_to"] not in DEPARTMENTS:
            raise ValueError("Invalid stage or department")
        if item["requirement_type"] not in REQUIREMENTS:
            raise ValueError("Invalid requirement type")
        for field in ("alternative_group", "condition_note", "validity_note"):
            if item[field] is not None and (
                not isinstance(item[field], str) or not item[field].strip()
            ):
                raise ValueError(f"Invalid {field}")
        if item["requirement_type"] == "CONDITIONAL" and not item["condition_note"]:
            raise ValueError("Conditional documents need a condition")
        if (item["stage"] == "SUPERVISOR") != (item["submit_to"] == "SUPERVISOR_DEPT"):
            raise ValueError("Supervisor stage/department mismatch")
        attachment = item["source_attachment_id"]
        if type(attachment) is not int or attachment not in source_map:
            raise ValueError("Unknown source attachment")
        if item["source_quote"] not in source_map[attachment]["content"]:
            raise ValueError("Source quote does not occur in original")
        key = (item["stage"], normalize_name(item["name"]), item["condition_note"],
               item["validity_note"], item["alternative_group"])
        if key in seen:
            raise ValueError("Duplicate document item")
        seen.add(key)
    groups = Counter(item["alternative_group"] for item in result["items"]
                     if item["alternative_group"])
    if any(count < 2 for count in groups.values()):
        raise ValueError("An alternative group needs at least two items")


def stage_output(connection, notice, version, sources, result, provenance):
    validate_output(result, sources)
    source_map = {s["attachment_id"]: s for s in sources}
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        current, current_sources = load_notice(connection, notice)
        if current["document_version"] != version or current_sources != sources:
            raise ValueError("Source changed during extraction")
        if current["analysis_status"] == "DONE":
            raise ValueError("Human-reviewed notice is protected")
        connection.execute(
            "DELETE FROM staging_evidence WHERE item_id IN (SELECT id FROM required_document_item WHERE notice_no=?)",
            (notice,),
        )
        connection.execute("DELETE FROM required_document_item WHERE notice_no=?", (notice,))
        positions = Counter()
        for extracted in result["items"]:
            item = dict(extracted)
            positions[item["stage"]] += 1
            item.update(notice_no=notice, position=positions[item["stage"]],
                        verification_status="REVIEW",
                        context=json.dumps(provenance, ensure_ascii=False))
            extractor = provenance.get("provider", "openai") + "-documents-v1"
            insert_item(connection, item, source_map[item["source_attachment_id"]], extractor)
        # Model extraction and exact-quote checks still require semantic review.
        connection.execute(
            "UPDATE notice_analysis_cache SET analysis_status='PENDING' WHERE notice_no=?", (notice,)
        )


def extract(connection, notice, folder, model, max_chars, max_output_tokens,
            execute=False, caller=None, provider="openai"):
    if provider not in ("openai", "ollama"):
        raise ValueError("Unknown LLM provider")
    if caller is None:
        caller = call_ollama if provider == "ollama" else call_openai
    cache, sources = load_notice(connection, notice)
    if cache["analysis_status"] == "DONE":
        return {"notice_no": notice, "status": "already_reviewed"}
    payload = request_payload(notice, sources, model, max_output_tokens)
    input_chars = len(json.dumps(payload, ensure_ascii=False))
    if input_chars > max_chars:
        raise ValueError(f"Input exceeds --max-chars ({input_chars}); no text was truncated")
    cache_input = payload if provider == "openai" else {"provider": provider, "payload": payload}
    key = digest(json.dumps(cache_input, ensure_ascii=False, sort_keys=True))
    folder.mkdir(parents=True, exist_ok=True)
    raw_path = folder / f"{key}.response.json"
    if not execute:
        return {"notice_no": notice, "status": "dry_run", "input_chars": input_chars,
                "cached_response": raw_path.exists(), "model": model}
    if raw_path.exists():
        response = json.loads(raw_path.read_text(encoding="utf-8"))
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if provider == "openai" and not api_key:
            raise ValueError("Set OPENAI_API_KEY before --execute")
        response = caller(payload, api_key)
        temporary = raw_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
        temporary.replace(raw_path)
    result = parse_response(response)
    validate_output(result, sources)
    record = {
        "notice_no": notice, "document_version": cache["document_version"],
        "reviewer": "", "review_complete": False, "items": result["items"],
        "issues": result["issues"], "provider": provider, "model": model, "response_id": response.get("id"),
        "usage": response.get("usage"), "request_hash": key,
    }
    # Save a usable reviewer handoff even when there are zero extracted items.
    review_path = folder / f"{key}.review.jsonl"
    review_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    stage_output(connection, notice, cache["document_version"], sources, result, {
        "provider": provider, "model": model, "response_id": response.get("id"), "request_hash": key,
        "issues": result["issues"], "review_file": str(review_path),
    })
    return {"notice_no": notice, "status": "needs_review", "items": len(result["items"]),
            "issues": result["issues"], "review_file": str(review_path), "usage": response.get("usage")}


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(".local/document-staging.sqlite3"))
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--notice")
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--provider", choices=("openai", "ollama"),
                        default=os.environ.get("LLM_PROVIDER", "openai"))
    parser.add_argument("--model")
    parser.add_argument("--output", type=Path, default=Path(".local/llm-documents"))
    parser.add_argument("--max-chars", type=int, default=300000)
    parser.add_argument("--max-output-tokens", type=int, default=16000)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.model is None:
        args.model = (os.environ.get("OLLAMA_MODEL", "qwen3.5:4b") if args.provider == "ollama"
                      else os.environ.get("OPENAI_MODEL", DEFAULT_MODEL))
    if args.max_chars <= 0 or args.max_output_tokens <= 0:
        parser.error("Limits must be positive")
    connection = open_database(args.db)
    failures = 0
    try:
        notices = [args.notice] if args.notice else [
            row[0] for row in connection.execute("SELECT notice_no FROM staging_notice ORDER BY notice_no")
        ]
        for notice in notices:
            try:
                result = extract(connection, notice, args.output, args.model,
                                 args.max_chars, args.max_output_tokens, args.execute,
                                 provider=args.provider)
            except (ValueError, RuntimeError, sqlite3.Error, OSError) as error:
                failures += 1
                result = {"notice_no": notice, "status": "failed", "error": str(error)}
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        connection.close()
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
