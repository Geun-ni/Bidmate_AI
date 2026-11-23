"""Local required-document candidates and evidence; no network or LLM calls."""

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


VERSION = "document-candidates-v1"
ITEM_FIELDS = (
    "notice_no", "stage", "submit_to", "position", "name", "requirement_type",
    "alternative_group", "condition_note", "validity_note", "source_attachment_id",
    "source_location", "verification_status",
)
# Broad recall-oriented candidates. A reviewer must confirm submission and stage.
DOCUMENT = re.compile(
    r"[가-힣A-Za-z0-9·ㆍ/]+(?:\s+[가-힣A-Za-z0-9·ㆍ/]+){0,4}"
    r"(?:증명서|확인서|신청서|등록증|등록수첩|인감계|등기부등본|평가서|"
    r"실적확인원|시공인증서|자격증|가입증명원|가입증명|통지서|명부|"
    r"협약서|계획서|증빙서류|확인서류|증빙자료|등록원부)"
)
SECTION = re.compile(r"^(?:제\d+조\s*\([^)]*\)|\d+[.)]\s*.{2,45}|[가-하][.)]\s*.{2,45})")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_csv(path, required):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(required).issubset(reader.fieldnames or []):
            raise ValueError(f"{path.name}: missing columns {required}")
        rows = list(reader)
    return rows


def candidates(text, kind):
    """Keep original phrases and section paths without inventing missing docs."""
    lines = text.splitlines()
    section = "문서 본문"
    for index, line in enumerate(lines):
        if SECTION.match(line):
            section = line[:120]
        if not DOCUMENT.search(line):
            continue
        context = "\n".join(lines[max(0, index - 2): index + 3])
        if "감독부서" in section or "인원장비" in section:
            stage = "SUPERVISOR"
        elif "참가자격" in section:
            stage = "ELIGIBILITY"
        elif kind == "CRITERIA" or "적격심사" in section:
            stage = "QUALIFICATION"
        else:
            stage = "BASIC"
        # Keep whole mixed/alternative statements together until human review.
        yield {
            "stage": stage,
            "submit_to": "SUPERVISOR_DEPT" if stage == "SUPERVISOR" else "CONTRACT_DEPT",
            "name": line.strip(),
            "requirement_type": "CONDITIONAL" if re.search(
                r"경우|필요시|필요 시|해당|선택|가점|또는|대체", context
            ) else "REQUIRED",
            "alternative_group": None,
            "condition_note": "후보 추출 결과. 제출 의무·단계·조건·택일 관계를 원문에서 검수하세요.",
            "validity_note": None,
            "source_location": section,
            "verification_status": "REVIEW",
            "source_quote": line,
            "context": context,
        }


def open_database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))
    return connection


