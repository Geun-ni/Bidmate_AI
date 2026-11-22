"""업체정보 API (FUNC-001~004) + 점수 계산 저장 (company_evaluation_draft).

- 1차 자격 판정(company_bid_eligibility)은 AI 파트(LLM)가 한다. 여기서는 판정 없이 업체 정보만 저장·제공한다.
- 업체는 가상 데이터만 쓴다 (스키마 02_company.sql 규칙). 로그인·소속 확인은 아직 없다(app_user 미구현).
"""
import json
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from backend import notices_api, scoring
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
def get_company(db, company_id):
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
    if not partial or "head_office_address" in body:
        addr = (body.get("head_office_address") or "").strip()
        if not addr:
            raise InputError("본점 소재지 주소를 입력하세요.")
        out["head_office_address"] = addr
    if not partial or "area_limit" in body or "sub_area_code" in body:
        area, sub = body.get("area_limit"), body.get("sub_area_code")
        if not db.execute("SELECT 1 FROM srm_region_district_master WHERE area_limit=? AND region_code=?",
                          (area, sub)).fetchone():
            raise InputError("광역·시군구 조합이 SRM 지역 기준정보에 없습니다.")
        out["area_limit"], out["sub_area_code"] = area, sub
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


def create_company(db, body):
    values, licenses_in, quals = _check(db, body)
    company_id, now = str(uuid.uuid4()), _now()
    with db:
        db.execute("""INSERT INTO company (company_id, company_name, business_registration_no, head_office_address,
                      area_limit, sub_area_code, address_validation_status, created_at, updated_at)
                      VALUES (?,?,?,?,?,?,'REVIEW',?,?)""",
                   (company_id, values["company_name"], values.get("business_registration_no"),
                    values["head_office_address"], values["area_limit"], values["sub_area_code"], now, now))
        _replace_children(db, company_id, licenses_in, quals, now)
    return get_company(db, company_id)


def update_company(db, company_id, body):
    if get_company(db, company_id) is None:
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
def save_evaluation(db, company_id, notice_no, inputs):
    if get_company(db, company_id) is None:
        raise InputError("업체를 찾을 수 없습니다.")
    result = scoring.calculate(db, notice_no, inputs)
    if result is None:
        return None
    status = {"CALCULATED": "CALCULATED", "IN_PROGRESS": "IN_PROGRESS"}.get(result["status"], "ON_HOLD")
    with db:
        db.execute("""INSERT INTO company_evaluation_draft (company_id, notice_no, inputs, item_scores, total_score,
                      calculation_status, updated_at) VALUES (?,?,?,?,?,?,?)
                      ON CONFLICT(company_id, notice_no) DO UPDATE SET inputs=excluded.inputs,
                      item_scores=excluded.item_scores, total_score=excluded.total_score,
                      calculation_status=excluded.calculation_status, updated_at=excluded.updated_at""",
                   (company_id, notice_no, json.dumps(inputs, ensure_ascii=False),
                    json.dumps(result["items"], ensure_ascii=False), result["total"], status, _now()))
    return result


def get_evaluation_draft(db, company_id, notice_no):
    row = db.execute("SELECT * FROM company_evaluation_draft WHERE company_id=? AND notice_no=?",
                     (company_id, notice_no)).fetchone()
    if row is None:
        return None
    draft = dict(row)
    draft["inputs"] = json.loads(draft["inputs"])
    draft["item_scores"] = json.loads(draft["item_scores"])
    return draft
