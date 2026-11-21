"""경비 오저장 정정: bid_notice.net_construction_cost(순공사원가)에 들어간 SRM overHeadCostAmount(=경비) 값을
overhead_cost_amount(경비)로 옮기고, 순공사원가는 NULL(미확인)로 되돌린다.

식별 기준
- 예전 수집기에서 net_construction_cost 에 값을 넣은 곳은 SRM overHeadCostAmount 하나뿐이었다.
  지금 수집기는 이 컬럼을 쓰지 않으므로 **net_construction_cost IS NOT NULL 인 행 = 정정 대상**이다.
- 근거 확인: 저장값이 공고문 원문(bid_document_content)에 '경비' 칸과 함께 나오는지(금액 앞 300자 안에 '경비'),
  overhead_cost_amount 가 이미 있으면 그 값과 같은지 함께 보여 준다.

정정 방법
- overhead_cost_amount 가 비어 있으면 저장값을 옮긴다(이미 있으면 새 수집값을 유지).
- net_construction_cost 는 NULL(미확인)로 바꾼다. 공고문 금액표에서 순공사원가를 읽어 채울지는
  표 파서의 수집기 통합과 함께 팀 결정으로 남긴다.

기본은 미리보기(DB 변경 없음). --apply 를 붙이면 DB 파일을 백업한 뒤 한 트랜잭션으로 반영한다.

    python -m backend.db.fix_overhead_cost                # 미리보기
    python -m backend.db.fix_overhead_cost --apply        # 반영 (bidmate.sqlite3.bak-YYYYmmddHHMMSS 생성)
"""
import argparse
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from backend.db.init_db import DEFAULT_DB_PATH, migrate

NEAR = 300  # 금액 앞에서 '경비' 글자를 찾는 범위(표 칸이 문단으로 읽혀 행·열 순서가 문서마다 다름)


def overhead_in_document(text, value):
    """저장값이 원문에 '경비' 칸과 함께 나오면 True, 금액만 있으면 '금액만', 없으면 False."""
    found = False
    for m in re.finditer(re.escape(f"{value:,}"), text or ""):
        found = True
        if re.search(r"경\s*비", text[max(0, m.start() - NEAR):m.start()]):
            return True
    return "금액만" if found else False


def plan(db):
    columns = {r[1] for r in db.execute("PRAGMA table_info(bid_notice)")}
    has_overhead = "overhead_cost_amount" in columns
    rows = db.execute(f"""
        SELECT notice_no, title, net_construction_cost,
               {"overhead_cost_amount" if has_overhead else "NULL"}
        FROM bid_notice WHERE net_construction_cost IS NOT NULL ORDER BY notice_no""").fetchall()
    result = []
    for notice_no, title, stored, overhead in rows:
        text = db.execute("""
            SELECT c.text FROM bid_attachment a JOIN bid_document_content c ON c.attachment_id = a.id
            WHERE a.notice_no = ? AND a.doc_kind = 'NOTICE' AND c.read_status = 'SUCCESS'
            ORDER BY a.id DESC LIMIT 1""", (notice_no,)).fetchone()
        hit = overhead_in_document(text[0], stored) if text else None
        evidence = {None: "공고문 원문 없음 → 저장 경로(overHeadCostAmount)만으로 판단",
                    True: "공고문 금액표 '경비' 칸에 같은 금액 있음",
                    "금액만": "공고문에 같은 금액은 있으나 '경비' 칸 확인 못함 → 저장 경로로 판단",
                    False: "공고문에서 같은 금액을 찾지 못함 → 저장 경로로 판단, 확인 권장"}[hit]
        result.append({
            "notice_no": notice_no, "title": title, "stored": stored, "overhead_now": overhead,
            "overhead_new": overhead if overhead is not None else stored,
            "evidence": evidence,
        })
    return has_overhead, result


def show(has_overhead, result):
    print(f"정정 대상: net_construction_cost IS NOT NULL → {len(result)}건"
          + ("" if has_overhead else " (overhead_cost_amount 컬럼 없음: --apply 시 마이그레이션으로 추가)"))
    for r in result:
        print(f"\n[{r['notice_no']}] {r['title']}")
        print(f"  지금        net_construction_cost={r['stored']:,}  overhead_cost_amount={r['overhead_now']}")
        print(f"  바꾼 뒤     net_construction_cost=NULL(미확인)  overhead_cost_amount={r['overhead_new']:,}")
        print(f"  근거        {r['evidence']}")
        if r["overhead_now"] is not None and r["overhead_now"] != r["stored"]:
            print("  주의        이미 저장된 경비와 다름 → 새 수집값 유지")


def apply(db_path, result):
    backup = Path(f"{db_path}.bak-{datetime.now():%Y%m%d%H%M%S}")
    shutil.copy2(db_path, backup)
    db = sqlite3.connect(db_path)
    try:
        migrate(db)
        with db:
            changed = db.execute("""
                UPDATE bid_notice
                SET overhead_cost_amount = COALESCE(overhead_cost_amount, net_construction_cost),
                    net_construction_cost = NULL
                WHERE net_construction_cost IS NOT NULL""").rowcount
        left = db.execute("SELECT COUNT(*) FROM bid_notice WHERE net_construction_cost IS NOT NULL").fetchone()[0]
    finally:
        db.close()
    print(f"\n반영: {changed}건 (예상 {len(result)}건), 남은 net_construction_cost 값 {left}건, 백업 {backup}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--apply", action="store_true", help="백업 후 DB 반영 (없으면 미리보기만)")
    args = parser.parse_args()
    db = sqlite3.connect(f"file:{Path(args.db).resolve().as_posix()}?mode=ro", uri=True)
    has_overhead, result = plan(db)
    db.close()
    show(has_overhead, result)
    if args.apply and result:
        apply(args.db, result)
    elif not args.apply:
        print("\n미리보기만 했다. 반영하려면 --apply")


if __name__ == "__main__":
    main()
