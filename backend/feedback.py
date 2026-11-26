"""베타 의견 저장 (POST /api/feedback, UI-10) — 형식: docs/design/frontend-requirements.md 11장.

세 종류(kind)를 beta_feedback 한 테이블에 쌓는다. 로그인 없이도 보낼 수 있고, 로그인했으면 세션 사용자로 기록한다.
조회·처리는 관리자(.env ADMIN_EMAIL)만: GET /api/admin/feedback (목록), GET /api/admin/feedback/export (CSV),
GET /api/admin/feedback/<id> (1건 + 첨부 이미지), PUT /api/admin/feedback/<id> {"status"} (처리 상태).
"""
import base64
import binascii
import csv
import io
import json
import re
from datetime import datetime

CATEGORIES = {"BUG", "INCONVENIENT", "IDEA", "GOOD"}
TARGETS = {"judgment", "score", "qna"}
MAX_MESSAGE = 2000
MAX_COMMENT = 500
MAX_JSON = 20_000           # answers·extra·context 각각 (JSON 문자열 길이)
MAX_IMAGE = 3 * 1024 * 1024  # 화면과 같은 원본 3MB
DATA_URL = re.compile(r"data:(image/[\w.+-]+);base64,(.*)", re.S)


def _text(value, limit, name, required=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"{name}을(를) 입력하세요.")
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{name}은(는) {limit:,}자 이하 문자열이어야 합니다.")
    return value.strip()


def _json(value, name):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{name} 형식을 확인하세요.")
    text = json.dumps(value, ensure_ascii=False)
    if len(text) > MAX_JSON:
        raise ValueError(f"{name}이(가) 너무 깁니다.")
    return text


