-- SRM 공고 수집용 테이블 (기능명세서 §5의 bid_notice / bid_restriction_* / bid_attachment 기준)
-- 기존 backend/db/schema.sql(P0 데모 스키마)과 별도 파일. 같은 DB에 함께 적용해도 테이블 이름이 겹치지 않는다.
-- LLM 요약·추출 결과(document_extraction 등)는 AI 파트 영역이라 여기서 정의하지 않는다.
PRAGMA foreign_keys = ON;

-- 공고 기본정보 (FUNC-005, Open API electContract.do)
CREATE TABLE IF NOT EXISTS bid_notice (
    notice_no                     TEXT PRIMARY KEY,            -- 공고번호 (예: E012607925)
    bid_id                        TEXT,                        -- SRM 내부 ID (getPageList로 확인)
    title                         TEXT NOT NULL,
    purchase_type                 TEXT,                        -- Product / ConstructionService
    item_type                     TEXT,                        -- Construction / Service
    bid_type                      TEXT,                        -- QualifiedEval 등 낙찰방법
    competition_type              TEXT,
    place_name                    TEXT,                        -- 발주기관
    notice_date                   TEXT,                        -- 'YYYYMMDD'
    attend_req_close_at           TEXT,                        -- 입찰참가신청 마감 (KST 문자열 그대로)
    bid_begin_at                  TEXT,
    bid_end_at                    TEXT,
    progress_state                TEXT,
    presumed_price                INTEGER,                     -- 추정가격 (VAT 제외)
    estimated_price_basic_amount  INTEGER,                     -- 예비가격기초금액 (VAT 포함)
    presumed_amount               INTEGER,                     -- 추정금액
    notice_doc_found              INTEGER CHECK (notice_doc_found IN (0, 1)),   -- 공고문 확보 여부 (NULL=미확인)
    criteria_doc_found            INTEGER CHECK (criteria_doc_found IN (0, 1)), -- 적격심사기준 확보 여부
    api_raw                       TEXT NOT NULL,               -- Open API 응답 원본(JSON)
    first_seen_at                 TEXT NOT NULL,
    updated_at                    TEXT NOT NULL
);

-- 투찰제한 자동심사 설정 (FUNC-006, SRM findBidBasicInfo)
CREATE TABLE IF NOT EXISTS bid_restriction_snapshot (
    notice_no                     TEXT PRIMARY KEY REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    fetch_status                  TEXT NOT NULL CHECK (fetch_status IN ('SUCCESS', 'PARTIAL', 'FAILED')),
    error                         TEXT,
    license_auto_eval_method      TEXT,                        -- Auto / Manual / None
    license_auto_eval_type        TEXT,                        -- And / Or
    qualified_auto_eval_method    TEXT,
    qualified_auto_eval_type      TEXT,
    region_limit_auto_eval_method TEXT,
    region_limit_auto_eval_type   TEXT,
    license_main_auto_eval_type   TEXT,
    limited_reason_code           TEXT,
    raw_response                  TEXT,                        -- 담당자 이름·사번 등 개인정보 필드는 제거 후 저장
    fetched_at                    TEXT NOT NULL
);

-- 범주별 조회 결과: '조회 성공 + 빈 목록(제한 없음)'과 '조회 실패'를 구분 (명세 §8)
CREATE TABLE IF NOT EXISTS bid_restriction_category (
    notice_no     TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    category      TEXT NOT NULL CHECK (category IN ('LICENSE', 'QUALIFIED', 'REGION', 'LICENSE_MAIN')),
    fetch_status  TEXT NOT NULL CHECK (fetch_status IN ('SUCCESS', 'FAILED')),
    item_count    INTEGER,
    error         TEXT,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (notice_no, category)
);

-- 공고가 요구하는 면허·제한경쟁입찰자격·지역·주력분야 (FUNC-007)
CREATE TABLE IF NOT EXISTS bid_restriction_item (
    id             INTEGER PRIMARY KEY,
    notice_no      TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    category       TEXT NOT NULL CHECK (category IN ('LICENSE', 'QUALIFIED', 'REGION', 'LICENSE_MAIN')),
    code           TEXT,                                       -- 면허/자격/주력분야 코드 (예: A06), 지역은 area_code
    name           TEXT,                                       -- 면허자격명/적격자격명/지역명
    area_code      TEXT,                                       -- REGION: 광역 그룹 코드 (예: I)
    sub_area_code  TEXT,                                       -- REGION: 시군구 코드 (NULL = 광역 전체), 선행 0 보존
    sub_area_name  TEXT
);
CREATE INDEX IF NOT EXISTS bid_restriction_item_notice_idx ON bid_restriction_item (notice_no, category);

-- 공고문·적격심사기준 첨부 (FUNC-008, Open API 우선 → 누락 시 SRM 보완)
CREATE TABLE IF NOT EXISTS bid_attachment (
    id               INTEGER PRIMARY KEY,
    notice_no        TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    doc_kind         TEXT NOT NULL CHECK (doc_kind IN ('NOTICE', 'CRITERIA')),  -- 공고문 / 적격심사기준
    filename         TEXT NOT NULL,
    file_ext         TEXT,
    source           TEXT NOT NULL CHECK (source IN ('OPEN_API', 'SRM_FALLBACK')),
    download_status  TEXT NOT NULL CHECK (download_status IN ('SUCCESS', 'FAILED')),
    local_path       TEXT,
    size_bytes       INTEGER,
    sha256           TEXT,
    error            TEXT,
    fetched_at       TEXT NOT NULL,
    UNIQUE (notice_no, filename)
);

-- 첨부 원문 읽기 결과: LLM 요약(AI 파트)이 입력으로 읽어가는 텍스트와 수식 스크립트
CREATE TABLE IF NOT EXISTS bid_document_content (
    attachment_id    INTEGER PRIMARY KEY REFERENCES bid_attachment(id) ON DELETE CASCADE,
    read_status      TEXT NOT NULL CHECK (read_status IN ('SUCCESS', 'FAILED')),
    text             TEXT,                                     -- 본문 문단(줄바꿈 구분)
    equations        TEXT,                                     -- JSON: [{"script": "...", "context": [...]}] (한글 수식 개체)
    paragraph_count  INTEGER,
    equation_count   INTEGER,
    error            TEXT,
    read_at          TEXT NOT NULL
);

-- 수집 실행 이력
CREATE TABLE IF NOT EXISTS collect_run (
    id               INTEGER PRIMARY KEY,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    api_notice_count INTEGER,
    target_count     INTEGER,
    processed_count  INTEGER,
    stopped_reason   TEXT
);
