-- 03. 공고·첨부·수집 이력
-- 쓰는 모듈: backend/collector/collect.py (Open API electContract.do + SRM /router)
-- 읽는 모듈: (예정) 추천 목록·공고 상세 API, (예정) 룰 엔진, (예정) AI 문서 분석(bid_document_content 입력)
-- 의존: 없음 (업체 테이블을 참조하지 않음)
-- 서비스 범위: 한전(COM01) 공사용역(ConstructionService) 중 낙찰방법 적격심사(QualifiedEval) 공고만 저장

-- 공고 기본정보
CREATE TABLE IF NOT EXISTS bid_notice (
    notice_no                     TEXT PRIMARY KEY,     -- 공고번호 (예: E012607925)
    bid_id                        TEXT,                 -- SRM 내부 ID
    company_id_src                TEXT NOT NULL DEFAULT 'COM01',  -- 발주사 구분(API companyId). 이름 변경 제안 있음(README)
    title                         TEXT NOT NULL,
    purchase_type                 TEXT,                 -- ConstructionService
    item_type                     TEXT,                 -- Construction / Service
    bid_type                      TEXT,                 -- QualifiedEval (낙찰방법)
    competition_type              TEXT,
    work_type                     TEXT,                 -- 공사유형 분류 (분류 규칙 예정)
    place_name                    TEXT,                 -- 발주기관
    notice_date                   TEXT,
    attend_req_begin_at           TEXT,                 -- 입찰참가신청 시작
    attend_req_close_at           TEXT,                 -- 입찰참가신청 마감 ('YYYY-MM-DD HH:MM:SS', KST)
    bid_begin_at                  TEXT,                 -- 투찰 시작
    bid_end_at                    TEXT,                 -- 투찰 마감
    open_bid_at                   TEXT,                 -- 개찰 일시 (SRM)
    progress_state                TEXT,                 -- 마감 후에도 AttendProgress 로 남는 경우 많음 → 마감 일시와 함께 판단
    is_open                       INTEGER GENERATED ALWAYS AS
                                      (progress_state IN ('PreAttendProgress', 'AttendProgress')) VIRTUAL,
    presumed_price                INTEGER,              -- 추정가격(VAT 제외): 적격심사기준 금액구간, 실적 요건
    estimated_price_basic_amount  INTEGER,              -- 예비가격 기초금액(VAT 포함)
    approximate_amount            INTEGER,              -- 사정액(=사정금액, API approximateAmount): 시공경험 평가
    presumed_amount               INTEGER,              -- 추정금액
    a_amount                      INTEGER,              -- A금액 (SRM totalAAmt)
    net_construction_cost         INTEGER,              -- 순공사원가(재료비+노무비+경비). SRM에 값 없음 → NULL=미확인 (공고문 금액표 추출 방식은 팀 결정)
    overhead_cost_amount          INTEGER,              -- 경비 (SRM overHeadCostAmount = 공고문 금액표 '경비' 행)
    notice_doc_found              INTEGER CHECK (notice_doc_found IN (0, 1)),    -- 공고문 확보 (NULL=미확인)
    criteria_doc_found            INTEGER CHECK (criteria_doc_found IN (0, 1)),  -- 적격심사기준 확보
    api_raw                       TEXT NOT NULL,        -- Open API 응답 원본(JSON)
    -- 정정 공고 감지 (init_db.py 마이그레이션이 기존 DB에도 같은 컬럼을 추가함)
    api_created_at                TEXT,                 -- Open API createDatetime. 정정되면 더 늦은 값이 새로 옴
    srm_revision                  TEXT,                 -- SRM 정정 차수 (findBidBasicInfo, 수집 시점 기록)
    needs_recollect               INTEGER NOT NULL DEFAULT 0 CHECK (needs_recollect IN (0, 1)),
    recollect_reason              TEXT,
    last_collected_at             TEXT,                 -- 투찰제한·첨부를 마지막으로 수집한 시각
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
    code           TEXT,                                -- 면허/자격/주력분야 코드, 지역은 area_code 와 동일
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

-- 첨부 원문 (전처리한 본문 + 한글 수식 스크립트). 해석 없이 읽기만 함 → AI 문서 분석의 입력
-- 본문 형식은 backend/collector/doc_reader.py 맨 위 설명 (표는 '칸1 | 칸2' 행, 수식·그림은 나온 자리에 표시)
CREATE TABLE IF NOT EXISTS bid_document_content (
    attachment_id    INTEGER PRIMARY KEY REFERENCES bid_attachment(id) ON DELETE CASCADE,
    read_status      TEXT NOT NULL CHECK (read_status IN ('SUCCESS', 'FAILED')),
    text             TEXT,
    equations        TEXT,                              -- JSON [{"script", "line"(본문 줄 번호), "context"}]
    paragraph_count  INTEGER,                           -- 본문 줄 수
    equation_count   INTEGER,
    error            TEXT,
    read_at          TEXT NOT NULL,
    parser_version   TEXT,                              -- doc_reader.PARSER_VERSION (NULL = 전처리 전 v1)
    -- 적격심사기준(CRITERIA)에서 평가산식을 규칙 파서로 뽑았는지: 1 = 성공(evaluation_rule 에 행 있음),
    -- 0 = 실패(PDF·zip·그림, 기준서·공고 불일치, 규칙으로 못 읽는 항목) → 배점 행은 비워 두고 LLM이 본문을 직접 읽는다.
    -- NULL = 판단 대상 아님(공고문 등) 또는 아직 안 돌림. backend/db/extract_evaluation_rules.py 가 채운다
    rule_extracted   INTEGER CHECK (rule_extracted IN (0, 1))
);

CREATE TABLE IF NOT EXISTS collect_run (
    id               INTEGER PRIMARY KEY,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    api_notice_count INTEGER,
    target_count     INTEGER,
    processed_count  INTEGER,
    stopped_reason   TEXT,
    -- 실행 결과 건수 (init_db.py 마이그레이션이 기존 DB에도 추가)
    new_notice_count INTEGER,             -- 이번 실행에서 bid_notice 에 처음 저장된 공고 (알림 연결 지점)
    completed_count  INTEGER,             -- 상세 수집(투찰제한·문서)을 끝낸 공고
    deferred_count   INTEGER,             -- 미처리: 한도·SRM 차단·중단으로 이번에 못 한 상세 수집 (다음 실행에서 이어서)
    failed_count     INTEGER,             -- 처리했지만 실패·부분 수집으로 끝난 공고 (다음 실행에서 재시도)
    mode             TEXT,                -- full(최초 적재) / daily(증분)
    window_begin     TEXT,                -- 공식 API 조회 공고일 범위 (YYYYMMDD)
    window_end       TEXT,
    list_synced      INTEGER,             -- 1 = 조회 범위 전체를 정상 반영 → 다음 daily 의 기준 시점
    updated_count    INTEGER,             -- 기본정보만 바뀐 공고 (상세 재사용)
    corrected_count  INTEGER,             -- 정정·첨부 변경 → 상세 재수집 표시
    rule_checked_count   INTEGER,         -- 이번 실행에서 적격심사기준 본문으로 배점 규칙을 돌린 공고
    rule_extracted_count INTEGER          -- 그중 규칙으로 다 뽑은 공고 (rule_extracted=1). 비율이 떨어지면 새 문장 표현
);
