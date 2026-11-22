"""업체정보 API (FUNC-001~004) + 점수 계산 저장 (company_evaluation_draft).

- 1차 자격 판정(company_bid_eligibility)은 AI 파트(LLM)가 한다. 여기서는 판정 없이 업체 정보만 저장·제공한다.
- 업체는 가상 데이터만 쓴다 (스키마 02_company.sql 규칙).
- 로그인 필요. 관리자는 모든 업체, 일반 사용자는 company_user ACTIVE 소속 업체만 (backend/auth.py).
  권한 없는 업체는 있는지 여부도 알리지 않게 '없음'(404)과 같게 처리한다.
"""
import json
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from backend import auth, notices_api, scoring
from backend.db.init_db import DEFAULT_DB_PATH


class InputError(ValueError):
    pass


def connect(db_path=None):
    path = Path(db_path or DEFAULT_DB_PATH)
    if not path.exists():
        raise notices_api.DatabaseMissing(f"DB 파일이 없습니다: {path}")
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ 기준정보 (선택 목록)
def regions(db):
    """광역(area_limit) → 시군구 목록. 업체 본점 소재지 선택용"""
    groups = [dict(r) for r in db.execute(
        "SELECT area_limit, area_limit_name FROM srm_region_group_master WHERE is_active=1 ORDER BY area_limit")]
    for g in groups:
        g["districts"] = [dict(r) for r in db.execute(
            "SELECT region_code, region_name, si_name, gun_name, gu_name FROM srm_region_district_master "
            "WHERE area_limit=? ORDER BY region_name", (g["area_limit"],))]
    return {"regions": groups}


# 행안부 도로명주소 팝업 결과의 시도명(siNm) → SRM 광역(area_limit). 광주·전남은 SRM 에서 '전남광주' 하나
SIDO_TO_AREA = {"서울특별시": "S", "부산광역시": "B", "대구광역시": "T", "인천광역시": "H", "광주광역시": "I",
                "대전광역시": "D", "울산광역시": "Z", "세종특별자치시": "L", "경기도": "G", "강원특별자치도": "F",
                "강원도": "F", "충청북도": "C", "충청남도": "R", "전북특별자치도": "J", "전라북도": "J",
                "전라남도": "I", "경상북도": "K", "경상남도": "M", "제주특별자치도": "E"}
# 행안부 시군구명 → SRM 목록 이름이 다른 경우 (SRM 이 옛 이름을 씀)
SGG_ALIAS = {("H", "미추홀구"): "남구"}  # 인천 남구 → 2018 미추홀구


def match_region(db, si_nm, sgg_nm):
    """도로명주소 시도·시군구 이름 → SRM (area_limit, region_code) 또는 None(못 맞춤 → REVIEW).
    시군구는 이름이 같으면 그대로, 'X시 Y구'인데 SRM 에 'X시'만 있으면 'X시', 세종(시군구 없음)은 SRM 의 하나뿐인 시군구."""
    area = SIDO_TO_AREA.get((si_nm or "").strip())
    if not area:
        return None
    names = {r["region_name"]: r["region_code"] for r in db.execute(
        "SELECT region_name, region_code FROM srm_region_district_master WHERE area_limit=?", (area,))}
    sgg = re.sub(r"\s+", " ", (sgg_nm or "").strip())
    sgg = SGG_ALIAS.get((area, sgg), sgg)
    if sgg in names:
        return area, names[sgg]
    if sgg and sgg.split(" ")[0] in names:
        return area, names[sgg.split(" ")[0]]
    if not sgg and len(names) == 1:
        return area, next(iter(names.values()))
    return None


def region_match(db, si_nm, sgg_nm):
    """GET /api/reference/region-match — 주소 팝업에서 고른 주소로 광역·시군구 선택값 채우기"""
    found = match_region(db, si_nm, sgg_nm)
    if not found:
        return {"matched": False, "area_limit": None, "sub_area_code": None}
    return {"matched": True, "area_limit": found[0], "sub_area_code": found[1]}


