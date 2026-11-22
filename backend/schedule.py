"""공고 일정 저장 (notice_schedule_event, FUNC-016 달력).

- 신청 시작·마감, 투찰 시작·마감: 공식 API 값 (source='OPEN_API')
- 개찰: SRM 상세 개찰일시 (source='SRM')
- 적격심사서류 제출기한: 기준서에 '통보서 접수일로부터 N일 이상 지정'처럼 날짜가 없으므로
  날짜를 추정하지 않고 event_at=NULL, confirmed=0, note 에 원문 문구만 남긴다 (source='DOCUMENT').
- 정정 공고로 날짜가 바뀌면 다시 돌릴 때 덮어쓴다. 수집기가 공고 1건을 처리할 때마다 그 공고를 갱신한다.

    python -m backend.schedule            # 미리보기 (DB 변경 없음)
    python -m backend.schedule --apply    # 수집 범위 공고 전체 갱신
"""
import argparse
import re
import sqlite3

from backend.db.init_db import DEFAULT_DB_PATH

API_EVENTS = (("ATTEND_REQ_BEGIN", "attend_req_begin_at"), ("ATTEND_REQ_CLOSE", "attend_req_close_at"),
              ("BID_BEGIN", "bid_begin_at"), ("BID_END", "bid_end_at"))
DOC_DUE = re.compile(r"[^.\n]{0,80}통보[^.\n]{0,20}(?:접수일|받은\s*날)[^.\n]{0,40}\d+\s*일[^.\n]{0,40}")


def normalize(value):
    """'2026-11-26T11:00:00.000+09:00' → '2026-11-26 11:00:00' (KST 문자열로 통일)"""
    if not value:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}))?", str(value))
    return f"{m.group(1)} {m.group(2)}:{m.group(3) or '00'}" if m else None


def events_for(db, notice_no):
    cur = db.execute("SELECT * FROM bid_notice WHERE notice_no=?", (notice_no,))
    values = cur.fetchone()
    if values is None:
        return []
    row = dict(zip([d[0] for d in cur.description], values))  # 수집기 연결(tuple 행)에서도 쓰도록
    cols = row.keys()
    out = [(kind, normalize(row[col]), "OPEN_API", 1, None) for kind, col in API_EVENTS if col in cols and row[col]]
    if "open_bid_at" in cols and row["open_bid_at"]:
        out.append(("OPEN_BID", normalize(row["open_bid_at"]), "SRM", 1, None))
    for (text,) in db.execute("""SELECT c.text FROM bid_attachment a JOIN bid_document_content c ON c.attachment_id=a.id
                                 WHERE a.notice_no=? AND a.doc_kind='CRITERIA' AND c.read_status='SUCCESS'""",
                              (notice_no,)):
        m = DOC_DUE.search(text or "")
        if m:
            out.append(("QUALIFICATION_DOC_DUE", None, "DOCUMENT", 0, re.sub(r"\s+", " ", m.group(0)).strip()[:300]))
            break
    return [e for e in out if e[1] or e[0] == "QUALIFICATION_DOC_DUE"]


def refresh(db, notice_no):
    """공고 1건 일정을 다시 넣는다 (커밋은 호출자) → 넣은 건수"""
    events = events_for(db, notice_no)
    db.execute("DELETE FROM notice_schedule_event WHERE notice_no=?", (notice_no,))
    db.executemany("""INSERT INTO notice_schedule_event (notice_no, event_type, event_at, source, confirmed, note)
                      VALUES (?,?,?,?,?,?)""", [(notice_no, *e) for e in events])
    return len(events)


def list_events(db, date_from=None, date_to=None, notice_no=None):
    """달력용: 기간(YYYY-MM-DD) 안 일정 + 날짜 없는 제출기한(공고를 지정한 경우)"""
    where, params = ["1=1"], []
    if notice_no:
        where.append("e.notice_no=?")
        params.append(notice_no)
    if date_from:
        where.append("(e.event_at IS NULL OR e.event_at >= ?)")
        params.append(date_from)
    if date_to:
        where.append("(e.event_at IS NULL OR e.event_at < date(?, '+1 day'))")
        params.append(date_to)
    if not notice_no:
        where.append("e.event_at IS NOT NULL")
    rows = db.execute(f"""SELECT e.notice_no, n.title, e.event_type, e.event_at, e.source, e.confirmed, e.note
                          FROM notice_schedule_event e JOIN bid_notice n ON n.notice_no = e.notice_no
                          WHERE {' AND '.join(where)} ORDER BY e.event_at IS NULL, e.event_at, e.notice_no""", params)
    return {"events": [dict(r) for r in rows]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    nos = [r[0] for r in db.execute("""SELECT notice_no FROM bid_notice WHERE company_id_src='COM01'
                                       AND purchase_type='ConstructionService' AND bid_type='QualifiedEval'""")]
    counts, due = 0, 0
    for no in nos:
        ev = events_for(db, no)
        counts += len(ev)
        due += any(e[0] == "QUALIFICATION_DOC_DUE" for e in ev)
    print(f"공고 {len(nos)}건, 일정 {counts}개 (제출기한 문구 있는 공고 {due}건)")
    if args.apply:
        with db:
            for no in nos:
                refresh(db, no)
        print("반영함")


if __name__ == "__main__":
    main()