def ingest(connection, folder):
    attachments = read_csv(folder / "bid_attachment.csv", ("id", "notice_no", "filename", "doc_kind"))
    contents = read_csv(folder / "bid_document_content.csv", ("attachment_id", "read_status", "text_1"))
    source_ids = [int(row["id"]) for row in attachments]
    content_ids = [int(row["attachment_id"]) for row in contents]
    if len(set(source_ids)) != len(source_ids) or len(set(content_ids)) != len(content_ids):
        raise ValueError("Duplicate attachment IDs in input")
    if set(content_ids) - set(source_ids):
        raise ValueError("Content references an unknown attachment")
    content_by_id = {int(row["attachment_id"]): row for row in contents}
    now = datetime.now(timezone.utc).isoformat()
    changed_notices = set()
    with connection:
        for attachment in sorted(attachments, key=lambda row: int(row["id"])):
            attachment_id = int(attachment["id"])
            notice = attachment["notice_no"]
            content = content_by_id.get(attachment_id, {})
            # CSV split columns are contiguous chunks, so no extra newline is added.
            text = "".join(content.get(f"text_{part}", "") or "" for part in (1, 2, 3))
            text_hash = digest(text)
            metadata = json.dumps({"attachment": attachment, "content": {
                key: value for key, value in content.items() if not key.startswith("text_")
            }}, ensure_ascii=False, sort_keys=True)
            old = connection.execute(
                "SELECT * FROM staging_source WHERE attachment_id=?", (attachment_id,)
            ).fetchone()
            if old and (old["notice_no"], old["filename"]) != (notice, attachment["filename"]):
                raise ValueError(f"Attachment ID {attachment_id} reused for another document; use a new staging DB")
            connection.execute("INSERT OR IGNORE INTO staging_notice VALUES (?)", (notice,))
            if old and old["metadata"] == metadata and old["text_sha256"] == text_hash:
                continue
            changed_notices.add(notice)
            connection.execute(
                """INSERT INTO staging_source VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(attachment_id) DO UPDATE SET
                source_sha256=excluded.source_sha256, text_sha256=excluded.text_sha256,
                read_status=excluded.read_status, content=excluded.content, metadata=excluded.metadata""",
                (attachment_id, notice, attachment["filename"], attachment["doc_kind"],
                 attachment.get("sha256"), text_hash, content.get("read_status", "MISSING"), text, metadata),
            )
        for notice in sorted(changed_notices):
            # Changed inputs invalidate previous review instead of silently reusing it.
            connection.execute(
                "DELETE FROM staging_evidence WHERE item_id IN (SELECT id FROM required_document_item WHERE notice_no=?)",
                (notice,),
            )
            connection.execute("DELETE FROM required_document_item WHERE notice_no=?", (notice,))
            positions = Counter()
            sources = connection.execute(
                "SELECT * FROM staging_source WHERE notice_no=? ORDER BY attachment_id", (notice,)
            ).fetchall()
            for source in sources:
                if source["read_status"] != "SUCCESS":
                    continue
                for item in candidates(source["content"], source["doc_kind"]):
                    positions[item["stage"]] += 1
                    item.update(notice_no=notice, position=positions[item["stage"]],
                                source_attachment_id=source["attachment_id"])
                    insert_item(connection, item, source)
            version = digest(json.dumps([
                [s["attachment_id"], s["source_sha256"], s["text_sha256"], s["read_status"]]
                for s in sources
            ]))
            connection.execute(
                """INSERT INTO notice_analysis_cache VALUES (?,?,?, ?,NULL)
                ON CONFLICT(notice_no) DO UPDATE SET
                document_version=excluded.document_version, analysis_status=excluded.analysis_status,
                analyzed_at=excluded.analyzed_at, invalidated_at=excluded.analyzed_at""",
                (notice, version, "PENDING", now),
            )
    return len(changed_notices)


def insert_item(connection, item, source, extractor_version=VERSION):
    columns = ",".join(ITEM_FIELDS)
    markers = ",".join("?" for _ in ITEM_FIELDS)
    cursor = connection.execute(
        f"INSERT INTO required_document_item ({columns}) VALUES ({markers})",
        [item.get(key) for key in ITEM_FIELDS],
    )
    connection.execute("INSERT INTO staging_evidence VALUES (?,?,?,?,?,?)", (
        cursor.lastrowid, item["source_quote"], item["context"], source["source_sha256"],
        source["text_sha256"], extractor_version,
    ))