def licenses(db, q="", limit=50):
    rows = db.execute("SELECT license_code, license_name FROM license_master WHERE is_active=1 "
                      "AND (license_name LIKE ? OR license_code = ?) ORDER BY license_name LIMIT ?",
                      (f"%{q.strip()}%", q.strip(), limit))
    return {"licenses": [dict(r) for r in rows]}


def qualifications(db):
    rows = db.execute("SELECT qualification_code, qualification_name FROM restricted_qualification_master "
                      "WHERE is_active=1 ORDER BY qualification_code")
    return {"qualifications": [dict(r) for r in rows]}


# ------------------------------------------------------------------ 업체
def list_companies(db, user):
    """내 업체 목록 (관리자는 전체)"""
    if user["is_admin"]:
        rows = db.execute("SELECT company_id, company_name, updated_at FROM company ORDER BY updated_at DESC")
    else:
        rows = db.execute("""SELECT c.company_id, c.company_name, c.updated_at, u.role FROM company c
                             JOIN company_user u ON u.company_id = c.company_id
                             WHERE u.user_id=? AND u.membership_status='ACTIVE' ORDER BY c.updated_at DESC""",
                          (user["user_id"],))
    requests = db.execute("""SELECT c.company_id, c.company_name, u.membership_status FROM company c
                             JOIN company_user u ON u.company_id = c.company_id
                             WHERE u.user_id=? AND u.membership_status IN ('PENDING', 'REJECTED')""",
                          (user["user_id"],))
    return {"companies": [dict(r) for r in rows], "join_requests": [dict(r) for r in requests]}


# ------------------------------------------------------------------ 업체 합류 (가입 때 요청 → 소유자 승인)
def lookup_by_brn(db, brn):
    """가입 화면에서 합류할 업체 찾기: 사업자등록번호 10자리가 정확히 같은 업체의 이름·지역만 준다.
    찾았다고 바로 소속되지 않고, 합류 요청(PENDING)을 업체 소유자가 승인해야 업체 정보를 볼 수 있다."""
    no = re.sub(r"\D", "", brn or "")
    if len(no) != 10:
        raise InputError("사업자등록번호는 숫자 10자리입니다.")
    rows = db.execute("""SELECT c.company_id, c.company_name, g.area_limit_name, d.region_name FROM company c
                         JOIN srm_region_group_master g ON g.area_limit = c.area_limit
                         JOIN srm_region_district_master d ON d.area_limit = c.area_limit AND d.region_code = c.sub_area_code
                         WHERE c.business_registration_no=? ORDER BY c.created_at""", (no,))
    return {"companies": [dict(r) for r in rows]}


def _is_owner(db, user, company_id):
    return user["is_admin"] or db.execute(
        "SELECT 1 FROM company_user WHERE company_id=? AND user_id=? AND role='OWNER' AND membership_status='ACTIVE'",
        (company_id, user["user_id"])).fetchone() is not None


def members(db, company_id, user):
    """업체 소속 사용자·합류 요청 목록 (소유자·관리자만. 아니면 None → 404)"""
    if not _is_owner(db, user, company_id):
        return None
    rows = db.execute("""SELECT a.user_id, a.email, u.role, u.membership_status, u.created_at FROM company_user u
                         JOIN app_user a ON a.user_id = u.user_id WHERE u.company_id=?
                         ORDER BY u.membership_status='PENDING' DESC, u.created_at""", (company_id,))
    return {"company_id": company_id, "members": [dict(r) for r in rows]}


def set_member_status(db, company_id, member_id, status, user):
    """합류 요청 승인(ACTIVE)·거절(REJECTED). 소유자 자신은 바꿀 수 없다"""
    if not _is_owner(db, user, company_id):
        return None
    if status not in ("ACTIVE", "REJECTED"):
        raise InputError("status 는 ACTIVE(승인) 또는 REJECTED(거절)입니다.")
    row = db.execute("SELECT role FROM company_user WHERE company_id=? AND user_id=?", (company_id, member_id)).fetchone()
    if row is None:
        return None
    if row["role"] == "OWNER":
        raise InputError("소유자의 소속 상태는 바꿀 수 없습니다.")
    with db:
        db.execute("UPDATE company_user SET membership_status=? WHERE company_id=? AND user_id=?",
                   (status, company_id, member_id))
    return members(db, company_id, user)


