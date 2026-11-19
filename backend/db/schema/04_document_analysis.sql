-- 04. AI 문서 분석 결과
-- 쓰는 모듈: 팀 결정 필요 — AI 파트(ai/)가 직접 저장할지, 백엔드 API/룰 엔진이 검증 후 저장할지 미정. 현재 코드 없음
-- 데이터 출처: AI 파트(LLM)가 bid_document_content(03)의 원문 텍스트·수식을 읽어 추출
-- 읽는 모듈: (예정) 공고 상세 요약 탭 API, (예정) 룰 엔진
-- 의존: 03_bid_collection.sql
-- 이 파일은 저장 구조만 정의한다. field 이름·value JSON 형식은 AI 파트와 협의.

-- 공고별 분석 상태: 신규 공고 1회 분석·재사용, 정정 시 무효화 후 재분석
CREATE TABLE IF NOT EXISTS notice_analysis_cache (
    notice_no         TEXT PRIMARY KEY REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    document_version  TEXT,                             -- 분석에 쓴 첨부 sha256 조합
    analysis_status   TEXT NOT NULL CHECK (analysis_status IN ('PENDING', 'DONE', 'FAILED', 'INVALIDATED')),
    analyzed_at       TEXT,
    invalidated_at    TEXT
);

-- 문서 추출값 (field 예: eligibility, eligibility_docs, office_target, industry_avg_debt_ratio ...)
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
