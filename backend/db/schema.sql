-- 입찰메이트(Bidmate_AI) DB 스키마 v1.0 (SQLite)
-- 기준 문서: 기능명세서 §5, 업체정보 데이터 정의서 v0.2, 11-18 회의 결정
-- 설명서: backend/db/README.md
--
-- 영역                     | 채우는 쪽
-- A. 기준정보(master)       | 시드 스크립트 (backend/db/seed_reference.py, 참고 엑셀)
-- B. 업체                   | 업체정보 입력 화면 (프론트 → 백엔드 API)
-- C. 공고 수집              | 수집기 (backend/collector) — Open API + SRM
-- D. 문서 분석              | AI 파트 (LLM 추출·요약) — 이 파일은 저장 구조만 정의
-- E. 판정·점수              | 룰 엔진 (백엔드)
-- F. 사용자 작업·알림       | 화면 동작 / 알림 발송
--
-- 공통 규칙
-- * 코드값(면허·자격·지역)은 앞자리 0 보존을 위해 모두 TEXT
-- * 금액은 원 단위 INTEGER, 일시는 ISO-8601 TEXT(예: 2026-11-12T14:00:00+09:00)
-- * 상태값은 CHECK 제약으로 고정, 조회 실패와 '해당 없음'은 반드시 구분
-- * 업체 ID 등 사용자 데이터 PK는 UUID 문자열(TEXT)
PRAGMA foreign_keys = ON;

------------------------------------------------------------------------
-- A. 기준정보 (master)
------------------------------------------------------------------------

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

-- 상하위 인정: 업체가 held 를 보유하면 공고 요구 accepted 를 충족 (자기 자신 포함)
CREATE TABLE IF NOT EXISTS restricted_qualification_equivalence (
    held_qualification_code    TEXT NOT NULL REFERENCES restricted_qualification_master(qualification_code),
    accepted_requirement_code  TEXT NOT NULL REFERENCES restricted_qualification_master(qualification_code),
    is_active                  INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    source_note                TEXT,
    PRIMARY KEY (held_qualification_code, accepted_requirement_code)
);

-- 면허 상하위 인정 (예: 감리업(전력종합감리) 보유 → 감리업(전력전문감리) 요구 충족)
CREATE TABLE IF NOT EXISTS license_equivalence (
    held_license_code      TEXT NOT NULL REFERENCES license_master(license_code),
    accepted_license_code  TEXT NOT NULL REFERENCES license_master(license_code),
    is_active              INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    source_note            TEXT,
    PRIMARY KEY (held_license_code, accepted_license_code)
);