def get_company(db, company_id, user=None):
    if user is not None and not auth.can_access_company(db, user, company_id):
        return None
    row = db.execute("SELECT * FROM company WHERE company_id=?", (company_id,)).fetchone()
    if row is None:
        return None
    company = dict(row)
    region = db.execute("""SELECT g.area_limit_name, d.region_name FROM srm_region_group_master g
                           JOIN srm_region_district_master d ON d.area_limit = g.area_limit
                           WHERE g.area_limit=? AND d.region_code=?""",
                        (row["area_limit"], row["sub_area_code"])).fetchone()
    company["region"] = dict(region) if region else None
    company["licenses"] = [dict(r) for r in db.execute(
        """SELECT l.license_code, m.license_name, l.license_registration_no, l.license_registration_date
           FROM company_license l JOIN license_master m ON m.license_code = l.license_code
           WHERE l.company_id=? ORDER BY m.license_name""", (company_id,))]
    company["qualifications"] = [dict(r) for r in db.execute(
        """SELECT q.qualification_code, m.qualification_name FROM company_restricted_qualification q
           JOIN restricted_qualification_master m ON m.qualification_code = q.qualification_code
           WHERE q.company_id=? ORDER BY q.qualification_code""", (company_id,))]
    return company


def _check(db, body, partial=False):
    """입력 검증 → 저장할 값. 잘못되면 InputError(사용자에게 보여 줄 문장)"""
    out = {}
    if not partial or "company_name" in body:
        name = (body.get("company_name") or "").strip()
        if not name:
            raise InputError("업체명을 입력하세요.")
        out["company_name"] = name
    if "business_registration_no" in body:
        no = re.sub(r"\D", "", body.get("business_registration_no") or "") or None
        if no and len(no) != 10:
            raise InputError("사업자등록번호는 숫자 10자리입니다.")
        out["business_registration_no"] = no
    # 본점 소재지는 행안부 도로명주소 팝업에서 고른 주소만 받는다 (직접 입력·지역 직접 선택 없음).
    # 광역·시군구는 팝업 결과의 시도·시군구(juso.siNm, sggNm)로 서버가 정한다 → address_validation_status=MATCH
    juso = body.get("juso")
    if not partial or "juso" in body or "head_office_address" in body:
        if not isinstance(juso, dict) or not (juso.get("siNm") or "").strip():
            raise InputError("본점 소재지는 주소 검색으로 선택하세요.")
        mapped = match_region(db, juso.get("siNm"), juso.get("sggNm"))
        if not mapped:
            raise InputError(f"SRM 지역 기준정보에 없는 시군구입니다: {juso.get('siNm')} {juso.get('sggNm') or ''}".strip())
        addr = (body.get("head_office_address") or "").strip()
        if not addr:
            raise InputError("본점 소재지 주소를 입력하세요.")
        out["head_office_address"] = addr
        out["area_limit"], out["sub_area_code"] = mapped
        out["address_validation_status"] = "MATCH"
    licenses_in = body.get("licenses")
    if licenses_in is not None:
        for lic in licenses_in:
            if not db.execute("SELECT 1 FROM license_master WHERE license_code=?", (lic.get("license_code"),)).fetchone():
                raise InputError(f"없는 면허 코드입니다: {lic.get('license_code')}")
            if not (lic.get("license_registration_no") or "").strip():
                raise InputError("면허 등록번호를 입력하세요.")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", lic.get("license_registration_date") or ""):
                raise InputError("면허 등록일은 YYYY-MM-DD 형식입니다.")
        if len({lic["license_code"] for lic in licenses_in}) != len(licenses_in):
            raise InputError("같은 면허가 두 번 들어 있습니다.")
    quals = body.get("qualifications")
    if quals is not None:
        for code in quals:
            if not db.execute("SELECT 1 FROM restricted_qualification_master WHERE qualification_code=?",
                              (code,)).fetchone():
                raise InputError(f"없는 제한경쟁입찰자격 코드입니다: {code}")
    return out, licenses_in, quals


