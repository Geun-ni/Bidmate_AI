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
