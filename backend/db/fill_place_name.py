"""발주부서(bid_notice.place_name) 채우기: --srm-list 로 받은 공고는 공식 API 목록을 거치지 않아 비어 있다.

SRM 상세 기본정보(operatingUnitName + placeName)로 공식 API 와 같은 모양을 만든다 (collect.srm_place_name).
SRM 에 공고 1건당 1~2번 요청한다 (bid_id 가 있으면 1번). 비어 있는 공고만 건드린다.

기본은 미리보기(DB 변경 없음). --apply 를 붙이면 DB 파일을 백업한 뒤 반영한다.

    python -m backend.db.fill_place_name             # 미리보기
    python -m backend.db.fill_place_name --apply     # 반영 (bidmate.sqlite3.bak-YYYYmmddHHMMSS 생성)
"""
import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from backend.collector.clients import SrmClient
from backend.collector.collect import srm_place_name
from backend.db.init_db import DEFAULT_DB_PATH


def main():
    ap = argparse.ArgumentParser(description="SRM 으로 발주부서(place_name) 채우기")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--interval", type=float, default=3.0, help="SRM 요청 간격(초)")
    args = ap.parse_args()
    db = sqlite3.connect(args.db)
    targets = db.execute("""SELECT notice_no, bid_id FROM bid_notice WHERE place_name IS NULL OR place_name=''
                            ORDER BY notice_no""").fetchall()
    srm = SrmClient(interval=args.interval)
    found, missed = [], []
    for notice_no, bid_id in targets:
        try:
            bid_id = bid_id or (srm.find_bid(notice_no, days=400) or {}).get("id")
            name = srm_place_name(srm.basic_info(bid_id)) if bid_id else None
        except RuntimeError as exc:
            missed.append((notice_no, str(exc)[:80]))
            continue
        (found.append((name, notice_no)) if name else missed.append((notice_no, "SRM 에서 못 찾음")))
    for name, notice_no in found:
        print(f"  {notice_no} → {name}")
    for notice_no, why in missed:
        print(f"  {notice_no} 건너뜀: {why}")
    print(f"대상 {len(targets)}건 → 채움 {len(found)}건, 건너뜀 {len(missed)}건")
    if not args.apply or not found:
        print("미리보기만 했습니다. 반영하려면 --apply" if not args.apply else "반영할 것이 없습니다.")
        return
    backup = Path(f"{args.db}.bak-{datetime.now():%Y%m%d%H%M%S}")
    db.close()
    shutil.copy2(args.db, backup)
    db = sqlite3.connect(args.db)
    with db:
        db.executemany("UPDATE bid_notice SET place_name=? WHERE notice_no=? AND (place_name IS NULL OR place_name='')",
                       found)
    print(f"반영 {len(found)}건 (백업 {backup})")


if __name__ == "__main__":
    main()
