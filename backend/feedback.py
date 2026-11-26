"""베타 의견 저장 (POST /api/feedback, UI-10) — 형식: docs/design/frontend-requirements.md 11장.

세 종류(kind)를 beta_feedback 한 테이블에 쌓는다. 로그인 없이도 보낼 수 있고, 로그인했으면 세션 사용자로 기록한다.
"""
import base64
import binascii
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