-- SRM 지역그룹 (areaLimit). U(전남)·W(광주)는 과거 공고 판독용, 업체 선택 불가
CREATE TABLE IF NOT EXISTS srm_region_group_master (
    area_limit       TEXT PRIMARY KEY,                  -- 예: F
    area_limit_name  TEXT NOT NULL UNIQUE,              -- 예: 강원
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

-- SRM 시·군·구 (지역제한 목록.xlsx, findAreaRestrictList)
CREATE TABLE IF NOT EXISTS srm_region_district_master (
    code_id      TEXT PRIMARY KEY,                      -- 예: F_011013
    area_limit   TEXT NOT NULL REFERENCES srm_region_group_master(area_limit),
    region_code  TEXT NOT NULL,                         -- 예: 011013 (앞자리 0 보존)
    region_name  TEXT NOT NULL,                         -- 예: 춘천시
    sub_area_yn  INTEGER NOT NULL DEFAULT 1 CHECK (sub_area_yn IN (0, 1)),
    UNIQUE (area_limit, region_code)
);

------------------------------------------------------------------------
-- B. 업체
------------------------------------------------------------------------

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

-- 알림 수신 설정 (FUNC-026, 채널·발송 정책 확정 전까지 저장 구조만)
CREATE TABLE IF NOT EXISTS company_notification_setting (
    company_id         TEXT PRIMARY KEY REFERENCES company(company_id) ON DELETE CASCADE,
    email              TEXT,
    telegram_chat_id   TEXT,
    email_enabled      INTEGER NOT NULL DEFAULT 0 CHECK (email_enabled IN (0, 1)),
    telegram_enabled   INTEGER NOT NULL DEFAULT 0 CHECK (telegram_enabled IN (0, 1)),
    include_review     INTEGER NOT NULL DEFAULT 1 CHECK (include_review IN (0, 1)),  -- REVIEW 공고도 알릴지
    consent_at         TEXT,                             -- 수신 동의 일시 (NULL이면 발송 금지)
    updated_at         TEXT NOT NULL
);

------------------------------------------------------------------------
-- C. 공고 수집 (backend/collector)
------------------------------------------------------------------------

-- 공고 기본정보 (Open API electContract.do). 수집 대상: companyId=COM01, purchaseType=ConstructionService
CREATE TABLE IF NOT EXISTS bid_notice (
    notice_no                     TEXT PRIMARY KEY,     -- 공고번호 (예: E012607925)
    bid_id                        TEXT,                 -- SRM 내부 ID
    company_id_src                TEXT NOT NULL DEFAULT 'COM01',  -- 발주 회사 구분(API companyId)
    title                         TEXT NOT NULL,
    purchase_type                 TEXT,                 -- ConstructionService (자재구매 Product 는 제외)
    item_type                     TEXT,                 -- Construction / Service
    bid_type                      TEXT,                 -- QualifiedEval 등 낙찰방법
    competition_type              TEXT,
    work_type                     TEXT,                 -- 공사유형 분류 (예: 배전공사 총액계약, 배전공사 감리용역)
    place_name                    TEXT,                 -- 발주기관
    notice_date                   TEXT,
    attend_req_begin_at           TEXT,                 -- 입찰참가신청 시작
    attend_req_close_at           TEXT,                 -- 입찰참가신청 마감
    bid_begin_at                  TEXT,                 -- 투찰 시작
    bid_end_at                    TEXT,                 -- 투찰 마감
    open_bid_at                   TEXT,                 -- 개찰 일시 (SRM, 확인된 경우)
    progress_state                TEXT,                 -- PreAttendProgress/AttendProgress = 진행중, 그 외 마감·유찰·개찰·종료
    is_open                       INTEGER GENERATED ALWAYS AS
                                      (progress_state IN ('PreAttendProgress', 'AttendProgress')) VIRTUAL,
    presumed_price                INTEGER,              -- 추정가격(VAT 제외): 적격심사기준 금액구간, 실적 요건
    estimated_price_basic_amount  INTEGER,              -- 예비가격 기초금액(VAT 포함): 투찰 참고
    approximate_amount            INTEGER,              -- 사정액(=사정금액): 시공경험 평가
    presumed_amount               INTEGER,              -- 추정금액
    a_amount                      INTEGER,              -- A금액 (SRM totalAAmt): 입찰가격 평가산식
    net_construction_cost         INTEGER,              -- 순공사원가 (SRM overHeadCostAmount): 98% 미만 심사 제외
    notice_doc_found              INTEGER CHECK (notice_doc_found IN (0, 1)),    -- 공고문 확보 (NULL=미확인)
    criteria_doc_found            INTEGER CHECK (criteria_doc_found IN (0, 1)),  -- 적격심사기준 확보
    api_raw                       TEXT NOT NULL,        -- Open API 응답 원본(JSON)
    first_seen_at                 TEXT NOT NULL,
    updated_at                    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bid_notice_open_idx ON bid_notice (progress_state, attend_req_close_at);

-- 투찰제한 자동심사 설정 (SRM findBidBasicInfo)
CREATE TABLE IF NOT EXISTS bid_restriction_snapshot (
    notice_no                     TEXT PRIMARY KEY REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    fetch_status                  TEXT NOT NULL CHECK (fetch_status IN ('SUCCESS', 'PARTIAL', 'FAILED')),
    error                         TEXT,
    license_auto_eval_method      TEXT,                 -- Auto / Manual / None
    license_auto_eval_type        TEXT,                 -- And / Or
    qualified_auto_eval_method    TEXT,
    qualified_auto_eval_type      TEXT,
    region_limit_auto_eval_method TEXT,
    region_limit_auto_eval_type   TEXT,
    license_main_auto_eval_type   TEXT,
    limited_reason_code           TEXT,
    raw_response                  TEXT,                 -- 담당자 이름·사번·연락처 제거 후 저장
    fetched_at                    TEXT NOT NULL
);

-- 범주별 조회 결과: SUCCESS + item_count=0 일 때만 '제한 없음'
CREATE TABLE IF NOT EXISTS bid_restriction_category (
    notice_no     TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    category      TEXT NOT NULL CHECK (category IN ('LICENSE', 'QUALIFIED', 'REGION', 'LICENSE_MAIN')),
    fetch_status  TEXT NOT NULL CHECK (fetch_status IN ('SUCCESS', 'FAILED')),
    item_count    INTEGER,
    error         TEXT,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (notice_no, category)
);

-- 공고 요구 면허자격명·적격자격명·지역·주력분야
CREATE TABLE IF NOT EXISTS bid_restriction_item (
    id             INTEGER PRIMARY KEY,
    notice_no      TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    category       TEXT NOT NULL CHECK (category IN ('LICENSE', 'QUALIFIED', 'REGION', 'LICENSE_MAIN')),
    code           TEXT,                                -- 면허/자격/주력분야 코드, 지역은 area_code와 동일
    name           TEXT,                                -- 면허자격명 / 적격자격명 / 지역명
    area_code      TEXT,                                -- REGION: 광역 그룹 (예: I)
    sub_area_code  TEXT,                                -- REGION: 시군구 (NULL = 광역 전체)
    sub_area_name  TEXT
);
CREATE INDEX IF NOT EXISTS bid_restriction_item_notice_idx ON bid_restriction_item (notice_no, category);

-- 공고문·적격심사기준 첨부 (Open API 우선 → 누락 시 SRM 보완)
CREATE TABLE IF NOT EXISTS bid_attachment (
    id               INTEGER PRIMARY KEY,
    notice_no        TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    doc_kind         TEXT NOT NULL CHECK (doc_kind IN ('NOTICE', 'CRITERIA')),
    filename         TEXT NOT NULL,
    file_ext         TEXT,
    source           TEXT NOT NULL CHECK (source IN ('OPEN_API', 'SRM_FALLBACK')),
    download_status  TEXT NOT NULL CHECK (download_status IN ('SUCCESS', 'FAILED')),
    local_path       TEXT,
    size_bytes       INTEGER,
    sha256           TEXT,                              -- 정정 공고 시 파일 변경 감지
    error            TEXT,
    fetched_at       TEXT NOT NULL,
    UNIQUE (notice_no, filename)
);

-- 첨부 원문 (본문 텍스트 + 한글 수식 스크립트). AI 파트의 입력
CREATE TABLE IF NOT EXISTS bid_document_content (
    attachment_id    INTEGER PRIMARY KEY REFERENCES bid_attachment(id) ON DELETE CASCADE,
    read_status      TEXT NOT NULL CHECK (read_status IN ('SUCCESS', 'FAILED')),
    text             TEXT,
    equations        TEXT,                              -- JSON [{"script": "...", "context": [...]}]
    paragraph_count  INTEGER,
    equation_count   INTEGER,
    error            TEXT,
    read_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collect_run (
    id               INTEGER PRIMARY KEY,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    api_notice_count INTEGER,
    target_count     INTEGER,
    processed_count  INTEGER,
    stopped_reason   TEXT
);

------------------------------------------------------------------------
-- D. 문서 분석 결과 (AI 파트가 채움 — 저장 구조만 정의, 필드 세부는 AI 파트와 협의)
------------------------------------------------------------------------

-- 공고별 분석 상태: 신규 공고 1회 분석·재사용, 정정 시 무효화 후 재분석
CREATE TABLE IF NOT EXISTS notice_analysis_cache (
    notice_no         TEXT PRIMARY KEY REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    document_version  TEXT,                             -- 분석에 쓴 첨부 sha256 조합
    analysis_status   TEXT NOT NULL CHECK (analysis_status IN ('PENDING', 'DONE', 'FAILED', 'INVALIDATED')),
    analyzed_at       TEXT,
    invalidated_at    TEXT
);

-- 문서 추출값 (field 예: eligibility, eligibility_docs, a_amount, office_target, industry_avg_debt_ratio ...)
CREATE TABLE IF NOT EXISTS document_extraction (
    id                   INTEGER PRIMARY KEY,
    notice_no            TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    attachment_id        INTEGER REFERENCES bid_attachment(id) ON DELETE SET NULL,
    field                TEXT NOT NULL,
    value                TEXT,                          -- 단일 값 또는 JSON
    source_location      TEXT,                          -- 근거 문장/조항
    verification_status  TEXT NOT NULL DEFAULT 'UNVERIFIED'
                             CHECK (verification_status IN ('UNVERIFIED', 'VERIFIED', 'REVIEW')),
    extracted_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS document_extraction_notice_idx ON document_extraction (notice_no, field);

------------------------------------------------------------------------
-- E. 판정·점수 (룰 엔진)
------------------------------------------------------------------------

-- 공고별 1차 참가자격 판정 (업체정보 정의서 §8)
CREATE TABLE IF NOT EXISTS company_bid_eligibility (
    company_id            TEXT NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
    notice_no             TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    license_status        TEXT NOT NULL CHECK (license_status IN ('MATCH', 'MISMATCH', 'REVIEW', 'NOT_APPLICABLE')),
    qualification_status  TEXT NOT NULL CHECK (qualification_status IN ('MATCH', 'MISMATCH', 'REVIEW', 'NOT_APPLICABLE')),
    region_status         TEXT NOT NULL CHECK (region_status IN ('MATCH', 'MISMATCH', 'REVIEW', 'NOT_APPLICABLE')),
    license_main_status   TEXT NOT NULL CHECK (license_main_status IN ('MATCH', 'MISMATCH', 'REVIEW', 'NOT_APPLICABLE')),
    overall_status        TEXT NOT NULL CHECK (overall_status IN ('MATCH', 'MISMATCH', 'REVIEW')),
    reason                TEXT,                         -- 불일치 코드, 적용된 상하위 인정 규칙 등
    checked_at            TEXT NOT NULL,
    PRIMARY KEY (company_id, notice_no)
);

-- 적격심사 평가 기준 (분야별 배점표, 입찰가격 산식, 경영상태 평균비율, 해당/인접 사업소 등)
CREATE TABLE IF NOT EXISTS evaluation_rule (
    id              INTEGER PRIMARY KEY,
    notice_no       TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    category        TEXT NOT NULL CHECK (category IN
                        ('PRICE', 'PERFORMANCE', 'MANAGEMENT', 'OFFICE', 'BONUS', 'PENALTY', 'PASS_SCORE')),
    item_name       TEXT NOT NULL,                      -- 예: 입찰가격, 시공경험, 부채비율, 해당사업소
    max_score       REAL,
    formula         TEXT,                               -- 수식 스크립트 또는 정규화 산식
    parameters      TEXT,                               -- JSON (상수·계수·특례 구간·업종 평균비율·사업소 목록 등)
    rounding_rule   TEXT,
    source_location TEXT,
    verified        INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evaluation_rule_notice_idx ON evaluation_rule (notice_no, category);

-- 업체의 점수 시뮬레이션 입력·결과 (입찰가격·예정가격은 사용자 입력)
CREATE TABLE IF NOT EXISTS company_evaluation_draft (
    company_id          TEXT NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
    notice_no           TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    inputs              TEXT NOT NULL DEFAULT '{}',     -- JSON (입찰가격, 예정가격, 실적누계액, 재무비율 ...)
    item_scores         TEXT NOT NULL DEFAULT '{}',     -- JSON (항목별 점수, 미산정 항목은 null)
    total_score         REAL,                           -- 모든 필수 항목 산정 시에만
    calculation_status  TEXT NOT NULL DEFAULT 'IN_PROGRESS'
                            CHECK (calculation_status IN ('IN_PROGRESS', 'CALCULATED', 'ON_HOLD')),
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (company_id, notice_no)
);

------------------------------------------------------------------------
-- F. 필요서류·사용자 작업·알림
------------------------------------------------------------------------

-- 공고별 필요서류 (공고문 + 적격심사기준에서 추출)
CREATE TABLE IF NOT EXISTS required_document_item (
    id                  INTEGER PRIMARY KEY,
    notice_no           TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    stage               TEXT NOT NULL CHECK (stage IN
                            ('BASIC',          -- 1) 입찰참가신청 및 기본서류
                             'ELIGIBILITY',    -- 2) 입찰참가자격 증명 서류
                             'QUALIFICATION',  -- 3) 적격심사서류
                             'SUPERVISOR')),   -- 라) 감독부서 제출서류(인원장비 서류심사)
    submit_to           TEXT NOT NULL DEFAULT 'CONTRACT_DEPT' CHECK (submit_to IN ('CONTRACT_DEPT', 'SUPERVISOR_DEPT')),
    position            INTEGER NOT NULL,
    name                TEXT NOT NULL,
    requirement_type    TEXT NOT NULL CHECK (requirement_type IN ('REQUIRED', 'CONDITIONAL', 'OPTIONAL')),
    alternative_group   TEXT,                           -- '택1' 묶음 (예: 동종실적 vs 시공인증) 같은 값끼리 하나만 충족
    condition_note      TEXT,                           -- 예: 가점 필요시, 1억 이상일 시, 원본대조필 필수
    validity_note       TEXT,                           -- 예: 입찰공고일 기준 최근 2년간
    source_attachment_id INTEGER REFERENCES bid_attachment(id) ON DELETE SET NULL,
    source_location     TEXT,
    verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
                            CHECK (verification_status IN ('UNVERIFIED', 'VERIFIED', 'REVIEW')),
    UNIQUE (notice_no, stage, position)
);

-- 공고유형별 표준 제출서류 목록 (팀 정리본 1~7번). 공고별 추출 전 참고/비교용
CREATE TABLE IF NOT EXISTS document_template_item (
    id                 INTEGER PRIMARY KEY,
    work_type          TEXT NOT NULL,                   -- 예: 배전공사 총액계약
    price_min          INTEGER,                         -- 추정가격 구간(VAT 제외), NULL = 하한 없음
    price_max          INTEGER,                         -- 미만, NULL = 상한 없음
    stage              TEXT NOT NULL CHECK (stage IN ('BASIC', 'ELIGIBILITY', 'QUALIFICATION', 'SUPERVISOR')),
    submit_to          TEXT NOT NULL DEFAULT 'CONTRACT_DEPT' CHECK (submit_to IN ('CONTRACT_DEPT', 'SUPERVISOR_DEPT')),
    position           INTEGER NOT NULL,
    name               TEXT NOT NULL,
    requirement_type   TEXT NOT NULL CHECK (requirement_type IN ('REQUIRED', 'CONDITIONAL', 'OPTIONAL')),
    alternative_group  TEXT,
    condition_note     TEXT,
    reference_notice_no TEXT                            -- 대표 공고번호
);

-- 업체별 서류 준비 체크
CREATE TABLE IF NOT EXISTS company_document_check (
    company_id   TEXT NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
    item_id      INTEGER NOT NULL REFERENCES required_document_item(id) ON DELETE CASCADE,
    status       TEXT NOT NULL CHECK (status IN ('READY', 'NOT_READY', 'NOT_APPLICABLE', 'REVIEW')),
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (company_id, item_id)
);

-- 관심공고 (별표)
CREATE TABLE IF NOT EXISTS favorite_notice (
    company_id  TEXT NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
    notice_no   TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (company_id, notice_no)
);

-- 달력 일정. 적격심사서류 제출기한은 통보서 접수일로부터 5일 이상 지정(적격심사기준 제8조 ②)이라
-- 개찰 전에는 확정할 수 없음 → confirmed=0 인 참고 일정(개찰일+7일)으로 표시
CREATE TABLE IF NOT EXISTS notice_schedule_event (
    id          INTEGER PRIMARY KEY,
    notice_no   TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    event_type  TEXT NOT NULL CHECK (event_type IN
                    ('ATTEND_REQ_BEGIN', 'ATTEND_REQ_CLOSE', 'BID_BEGIN', 'BID_END', 'OPEN_BID',
                     'QUALIFICATION_DOC_DUE', 'FIELD_INTRO')),
    event_at    TEXT,
    source      TEXT NOT NULL CHECK (source IN ('OPEN_API', 'SRM', 'DOCUMENT', 'ESTIMATED')),
    confirmed   INTEGER NOT NULL DEFAULT 1 CHECK (confirmed IN (0, 1)),
    note        TEXT,
    UNIQUE (notice_no, event_type)
);

-- 알림 발송 이력 (중복 발송 방지)
CREATE TABLE IF NOT EXISTS notice_notification (
    id           INTEGER PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
    notice_no    TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    channel      TEXT NOT NULL CHECK (channel IN ('EMAIL', 'TELEGRAM')),
    reason       TEXT NOT NULL DEFAULT 'NEW' CHECK (reason IN ('NEW', 'CORRECTED', 'CANCELLED')),
    send_status  TEXT NOT NULL CHECK (send_status IN ('PENDING', 'SENT', 'FAILED')),
    error        TEXT,
    sent_at      TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (company_id, notice_no, channel, reason)
);

------------------------------------------------------------------------
-- 화면용 조회 뷰
------------------------------------------------------------------------

-- 추천 목록 후보: 진행 중인 공사용역 공고 + 판정 결과 (MISMATCH 제외는 화면/API에서)
-- Open API progressState 는 마감 후에도 'AttendProgress'로 남는 경우가 있어(E012607925 확인)
-- 입찰참가신청 마감 일시('YYYY-MM-DD HH:MM:SS', KST)도 함께 확인한다.
CREATE VIEW IF NOT EXISTS v_recommendation AS
SELECT e.company_id, n.notice_no, n.title, n.place_name, n.work_type,
       n.attend_req_close_at, n.bid_end_at, n.presumed_price,
       e.overall_status, e.reason,
       EXISTS (SELECT 1 FROM favorite_notice f
               WHERE f.company_id = e.company_id AND f.notice_no = n.notice_no) AS is_favorite
FROM bid_notice n
JOIN company_bid_eligibility e ON e.notice_no = n.notice_no
WHERE n.purchase_type = 'ConstructionService' AND n.company_id_src = 'COM01' AND n.is_open = 1
  AND (n.attend_req_close_at IS NULL OR n.attend_req_close_at >= datetime('now', 'localtime'));
