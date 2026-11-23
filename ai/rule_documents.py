"""Extract reviewable qualification-document candidates from explicit clauses."""

import argparse
import csv
import json
import re
import sqlite3
from pathlib import Path


LIST_START = re.compile(r"제출서류는\s*다음|다음의\s*적격심사서류|제출서류\s*목록|다음\s*각\s*호의\s*서류")
LIST_ITEM = re.compile(r"^\s*\d+[.)]\s*(.+)")
LIST_END = re.compile(r"^\s*(?:제\d+조|[③-⑳]|\[붙임\])")
EXPLICIT = re.compile(r"※\s*제출서류\s*[:：]\s*(.+)")
FORMS = re.compile(r"[‘'“](시설보유\s*및\s*적용기술\s*현황|장비보유\s*현황)[’'”]")
GENERIC = re.compile(r"기타\s*별도\s*제출|별도\s*제출\s*요청|^\s*\d+[.)]\s*(?:시공경험|경영상태)\s*$")


def name_from_list(value):
    value = re.sub(r"^제\d+조의\s*규정에\s*의한\s*", "", value)
    value = re.sub(r"\s+\d+\s*(?:부|식)\b.*$", "", value)
    value = re.sub(r"\s*\([^)]*(?:서식|제출)[^)]*\).*", "", value)
    return value.strip(" .")


def extract_source(source):
    lines = source["content"].splitlines()
    remaining = 0
    list_context = ""
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if remaining and (not line or LIST_END.match(line)):
            remaining = 0
        if remaining:
            match = LIST_ITEM.match(line)
            if match and not GENERIC.search(line):
                name = name_from_list(match.group(1))
                if name and len(name) <= 180:
                    yield name, number, raw, list_context
            remaining -= 1
        if LIST_START.search(line) and not line.startswith("붙임") and not line.startswith("붙 임"):
            remaining = 25
            list_context = line
        explicit = EXPLICIT.search(line)
        if explicit and not GENERIC.search(explicit.group(1)):
            value = explicit.group(1)
            if re.search(r"신기술지정증서\s*,\s*특허증", value):
                for name in ("신기술지정증서", "특허증", "실용신안증", "등록원부"):
                    if name in value:
                        yield name, number, raw, "가점 평가항목 적용 시; 예시·조건 확인 필요"
            elif len(value) <= 180:
                yield value.strip(" ."), number, raw, "제출서류 명시; 복합 서류 분리 검토 필요"
        if "시설, 장비 등은" in line and "명시하여야" in line:
            for match in FORMS.finditer(line):
                yield match.group(1), number, raw, "시설·장비 평가대상인 경우"


def extract_notice(connection, notice):
    items = []
    seen = set()
    for source in connection.execute(
        "SELECT attachment_id,filename,content FROM staging_source "
        "WHERE notice_no=? AND doc_kind='CRITERIA' AND read_status='SUCCESS' ORDER BY attachment_id",
        (notice,),
    ):
        for name, line_number, quote, context in extract_source(source):
            key = re.sub(r"\s+", "", name)
            if key in seen:
                continue
            seen.add(key)
            condition = (quote if re.search(r"필요시|필요 시|가점|경우|예시|또는", quote)
                         else context if re.search(r"이미 제출|예시|가점", context) else None)
            items.append({"name": name, "stage": "QUALIFICATION", "submit_to": "CONTRACT_DEPT",
                          "requirement_type": "CONDITIONAL" if condition else "REQUIRED",
                          "alternative_group": None, "condition_note": condition, "validity_note": None,
                          "source_attachment_id": source["attachment_id"],
                          "source_location": f"{source['filename']}:{line_number}", "source_quote": quote,
                          "verification_status": "REVIEW"})
    for position, item in enumerate(items, 1):
        item["position"] = position
    return {"notice_no": notice, "rule_version": "explicit-criteria-v2",
            "verification_status": "REVIEW", "items": items}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(".local/document-staging.sqlite3"))
    parser.add_argument("--split-file", type=Path, default=Path("ai/splits/notices_20261121.csv"))
    parser.add_argument("--split", choices=("development", "validation", "test"), default="development")
    parser.add_argument("--output", type=Path, default=Path(".local/rule-documents.jsonl"))
    args = parser.parse_args()
    with args.split_file.open(encoding="utf-8-sig", newline="") as stream:
        notices = [row["notice_no"] for row in csv.DictReader(stream) if row["split"] == args.split]
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        records = [extract_notice(connection, notice) for notice in notices]
    finally:
        connection.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps({"notices": len(records), "items": sum(len(r["items"]) for r in records),
                      "zero_items": sum(not r["items"] for r in records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