def apply_review(connection, path):
    """Replace only listed notices using fully reviewed, evidence-checked JSONL."""
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    grouped = {}
    for record in records:
        notice = record["notice_no"]
        if notice in grouped:
            raise ValueError(f"Duplicate notice in review file: {notice}")
        sources = {row["attachment_id"]: row for row in connection.execute(
            "SELECT * FROM staging_source WHERE notice_no=?", (notice,)
        )}
        if not sources:
            raise ValueError(f"Unknown notice: {notice}")
        cache = connection.execute(
            "SELECT document_version FROM notice_analysis_cache WHERE notice_no=?", (notice,)
        ).fetchone()
        if record["document_version"] != cache["document_version"]:
            raise ValueError(f"Stale review: {notice}")
        if not record.get("reviewer") or record.get("review_complete") is not True:
            raise ValueError(f"Explicit complete review and reviewer required: {notice}")
        if record.get("issues"):
            raise ValueError(f"Resolve extraction issues before human approval: {notice}")
        if any(source["read_status"] != "SUCCESS" for source in sources.values()):
            raise ValueError(f"Missing/failed source: {notice}")
        grouped[notice] = (record, sources)
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        for notice, (record, sources) in grouped.items():
            current_version = connection.execute(
                "SELECT document_version FROM notice_analysis_cache WHERE notice_no=?", (notice,)
            ).fetchone()[0]
            if current_version != record["document_version"]:
                raise ValueError(f"Source changed before review save: {notice}")
            connection.execute(
                "DELETE FROM staging_evidence WHERE item_id IN (SELECT id FROM required_document_item WHERE notice_no=?)",
                (notice,),
            )
            connection.execute("DELETE FROM required_document_item WHERE notice_no=?", (notice,))
            positions = Counter()
            for item in record["items"]:
                source = sources[item["source_attachment_id"]]
                if not item.get("source_quote") or item["source_quote"] not in source["content"]:
                    raise ValueError(f"Quote does not occur in original: {notice}")
                if not item.get("source_location") or not item.get("name", "").strip():
                    raise ValueError(f"Name and structural source location required: {notice}")
                if not item.get("stage") or not item.get("submit_to") or not item.get("requirement_type"):
                    raise ValueError(f"Stage, department and requirement type required: {notice}")
                if item["stage"] == "SUPERVISOR" and item["submit_to"] != "SUPERVISOR_DEPT":
                    raise ValueError(f"Supervisor department mismatch: {notice}")
                positions[item["stage"]] += 1
                item.update(notice_no=notice, position=positions[item["stage"]],
                            verification_status="VERIFIED",
                            context=json.dumps({"reviewer": record["reviewer"],
                                                "quote": item["source_quote"]}, ensure_ascii=False))
                insert_item(connection, item, source,
                            "openai-documents-v1-reviewed" if record.get("model") else VERSION)
            connection.execute(
                "UPDATE notice_analysis_cache SET analysis_status='DONE', analyzed_at=? WHERE notice_no=?",
                (datetime.now(timezone.utc).isoformat(), notice),
            )
    return len(grouped)


