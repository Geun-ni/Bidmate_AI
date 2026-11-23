"""관심공고 (별표, FUNC-016) — 로그인 사용자 개인별 favorite_notice.

달력에 관심공고 일정만 보이려면 GET /api/schedule?favorites=1 (backend/schedule.py list_events 의 user_id).
마감된 공고도 목록에 남기고 is_open·마감 여부로 구분한다 (마감 공고를 투찰 가능으로 오인하지 않게).
"""
from datetime import datetime

from backend.notices_api import LIST_COLUMNS


def _now():
    return datetime.now().isoformat(timespec="seconds")


def list_favorites(db, user):
    cols = ", ".join(f"n.{c}" for c in LIST_COLUMNS)
    rows = db.execute(f"""SELECT {cols}, n.is_open, f.created_at AS favorited_at
                          FROM favorite_notice f JOIN bid_notice n ON n.notice_no = f.notice_no
                          WHERE f.user_id=? ORDER BY n.attend_req_close_at IS NULL, n.attend_req_close_at""",
                       (user["user_id"],))
    now = _now()
    out = []
    for r in rows:
        item = dict(r)
        item["closed"] = not r["is_open"] or bool(r["attend_req_close_at"] and r["attend_req_close_at"] < now)
        out.append(item)
    return {"favorites": out, "count": len(out)}


def add(db, user, notice_no):
    """→ 목록 (없는 공고면 None → 404). 이미 있으면 그대로"""
    if not db.execute("SELECT 1 FROM bid_notice WHERE notice_no=?", (notice_no,)).fetchone():
        return None
    with db:
        db.execute("INSERT OR IGNORE INTO favorite_notice (user_id, notice_no, created_at) VALUES (?,?,?)",
                   (user["user_id"], notice_no, _now()))
    return list_favorites(db, user)


def remove(db, user, notice_no):
    with db:
        db.execute("DELETE FROM favorite_notice WHERE user_id=? AND notice_no=?", (user["user_id"], notice_no))
    return list_favorites(db, user)
