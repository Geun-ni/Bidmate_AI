-- 05. 자격 판정·점수 계산
-- 쓰는 모듈:
--   company_bid_eligibility  → (예정) 룰 엔진. 업체 매칭은 현재 범위 밖(11-19 결정: 조건 확인까지만)
--   evaluation_rule          → 팀 결정 필요 (AI 추출값을 AI 파트가 직접 저장 vs 룰 엔진이 검증 후 저장)
--   company_evaluation_draft → (예정) 점수 계산 API ← 프론트 점수 계산 탭(UI-04)
-- 읽는 모듈: (예정) 추천 목록 API(07 뷰), 점수 계산 탭 API
-- 의존: 02_company.sql, 03_bid_collection.sql

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

-- 적격심사 평가 기준: 행 1개 = 공고 1건의 평가 항목 1개 (분야별 배점, 입찰가격 산식, 구간 점수표, 가·감점, 통과점수)
-- 쓰는 모듈: 정근(배점·산식 추출). 산식이 그림에만 있으면 formula_source='IMAGE' + image_path 로 남기고,
--   LLM이 그림을 읽어 formula 를 채운 뒤 updated_by='LLM' 으로 고친다 (검증 전까지 verification_status='REVIEW').
-- parameters JSON 형식 (rule_type 별):
--   FORMULA   {"vars": {"A": "a_amount", "기준율": 88}}
--   TABLE     {"rows": [{"min": null, "max": 50, "score": 7}, ...]} 또는 {"rows": [{"grade": "AAA", "score": 15}, ...]}
--   FIXED     {"score": 1, "condition": "원문 조건 문구"}
--   THRESHOLD {"pass_score": 95}
--   GROUP     하위 항목을 묶는 행 (예: 당해공사 수행능력 30, 시공경험 18). max_score 가 묶음 한도.
--             가감점 묶음은 {"min": -5, "max": 0.1} 처럼 범위를 둔다
-- 근거 위치(source_location)는 본문 줄 번호가 아니라 '별표1 > 제3조 > 표 2행'처럼 문서 구조로 쓴다
-- (재파싱하면 줄 번호가 바뀜). 다시 찾을 때는 source_quote 를 본문에서 검색한다.
CREATE TABLE IF NOT EXISTS evaluation_rule (
    id                  INTEGER PRIMARY KEY,
    notice_no           TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    parent_id           INTEGER REFERENCES evaluation_rule(id) ON DELETE CASCADE,  -- 상위 항목 (최상위는 NULL)
    position            INTEGER NOT NULL,                -- 공고 안 표시 순서
    category            TEXT NOT NULL CHECK (category IN
                            ('PRICE', 'PERFORMANCE', 'MANAGEMENT', 'OFFICE', 'BONUS', 'PENALTY', 'PASS_SCORE')),
    item_name           TEXT NOT NULL,                   -- 원문 항목명 그대로 (예: 시공경험, 부채비율)
    rule_type           TEXT NOT NULL CHECK (rule_type IN ('GROUP', 'FORMULA', 'TABLE', 'FIXED', 'THRESHOLD')),
    max_score           REAL,                            -- 배점(점). 감점은 음수 한도, 통과점수 행은 NULL
    formula             TEXT,                            -- FORMULA: 산식 (예: 50-2×|(90/100)-(입찰가격/예정가격)|×100)
    formula_source      TEXT CHECK (formula_source IN ('EQUATION', 'TEXT', 'IMAGE')),  -- 산식이 어디서 왔는지
    image_path          TEXT,                            -- IMAGE: 꺼낸 그림 파일 (LLM이 읽을 대상)
    parameters          TEXT,                            -- JSON, 형식은 위 설명
    input_key           TEXT,                            -- 업체 입력값 이름 (company_evaluation_draft.inputs 의 키)
    rounding_digits     INTEGER,                         -- 소수점 몇째 자리까지 남기는지. NULL = 원문에 규칙 없음
    rounding_mode       TEXT CHECK (rounding_mode IN ('ROUND', 'FLOOR', 'CEIL')),
    attachment_id       INTEGER REFERENCES bid_attachment(id) ON DELETE SET NULL,  -- 근거 문서
    source_sha256       TEXT,                            -- 추출에 쓴 문서 파일 지문 (정정 후에도 어느 버전인지 확인)
    source_location     TEXT,                            -- 문서 안 위치 (구조 경로)
    source_quote        TEXT,                            -- 해당 문구
    verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
                            CHECK (verification_status IN ('UNVERIFIED', 'VERIFIED', 'REVIEW')),
    updated_by          TEXT NOT NULL DEFAULT 'PARSER' CHECK (updated_by IN ('PARSER', 'LLM', 'USER')),
    extracted_at        TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (notice_no, position)
);

-- 업체가 직접 올린 적격심사기준 (zip 이거나 수집기가 기준서를 못 찾은 공고). 올린 업체만 쓴다 → 공용 evaluation_rule 은 안 건드림.
-- 원본 파일은 저장하지 않고 전처리한 본문과 규칙 추출 결과(JSON)만 남긴다. 업체·공고당 1건 (다시 올리면 덮어씀)
-- 공용 배점(evaluation_rule)이 없는 공고에서만 점수 계산에 쓴다
CREATE TABLE IF NOT EXISTS company_uploaded_criteria (
    company_id       TEXT NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
    notice_no        TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    filename         TEXT NOT NULL,
    file_ext         TEXT,
    size_bytes       INTEGER,
    sha256           TEXT,
    read_status      TEXT NOT NULL CHECK (read_status IN ('SUCCESS', 'FAILED')),
    text             TEXT,                             -- 전처리한 본문 (bid_document_content.text 와 같은 형식)
    error            TEXT,                             -- 본문을 못 읽은 이유
    rule_extracted   INTEGER CHECK (rule_extracted IN (0, 1)),  -- 1 = 규칙으로 다 뽑음, 0 = 못 뽑음(LLM 대상)
    rules            TEXT NOT NULL DEFAULT '[]',       -- JSON: evaluation_rule 행과 같은 모양의 목록 (parent = 목록 번호)
    issues           TEXT NOT NULL DEFAULT '[]',       -- JSON: 규칙 추출에서 막힌 이유
    rules_updated_by TEXT NOT NULL DEFAULT 'PARSER' CHECK (rules_updated_by IN ('PARSER', 'LLM', 'USER')),
    uploaded_by      TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,
    uploaded_at      TEXT NOT NULL,
    PRIMARY KEY (company_id, notice_no)
);

-- 업체의 점수 시뮬레이션 입력·결과 (입찰가격·예정가격은 사용자 입력). 업체·공고당 직전 1건만 (다시 하면 덮어씀)
-- Q&A 봇이 '저번 시뮬레이션 결과'를 답할 때도 이 행을 쓴다 → 요청 사용자의 ACTIVE 소속 업체 행만 내준다
CREATE TABLE IF NOT EXISTS company_evaluation_draft (
    company_id          TEXT NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
    notice_no           TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    inputs              TEXT NOT NULL DEFAULT '{}',     -- JSON (입찰가격, 예정가격, 실적누계액, 재무비율 ...)
    item_scores         TEXT NOT NULL DEFAULT '{}',     -- JSON (항목별 점수, 미산정 항목은 null)
    total_score         REAL,                           -- 모든 필수 항목 산정 시에만
    calculation_status  TEXT NOT NULL DEFAULT 'IN_PROGRESS'
                            CHECK (calculation_status IN ('IN_PROGRESS', 'CALCULATED', 'ON_HOLD')),
    updated_by          TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,  -- 마지막으로 돌린 사용자
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (company_id, notice_no)
);
