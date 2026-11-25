"""Collect explicit submission evidence from notice/criteria text, without an API."""

import argparse
import json
import re
import sqlite3
from pathlib import Path


EXPLICIT = re.compile(r"제출서류\s*[:：]|제출서류\s*목록|제출서류는\s*다음|제출하여야\s*한다|제출하여야\s*합니다|첨부하여야\s*한다")
EMPTY = re.compile(r"제출서류\s*[:：]\s*해당\s*사항\s*없음")
EXAMPLE = re.compile(r"제출서류\s*\(\s*예시\s*\)")
HEADING = re.compile(r"^(?:제\d+조|\[부록|\d+[.)]\s|[가-하][.)]\s)")


def evidence_lines(text):
    section = "본문"
    example_section = False
    list_remaining = 0
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if list_remaining and (re.match(r"^(?:제\d+조|[③-⑳]|\[붙임\])", stripped) or not stripped):
            list_remaining = 0
        if HEADING.match(stripped):
            section = stripped[:120]
            if stripped.startswith("[부록"):
                example_section = bool(EXAMPLE.search(stripped))
        if list_remaining and re.match(r"^(?:\d+[.)]|[-○])\s*\S", stripped):
            yield {"line": line_number, "section": section, "quote": stripped,
                   "kind": "LIST_ITEM", "review_reason":
                   "예시 부록: 적용 여부 확인" if example_section else "상위 제출 조항의 목록: 서류명·조건 확인"}
        if list_remaining:
            list_remaining -= 1
        if EMPTY.search(stripped):
            yield {"line": line_number, "section": section, "quote": stripped,
                   "kind": "NO_SCANNED_ATTACHMENT", "review_reason": "전자 입력서류와 구분 필요"}
        elif EXPLICIT.search(stripped) and len(stripped) > 15:
            yield {"line": line_number, "section": section, "quote": stripped,
                   "kind": "EXPLICIT_SUBMISSION", "review_reason":
                   "예시 부록: 적용 여부 확인" if example_section else "서류명·조건 분리 필요"}
            if not stripped.startswith("붙임") and re.search(r"제출서류\s*(?:목록|는\s*다음)|다음의\s*적격심사서류", stripped):
                list_remaining = 25


def build_report(connection, notices):
    result = []
    for notice in notices:
        source_rows = connection.execute(
            "SELECT attachment_id,filename,doc_kind,content FROM staging_source "
            "WHERE notice_no=? AND read_status='SUCCESS' ORDER BY attachment_id", (notice,)
        ).fetchall()
        old_quotes = [row[0] for row in connection.execute(
            "SELECT e.source_quote FROM required_document_item r "
            "JOIN staging_evidence e ON e.item_id=r.id WHERE r.notice_no=?", (notice,)
        )]
        evidence = []
        for source in source_rows:
            for entry in evidence_lines(source["content"]):
                entry.update(attachment_id=source["attachment_id"], filename=source["filename"],
                             doc_kind=source["doc_kind"],
                             in_old_candidates=entry["quote"] in old_quotes)
                evidence.append(entry)
        result.append({"notice_no": notice, "sources": len(source_rows), "evidence": evidence,
                       "explicit_count": sum(x["kind"] == "EXPLICIT_SUBMISSION" for x in evidence),
                       "list_items": sum(x["kind"] == "LIST_ITEM" for x in evidence),
                       "old_rule_missed": sum(x["kind"] in ("EXPLICIT_SUBMISSION", "LIST_ITEM") and not x["in_old_candidates"] for x in evidence)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(".local/document-staging.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path(".local/rule-evidence-10.json"))
    parser.add_argument("notices", nargs="+")
    args = parser.parse_args()
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        report = build_report(connection, args.notices)
    finally:
        connection.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in report:
        print(row["notice_no"], "explicit", row["explicit_count"], "list", row["list_items"],
              "old_rule_missed", row["old_rule_missed"])


if __name__ == "__main__":
    main()
