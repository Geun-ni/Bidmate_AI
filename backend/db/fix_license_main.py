"""주력분야(LICENSE_MAIN) 코드·이름 채우기: 예전 수집기는 SRM 응답 키를 잘못 읽어
bid_restriction_item 의 주력분야 행에 code·name 을 NULL 로 저장했다(실제 키 licenseMainCode/licenseMainCodeName).

정정 방법
- SRM 에 다시 요청하지 않고, 저장해 둔 원본(bid_restriction_snapshot.raw_response 의 items.LICENSE_MAIN)에서 읽는다.
- 공고별 DB 행 수와 원본 항목 수가 같을 때만, 같은 순서(id 순 = 저장 순서)로 code·name 을 채운다.
  수가 다르거나 원본에서 값을 못 읽으면 건너뛰고 사유를 보여 준다.

기본은 미리보기(DB 변경 없음). --apply 를 붙이면 DB 파일을 백업한 뒤 한 트랜잭션으로 반영한다.

    python -m backend.db.fix_license_main             # 미리보기
    python -m backend.db.fix_license_main --apply     # 반영 (bidmate.sqlite3.bak-YYYYmmddHHMMSS 생성)
"""
import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from backend.collector.collect import parse_restriction_item
from backend.db.init_db import DEFAULT_DB_PATH


def plan(db):
    notices = [r[0] for r in db.execute("""SELECT DISTINCT notice_no FROM bid_restriction_item
                                           WHERE category='LICENSE_MAIN' AND (code IS NULL OR name IS NULL)
                                           ORDER BY notice_no""")]
    updates, skipped = [], []
    for notice_no in notices:
        rows = db.execute("SELECT id, code, name FROM bid_restriction_item WHERE notice_no=? AND category='LICENSE_MAIN' "
                          "ORDER BY id", (notice_no,)).fetchall()
        raw = db.execute("SELECT raw_response FROM bid_restriction_snapshot WHERE notice_no=?", (notice_no,)).fetchone()
        items = (json.loads(raw[0]).get("items") or {}).get("LICENSE_MAIN") if raw and raw[0] else None
        if items is None:
            skipped.append((notice_no, "원본 응답 없음"))
            continue
        if len(items) != len(rows):
            skipped.append((notice_no, f"DB {len(rows)}행 / 원본 {len(items)}건으로 수가 다름"))
            continue
        for (row_id, code, name), item in zip(rows, items):
            new_code, new_name = parse_restriction_item("LICENSE_MAIN", item)[:2]
            if new_code is None or new_name is None:
                skipped.append((notice_no, f"원본에서 값을 못 읽음 (키: {sorted(item)})"))
            elif (code, name) != (new_code, new_name):
                updates.append({"id": row_id, "notice_no": notice_no, "old": (code, name), "new": (new_code, new_name),
                                "license": (item.get("licenseCode"), item.get("licenseCodeName"))})
    return updates, skipped


def show(updates, skipped):
    print(f"채울 행: {len(updates)}건, 건너뜀: {len(skipped)}건")
    for u in updates:
        print(f"  [{u['notice_no']}] id={u['id']}  {u['old']} → code={u['new'][0]}, name={u['new'][1]}"
              f"  (원본의 소속 면허 {u['license'][0]} {u['license'][1]})")
    for notice_no, why in skipped:
        print(f"  건너뜀 [{notice_no}] {why}")


def apply(db_path, updates):
    backup = Path(f"{db_path}.bak-{datetime.now():%Y%m%d%H%M%S}")
    shutil.copy2(db_path, backup)
    db = sqlite3.connect(db_path)
    try:
        with db:
            changed = sum(db.execute("UPDATE bid_restriction_item SET code=?, name=? WHERE id=? AND category='LICENSE_MAIN'",
                                     (*u["new"], u["id"])).rowcount for u in updates)
        left = db.execute("SELECT COUNT(*) FROM bid_restriction_item WHERE category='LICENSE_MAIN' "
                          "AND (code IS NULL OR name IS NULL)").fetchone()[0]
    finally:
        db.close()
    print(f"\n반영: {changed}건 (예상 {len(updates)}건), 남은 빈 주력분야 행 {left}건, 백업 {backup}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--apply", action="store_true", help="백업 후 DB 반영 (없으면 미리보기만)")
    args = parser.parse_args()
    db = sqlite3.connect(f"file:{Path(args.db).resolve().as_posix()}?mode=ro", uri=True)
    updates, skipped = plan(db)
    db.close()
    show(updates, skipped)
    if args.apply and updates:
        apply(args.db, updates)
    elif not args.apply:
        print("\n미리보기만 했다. 반영하려면 --apply")


if __name__ == "__main__":
    main()
