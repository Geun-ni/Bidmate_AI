-- 01. 기준정보 (master)
-- 쓰는 모듈: backend/db/seed_reference.py (docs/planning/reference/*.xlsx, 업체정보 데이터 정의서 §5.3·§6.2)
-- 읽는 모듈: (예정) 업체정보 입력 API, (예정) 룰 엔진 자격 판정
-- 의존: 없음
-- 규칙: 코드는 앞자리 0 보존을 위해 전부 TEXT. 시군구 코드는 시드 시 6자리로 복원(011013).

-- 일반 면허 (면허종류.xlsx, SRM licenseQualificationCode)
CREATE TABLE IF NOT EXISTS license_master (
    license_code   TEXT PRIMARY KEY,                    -- 예: A06, 0041
    license_name   TEXT NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

-- 제한경쟁입찰자격 (제한경쟁입찰자격 종류.xlsx, SRM qualifiedQualificationCode)
CREATE TABLE IF NOT EXISTS restricted_qualification_master (
    qualification_code  TEXT PRIMARY KEY,               -- 예: 1, B, C, 2 (숫자도 문자열)
    qualification_name  TEXT NOT NULL,
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

-- 자격 상하위 인정: 업체가 held 를 보유하면 공고 요구 accepted 를 충족 (자기 자신 포함)
CREATE TABLE IF NOT EXISTS restricted_qualification_equivalence (
    held_qualification_code    TEXT NOT NULL REFERENCES restricted_qualification_master(qualification_code),
    accepted_requirement_code  TEXT NOT NULL REFERENCES restricted_qualification_master(qualification_code),
    is_active                  INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    source_note                TEXT,
    PRIMARY KEY (held_qualification_code, accepted_requirement_code)
);

-- 면허 상하위 인정 (예: 감리업(전력종합감리) 보유 → 감리업(전력전문감리) 요구 충족). 시드 데이터는 예정
CREATE TABLE IF NOT EXISTS license_equivalence (
    held_license_code      TEXT NOT NULL REFERENCES license_master(license_code),
    accepted_license_code  TEXT NOT NULL REFERENCES license_master(license_code),
    is_active              INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    source_note            TEXT,
    PRIMARY KEY (held_license_code, accepted_license_code)
);

-- SRM 지역그룹 (areaLimit). U(전남)·W(광주)는 과거 공고 판독용, 업체 선택 불가(is_active=0)
CREATE TABLE IF NOT EXISTS srm_region_group_master (
    area_limit       TEXT PRIMARY KEY,                  -- 예: F
    area_limit_name  TEXT NOT NULL UNIQUE,              -- 예: 강원
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

-- SRM 시·군·구 (지역제한 목록.xlsx, findAreaRestrictList)
CREATE TABLE IF NOT EXISTS srm_region_district_master (
    code_id      TEXT PRIMARY KEY,                      -- 예: F_011013
    area_limit   TEXT NOT NULL REFERENCES srm_region_group_master(area_limit),
    region_code  TEXT NOT NULL,                         -- 예: 011013 (6자리, 앞자리 0 보존)
    region_name  TEXT NOT NULL,                         -- 예: 춘천시
    sub_area_yn  INTEGER NOT NULL DEFAULT 1 CHECK (sub_area_yn IN (0, 1)),
    UNIQUE (area_limit, region_code)
);