def export(connection, folder):
    folder.mkdir(parents=True, exist_ok=True)
    notices = connection.execute("SELECT * FROM notice_analysis_cache ORDER BY notice_no").fetchall()
    with (folder / "llm-input.jsonl").open("w", encoding="utf-8") as stream:
        for notice in notices:
            sources = connection.execute(
                """SELECT attachment_id, filename, doc_kind, source_sha256, text_sha256,
                read_status, content FROM staging_source WHERE notice_no=? ORDER BY attachment_id""",
                (notice["notice_no"],),
            ).fetchall()
            stream.write(json.dumps({
                "notice_no": notice["notice_no"], "document_version": notice["document_version"],
                "sources": [dict(source) for source in sources],
            }, ensure_ascii=False) + "\n")
    review_path = folder / "review.jsonl"
    with review_path.open("w", encoding="utf-8") as stream:
        for notice in notices:
            rows = connection.execute(
                """SELECT r.*, e.source_quote, e.context, e.source_sha256, e.text_sha256
                FROM required_document_item r JOIN staging_evidence e ON e.item_id=r.id
                WHERE r.notice_no=? ORDER BY r.stage,r.position""", (notice["notice_no"],)
            ).fetchall()
            record = dict(notice)
            record.update(reviewer="", review_complete=False, items=[dict(row) for row in rows])
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    approved = connection.execute(
        """SELECT r.*, s.filename, s.source_sha256, s.text_sha256, e.source_quote
        FROM required_document_item r JOIN staging_source s ON s.attachment_id=r.source_attachment_id
        JOIN staging_evidence e ON e.item_id=r.id
        JOIN notice_analysis_cache c ON c.notice_no=r.notice_no
        WHERE r.verification_status='VERIFIED' AND c.analysis_status='DONE'
        ORDER BY r.notice_no,r.stage,r.position"""
    ).fetchall()
    fields = list(ITEM_FIELDS) + ["source_filename", "source_sha256", "text_sha256", "source_quote"]
    with (folder / "required_document_item.verified.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in approved:
            data = {field: row[field] for field in ITEM_FIELDS}
            data.update(source_filename=row["filename"], source_sha256=row["source_sha256"],
                        text_sha256=row["text_sha256"], source_quote=row["source_quote"])
            writer.writerow(data)
    counts = dict(connection.execute(
        "SELECT verification_status,count(*) FROM required_document_item GROUP BY verification_status"
    ))
    report = {
        "extractor_version": VERSION, "notices": len(notices),
        "sources": connection.execute("SELECT count(*) FROM staging_source").fetchone()[0],
        "source_status": dict(connection.execute("SELECT read_status,count(*) FROM staging_source GROUP BY read_status")),
        "missing_or_failed_sources": [dict(row) for row in connection.execute(
            "SELECT attachment_id,notice_no,filename,read_status FROM staging_source WHERE read_status!='SUCCESS'"
        )],
        "notice_candidate_counts": dict(connection.execute(
            """SELECT n.notice_no,count(r.id) FROM staging_notice n
            LEFT JOIN required_document_item r ON r.notice_no=n.notice_no
            GROUP BY n.notice_no ORDER BY n.notice_no"""
        )),
        "items": counts, "verified_export_rows": len(approved),
        "warning": "Rule candidates require source review; zero candidates does not mean no required documents.",
    }
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def migrate(connection, target_path, apply=False):
    """Map attachment IDs and insert reviewed rows into an existing SQLite DB."""
    rows = connection.execute(
        """SELECT r.*, s.filename, s.source_sha256, e.source_quote
        FROM required_document_item r JOIN staging_source s ON s.attachment_id=r.source_attachment_id
        JOIN staging_evidence e ON e.item_id=r.id
        JOIN notice_analysis_cache c ON c.notice_no=r.notice_no
        WHERE r.verification_status='VERIFIED' AND c.analysis_status='DONE'"""
    ).fetchall()
    target = sqlite3.connect(target_path.resolve().as_uri() + "?mode=rw", uri=True)
    target.row_factory = sqlite3.Row
    target.execute("PRAGMA foreign_keys=ON")
    inserted = skipped = 0
    try:
        target.execute("BEGIN IMMEDIATE")
        for row in rows:
            if not row["source_sha256"]:
                raise ValueError("Source file hash required for migration")
            matches = target.execute(
                "SELECT id FROM bid_attachment WHERE notice_no=? AND filename=? AND sha256=?",
                (row["notice_no"], row["filename"], row["source_sha256"]),
            ).fetchall()
            if len(matches) != 1:
                raise ValueError(f"Target attachment version missing or ambiguous: {row['notice_no']}")
            attachment_id = matches[0]["id"]
            content = target.execute(
                "SELECT text FROM bid_document_content WHERE attachment_id=? AND read_status='SUCCESS'",
                (attachment_id,),
            ).fetchone()
            if not content or row["source_quote"] not in (content["text"] or ""):
                raise ValueError(f"Target source quote not found: {row['notice_no']}")
            values = {field: row[field] for field in ITEM_FIELDS}
            values["source_attachment_id"] = attachment_id
            old = target.execute(
                "SELECT * FROM required_document_item WHERE notice_no=? AND stage=? AND position=?",
                (row["notice_no"], row["stage"], row["position"]),
            ).fetchone()
            if old:
                if any(old[field] != values[field] for field in ITEM_FIELDS):
                    raise ValueError(f"Existing target item differs; reconcile before migration: {row['notice_no']}")
                skipped += 1
                continue
            target.execute(
                f"INSERT INTO required_document_item ({','.join(ITEM_FIELDS)}) VALUES ({','.join('?' for _ in ITEM_FIELDS)})",
                list(values.values()),
            )
            inserted += 1
        if apply:
            target.commit()
        else:
            target.rollback()
    finally:
        target.close()
    return {"apply": apply, "inserted" if apply else "would_insert": inserted, "unchanged": skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(".local/document-staging.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest").add_argument("folder", type=Path)
    sub.add_parser("review").add_argument("jsonl", type=Path)
    sub.add_parser("export").add_argument("folder", type=Path)
    migration = sub.add_parser("migrate")
    migration.add_argument("target", type=Path)
    migration.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    connection = open_database(args.db)
    try:
        if args.command == "ingest":
            print(json.dumps({"changed_notices": ingest(connection, args.folder)}))
        elif args.command == "review":
            print(json.dumps({"reviewed_notices": apply_review(connection, args.jsonl)}))
        elif args.command == "export":
            print(json.dumps(export(connection, args.folder), ensure_ascii=False))
        else:
            if args.target.resolve() == args.db.resolve():
                raise ValueError("Target DB must differ from staging DB")
            print(json.dumps(migrate(connection, args.target, args.apply)))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