def _replace_children(db, company_id, licenses_in, quals, now):
    if licenses_in is not None:
        db.execute("DELETE FROM company_license WHERE company_id=?", (company_id,))
        for lic in licenses_in:
            db.execute("""INSERT INTO company_license (company_license_id, company_id, license_code,
                          license_registration_no, license_registration_date, created_at, updated_at)
                          VALUES (?,?,?,?,?,?,?)""",
                       (str(uuid.uuid4()), company_id, lic["license_code"], lic["license_registration_no"].strip(),
                        lic["license_registration_date"], now, now))
    if quals is not None:
        db.execute("DELETE FROM company_restricted_qualification WHERE company_id=?", (company_id,))
        for code in quals:
            db.execute("""INSERT INTO company_restricted_qualification (company_qualification_id, company_id,
                          qualification_code, created_at, updated_at) VALUES (?,?,?,?,?)""",
                       (str(uuid.uuid4()), company_id, code, now, now))


def create_company(db, body, user):
    values, licenses_in, quals = _check(db, body)
    company_id, now = str(uuid.uuid4()), _now()
    with db:
        db.execute("""INSERT INTO company (company_id, company_name, business_registration_no, head_office_address,
                      area_limit, sub_area_code, address_validation_status, created_at, updated_at)
                      VALUES (?,?,?,?,?,?,?,?,?)""",
                   (company_id, values["company_name"], values.get("business_registration_no"),
                    values["head_office_address"], values["area_limit"], values["sub_area_code"],
                    values["address_validation_status"], now, now))
        _replace_children(db, company_id, licenses_in, quals, now)
        db.execute("""INSERT INTO company_user (company_id, user_id, role, membership_status, created_at)
                      VALUES (?,?,'OWNER','ACTIVE',?)""", (company_id, user["user_id"], now))  # 만든 사람 = 소유자
    return get_company(db, company_id)


def update_company(db, company_id, body, user):
    if get_company(db, company_id, user) is None:
        return None
    values, licenses_in, quals = _check(db, body, partial=True)
    now = _now()
    with db:
        if values:
            sets = ", ".join(f"{k}=?" for k in values)
            db.execute(f"UPDATE company SET {sets}, updated_at=? WHERE company_id=?",
                       (*values.values(), now, company_id))
        _replace_children(db, company_id, licenses_in, quals, now)
    return get_company(db, company_id)


# ------------------------------------------------------------------ 점수 계산 저장 (업체·공고당 직전 1건)
def save_evaluation(db, company_id, notice_no, inputs, user):
    if get_company(db, company_id, user) is None:
        return None
    result = scoring.calculate(db, notice_no, inputs)
    if result is None:
        return None
    status = {"CALCULATED": "CALCULATED", "IN_PROGRESS": "IN_PROGRESS"}.get(result["status"], "ON_HOLD")
    with db:
        db.execute("""INSERT INTO company_evaluation_draft (company_id, notice_no, inputs, item_scores, total_score,
                      calculation_status, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?)
                      ON CONFLICT(company_id, notice_no) DO UPDATE SET inputs=excluded.inputs,
                      item_scores=excluded.item_scores, total_score=excluded.total_score,
                      calculation_status=excluded.calculation_status, updated_at=excluded.updated_at,
                      updated_by=excluded.updated_by""",
                   (company_id, notice_no, json.dumps(inputs, ensure_ascii=False),
                    json.dumps(result["items"], ensure_ascii=False), result["total"], status, _now(),
                    user["user_id"]))
    return result


def get_evaluation_draft(db, company_id, notice_no, user):
    if not auth.can_access_company(db, user, company_id):
        return None
    row = db.execute("SELECT * FROM company_evaluation_draft WHERE company_id=? AND notice_no=?",
                     (company_id, notice_no)).fetchone()
    if row is None:
        return None
    draft = dict(row)
    draft["inputs"] = json.loads(draft["inputs"])
    draft["item_scores"] = json.loads(draft["item_scores"])
    return draft
