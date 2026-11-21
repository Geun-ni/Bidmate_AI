-- 02. 업체 정보
-- 쓰는 모듈: (예정) 업체정보 입력 API ← 프론트 업체정보 입력 화면(UI-02, UI-07). 현재 backend/server.py 에는 /api/demo 만 있음
-- 읽는 모듈: (예정) 룰 엔진 자격 판정, (예정) 알림 발송
-- 의존: 01_reference.sql
-- 규칙: 업체는 가상 데이터만 사용. PK 는 업체정보 데이터 정의서 v0.2 이름(company_id 등, UUID 문자열)을 따름.

CREATE TABLE IF NOT EXISTS company (
    company_id                 TEXT PRIMARY KEY,        -- UUID
    company_name               TEXT NOT NULL CHECK (length(trim(company_name)) > 0),
    business_registration_no   TEXT CHECK (business_registration_no IS NULL
                                           OR (length(business_registration_no) = 10
                                               AND business_registration_no NOT GLOB '*[^0-9]*')),
    head_office_address        TEXT NOT NULL,
    area_limit                 TEXT NOT NULL REFERENCES srm_region_group_master(area_limit),
    sub_area_code              TEXT NOT NULL,           -- srm_region_district_master.region_code
    address_validation_status  TEXT NOT NULL DEFAULT 'REVIEW'
                                   CHECK (address_validation_status IN ('MATCH', 'MISMATCH', 'REVIEW')),
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL,
    -- (광역, 시군구) 조합이 기준정보에 있어야 저장 가능 (예: 강원 + 가평군 차단)
    FOREIGN KEY (area_limit, sub_area_code) REFERENCES srm_region_district_master(area_limit, region_code)
);

CREATE TABLE IF NOT EXISTS company_license (
    company_license_id         TEXT PRIMARY KEY,        -- UUID
    company_id                 TEXT NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
    license_code               TEXT NOT NULL REFERENCES license_master(license_code),
    license_registration_no    TEXT NOT NULL,
    license_registration_date  TEXT NOT NULL,           -- YYYY-MM-DD (실제 등록일자)
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL,
    UNIQUE (company_id, license_code)
);

CREATE TABLE IF NOT EXISTS company_restricted_qualification (
    company_qualification_id  TEXT PRIMARY KEY,         -- UUID
    company_id                TEXT NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
    qualification_code        TEXT NOT NULL REFERENCES restricted_qualification_master(qualification_code),
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL,
    UNIQUE (company_id, qualification_code)
);

-- 로그인 사용자. 알림 수신 설정도 사용자 단위로 여기에 둔다 (옛 company_notification_setting)
CREATE TABLE IF NOT EXISTS app_user (
    user_id            TEXT PRIMARY KEY,                 -- UUID
    email              TEXT NOT NULL UNIQUE,
    password_hash      TEXT,                             -- 원문 저장 금지. 외부 로그인이면 NULL
    display_name       TEXT,
    email_enabled      INTEGER NOT NULL DEFAULT 0 CHECK (email_enabled IN (0, 1)),
    telegram_chat_id   TEXT,
    telegram_enabled   INTEGER NOT NULL DEFAULT 0 CHECK (telegram_enabled IN (0, 1)),
    include_review     INTEGER NOT NULL DEFAULT 1 CHECK (include_review IN (0, 1)),  -- REVIEW 공고도 알릴지
    consent_at         TEXT,                             -- 수신 동의 일시 (NULL이면 발송 금지)
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

-- 업체 소속 (한 업체에 여러 사용자). 업체 데이터 API는 membership_status='ACTIVE' 인 소속만 조회·수정 허용
-- 업체명·사업자번호가 같다고 자동 연결하지 않는다 (업체정보 정의서 §9)
CREATE TABLE IF NOT EXISTS company_user (
    company_id         TEXT NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
    user_id            TEXT NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    role               TEXT NOT NULL DEFAULT 'MEMBER' CHECK (role IN ('OWNER', 'MEMBER')),
    membership_status  TEXT NOT NULL DEFAULT 'PENDING' CHECK (membership_status IN ('PENDING', 'ACTIVE', 'REJECTED')),
    created_at         TEXT NOT NULL,
    PRIMARY KEY (company_id, user_id)
);
CREATE INDEX IF NOT EXISTS company_user_user_idx ON company_user (user_id);
