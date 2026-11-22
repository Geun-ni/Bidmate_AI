"""로그인 (이메일·비밀번호) + 세션 쿠키 + 관리자 구분.

- 비밀번호: hashlib.scrypt 해시만 app_user.password_hash 에 저장 (원문 저장 안 함).
- 세션: 서버에 표를 두지 않는 서명 쿠키. 값 = user_id.만료시각.서명,
  서명 = HMAC(.env SESSION_SECRET, user_id·만료·password_hash) → 비밀번호를 바꾸면 기존 로그인이 풀린다.
  로그아웃은 쿠키 삭제라서 서버가 남의 세션을 강제로 끊지는 못한다(필요해지면 세션 표를 둔다).
- 관리자: .env ADMIN_EMAIL(쉼표로 여러 개)에 있는 이메일. 관리자는 모든 업체를 조회·수정할 수 있다.
- 일반 사용자: company_user 에서 membership_status='ACTIVE' 인 업체만.

    python -m backend.auth create-admin   # .env 의 ADMIN_EMAIL·ADMIN_PASSWORD 로 관리자 계정 생성·갱신
                                          # (ADMIN_PASSWORD 가 없으면 만들어 .env 에 적는다. 화면에 출력 안 함)
"""
import argparse
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime

from backend.db.init_db import DEFAULT_DB_PATH, ROOT

ENV_PATH = ROOT / ".env"
COOKIE = "bidmate_session"
SESSION_SECONDS = 7 * 24 * 3600
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


class AuthError(Exception):
    """401: 로그인 필요 / 로그인 실패"""


def _env(name):
    if os.environ.get(name):
        return os.environ[name]
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return None


_FALLBACK_SECRET = secrets.token_hex(32)  # .env 에 SESSION_SECRET 이 없으면 서버를 다시 켤 때 로그인이 풀린다


def _secret():
    return (_env("SESSION_SECRET") or _FALLBACK_SECRET).encode()


def admin_emails():
    return {e.strip().lower() for e in (_env("ADMIN_EMAIL") or "").split(",") if e.strip()}


# ------------------------------------------------------------------ 비밀번호
def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    except (AttributeError, ValueError):
        return False
    return hmac.compare_digest(got.hex(), digest)


# ------------------------------------------------------------------ 세션 쿠키
def _sign(user_id, expires, password_hash):
    return hmac.new(_secret(), f"{user_id}.{expires}.{password_hash}".encode(), hashlib.sha256).hexdigest()


def make_token(user):
    expires = int(time.time()) + SESSION_SECONDS
    return f"{user['user_id']}.{expires}.{_sign(user['user_id'], expires, user['password_hash'])}"


def user_from_token(db, token):
    """쿠키 값 → 사용자 dict (is_admin 포함) 또는 None"""
    try:
        user_id, expires, sig = (token or "").split(".")
        if int(expires) < time.time():
            return None
    except ValueError:
        return None
    row = db.execute("SELECT * FROM app_user WHERE user_id=?", (user_id,)).fetchone()
    if row is None or not row["password_hash"] or not hmac.compare_digest(sig, _sign(user_id, expires, row["password_hash"])):
        return None
    return public_user(row)


def public_user(row):
    return {"user_id": row["user_id"], "email": row["email"], "display_name": row["display_name"],
            "is_admin": row["email"].lower() in admin_emails()}


# ------------------------------------------------------------------ 가입·로그인
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def signup(db, body):
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not EMAIL_RE.fullmatch(email):
        raise ValueError("이메일 형식을 확인하세요.")
    if len(password) < 8:
        raise ValueError("비밀번호는 8자 이상입니다.")
    if db.execute("SELECT 1 FROM app_user WHERE email=?", (email,)).fetchone():
        raise ValueError("이미 가입된 이메일입니다.")
    join = (body.get("join_company_id") or "").strip()  # 기존 업체 합류 요청 (소유자 승인 전까지 PENDING)
    if join and not db.execute("SELECT 1 FROM company WHERE company_id=?", (join,)).fetchone():
        raise ValueError("합류할 업체를 찾을 수 없습니다.")
    now, user_id = _now(), str(uuid.uuid4())
    with db:
        db.execute("INSERT INTO app_user (user_id, email, password_hash, display_name, created_at, updated_at) "
                   "VALUES (?,?,?,?,?,?)", (user_id, email, hash_password(password),
                                            (body.get("display_name") or "").strip() or None, now, now))
        if join:
            db.execute("INSERT INTO company_user (company_id, user_id, role, membership_status, created_at) "
                       "VALUES (?,?,'MEMBER','PENDING',?)", (join, user_id, now))
    return login(db, {"email": email, "password": password})


def login(db, body):
    """→ (사용자, 쿠키 값). 실패하면 AuthError (이메일·비밀번호 중 무엇이 틀렸는지 알려 주지 않는다)"""
    email = (body.get("email") or "").strip().lower()
    row = db.execute("SELECT * FROM app_user WHERE email=?", (email,)).fetchone()
    if row is None or not verify_password(body.get("password") or "", row["password_hash"]):
        raise AuthError("이메일 또는 비밀번호가 맞지 않습니다.")
    return public_user(row), make_token(row)


def can_access_company(db, user, company_id):
    if user["is_admin"]:
        return True
    return db.execute("SELECT 1 FROM company_user WHERE company_id=? AND user_id=? AND membership_status='ACTIVE'",
                      (company_id, user["user_id"])).fetchone() is not None


# ------------------------------------------------------------------ 관리자 계정 만들기 (CLI)
def _append_env(name, value):
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    ENV_PATH.write_text(text + f"{name}={value}\n", encoding="utf-8")


def create_admin(db_path=DEFAULT_DB_PATH):
    email = next(iter(sorted(admin_emails())), None)
    if not email:
        raise SystemExit(".env 에 ADMIN_EMAIL=관리자이메일 을 먼저 적으세요.")
    if not _env("SESSION_SECRET"):
        _append_env("SESSION_SECRET", secrets.token_hex(32))
    password = _env("ADMIN_PASSWORD")
    if not password:
        password = secrets.token_urlsafe(12)
        _append_env("ADMIN_PASSWORD", password)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    now = _now()
    with db:
        row = db.execute("SELECT user_id FROM app_user WHERE email=?", (email,)).fetchone()
        if row:
            db.execute("UPDATE app_user SET password_hash=?, updated_at=? WHERE user_id=?",
                       (hash_password(password), now, row["user_id"]))
        else:
            db.execute("INSERT INTO app_user (user_id, email, password_hash, display_name, created_at, updated_at) "
                       "VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), email, hash_password(password), "관리자", now, now))
    db.close()
    print(f"관리자 계정 {'갱신' if row else '생성'}: {email} (비밀번호는 .env ADMIN_PASSWORD)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["create-admin"])
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = ap.parse_args()
    create_admin(args.db)


if __name__ == "__main__":
    main()