def _attachment(att):
    """{name, type, size, data: 'data:image/png;base64,…'} → (이름, 형식, 바이트)"""
    if att is None:
        return None, None, None
    m = DATA_URL.fullmatch(att.get("data") or "") if isinstance(att, dict) else None
    if not m:
        raise ValueError("첨부는 이미지 파일만 가능합니다.")
    try:
        data = base64.b64decode(m.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("첨부 이미지를 읽지 못했습니다.") from None
    if len(data) > MAX_IMAGE:
        raise ValueError("이미지는 3MB까지 첨부할 수 있습니다.")
    return _text(att.get("name"), 200, "첨부 이름") or "image", m.group(1), data


def save(db, body, user=None):
    """→ {"ok": True, "id": n}. 형식 오류는 ValueError (서버가 400)"""
    if not isinstance(body, dict) or body.get("kind") not in ("general", "reaction", "survey"):
        raise ValueError("kind 는 general·reaction·survey 중 하나여야 합니다.")
    kind, context = body["kind"], body.get("context")
    row = {"kind": kind, "user_id": user["user_id"] if user else None, "context": _json(context, "context"),
           "notice_no": _text(context.get("notice_no"), 40, "공고번호") if isinstance(context, dict) else None,
           "created_at": datetime.now().isoformat(timespec="seconds")}
    if kind == "general":
        if body.get("category") not in CATEGORIES:
            raise ValueError("의견 유형을 선택하세요.")
        name, mime, data = _attachment(body.get("attachment"))
        row.update(category=body["category"], message=_text(body.get("message"), MAX_MESSAGE, "내용", required=True),
                   email=_text(body.get("email"), 200, "이메일"),
                   attachment_name=name, attachment_type=mime, attachment_data=data)
    elif kind == "reaction":
        if body.get("target") not in TARGETS or body.get("value") not in ("up", "down"):
            raise ValueError("target·value 를 확인하세요.")
        row.update(target=body["target"], value=body["value"],
                   ref_id=_text(str(body["ref_id"]) if body.get("ref_id") is not None else None, 200, "ref_id"),
                   message=_text(body.get("comment"), MAX_COMMENT, "이유"), payload=_json(body.get("extra"), "extra"))
    else:
        answers = body.get("answers")
        nps = answers.get("nps") if isinstance(answers, dict) else None
        if not isinstance(nps, int) or isinstance(nps, bool) or not 0 <= nps <= 10:
            raise ValueError("추천 의향(0~10점)에 답해 주세요.")
        row["payload"] = _json(answers, "answers")
    cols = ", ".join(row)
    with db:
        cur = db.execute(f"INSERT INTO beta_feedback ({cols}) VALUES ({', '.join('?' * len(row))})",
                         tuple(row.values()))
    return {"ok": True, "id": cur.lastrowid}


# ------------------------------------------------------------------ 관리자 조회 (GET /api/admin/feedback…)
LIST_FILTERS = ("kind", "category", "target", "value", "status")
STATUSES = {"NEW", "CHECKED", "DONE"}  # 미확인 / 확인함 / 반영함


def _require_admin(user):
    if not user or not user.get("is_admin"):
        raise PermissionError("관리자만 볼 수 있습니다.")


def _item(row):
    item = {k: row[k] for k in row.keys() if k != "attachment_data"}
    for k in ("payload", "context"):
        item[k] = json.loads(item[k]) if item[k] else None
    item["has_attachment"] = row["attachment_data"] is not None
    return item


def list_feedback(db, user, filters=None, limit=50, offset=0):
    """→ {"items": 최신순(첨부 이미지 본문 제외), "count": 조건에 맞는 전체 수, "summary": 종류별 수}.
    filters: kind·category·target·value 중 값이 있는 것만 같음(=) 조건"""
    _require_admin(user)
    limit, offset = int(limit or 50), int(offset or 0)
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("limit 은 1~200, offset 은 0 이상이어야 합니다.")
    conds = {k: v for k, v in (filters or {}).items() if k in LIST_FILTERS and v}
    where = " AND ".join(f"f.{k}=?" for k in conds) or "1=1"
    rows = db.execute(f"""SELECT f.*, u.email AS user_email FROM beta_feedback f
                          LEFT JOIN app_user u ON u.user_id = f.user_id
                          WHERE {where} ORDER BY f.id DESC LIMIT ? OFFSET ?""",
                      (*conds.values(), limit, offset)).fetchall()
    count = db.execute(f"SELECT count(*) FROM beta_feedback f WHERE {where}", tuple(conds.values())).fetchone()[0]
    summary = dict(db.execute("SELECT kind, count(*) FROM beta_feedback GROUP BY kind").fetchall())
    return {"items": [_item(r) for r in rows], "count": count, "summary": summary}


def get_feedback(db, user, feedback_id):
    """→ 의견 1건 + 첨부 이미지(data URL). 없으면 None (404)"""
    _require_admin(user)
    row = db.execute("""SELECT f.*, u.email AS user_email FROM beta_feedback f
                        LEFT JOIN app_user u ON u.user_id = f.user_id WHERE f.id=?""", (int(feedback_id),)).fetchone()
    if row is None:
        return None
    item = _item(row)
    data = row["attachment_data"]
    item["attachment"] = (f"data:{row['attachment_type']};base64,{base64.b64encode(data).decode()}"
                          if data is not None else None)
    return item


def set_status(db, user, feedback_id, body):
    """처리 상태 바꾸기 {"status": "NEW"|"CHECKED"|"DONE"} → 바뀐 1건 (없으면 None → 404)"""
    _require_admin(user)
    status = body.get("status") if isinstance(body, dict) else None
    if status not in STATUSES:
        raise ValueError("status 는 NEW·CHECKED·DONE 중 하나여야 합니다.")
    with db:
        cur = db.execute("UPDATE beta_feedback SET status=?, status_updated_at=?, status_updated_by=? WHERE id=?",
                         (status, datetime.now().isoformat(timespec="seconds"), user["user_id"], int(feedback_id)))
    return get_feedback(db, user, feedback_id) if cur.rowcount else None


def _flat(value, prefix=""):
    """설문 answers·👍👎 extra → {"likert.find_easy": 4, "best_features": "점수 계산; 일정", …}"""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out.update(_flat(v, f"{prefix}{k}."))
        return out
    return {prefix[:-1]: "; ".join(map(str, value)) if isinstance(value, list) else value}


CSV_COLUMNS = [("id", "번호"), ("created_at", "보낸 시각"), ("kind", "종류"), ("category", "유형"), ("target", "대상"),
               ("value", "👍👎"), ("message", "내용"), ("email", "답변 받을 이메일"), ("user_email", "보낸 사람"),
               ("notice_no", "공고번호"), ("page", "보던 화면"), ("has_attachment", "첨부"), ("status", "처리 상태"),
               ("status_updated_at", "처리 시각")]


def export_csv(db, user, filters=None):
    """→ (파일 이름, CSV 바이트). 엑셀에서 한글이 깨지지 않게 UTF-8 BOM. 설문·추가정보는 항목마다 열로 펼친다"""
    _require_admin(user)
    items, offset = [], 0
    while True:
        page = list_feedback(db, user, filters, limit=200, offset=offset)["items"]
        items += page
        offset += len(page)
        if len(page) < 200:
            break
    extra = [_flat(i["payload"]) if i["payload"] else {} for i in items]
    extra_keys = sorted({k for e in extra for k in e})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in CSV_COLUMNS] + extra_keys)
    for item, e in zip(items, extra):
        item = {**item, "page": (item["context"] or {}).get("page")}
        w.writerow([item.get(k) for k, _ in CSV_COLUMNS] + [e.get(k) for k in extra_keys])
    return f"bidmate_feedback_{datetime.now():%Y%m%d}.csv", buf.getvalue().encode("utf-8-sig")
