-- 04. AI 문서 분석 결과
-- 쓰는 모듈: ai/notice_extraction.py (제출서류 처리 상태), 수집기 (정정 시 무효화)
-- 데이터 출처: AI 파트(LLM)가 bid_document_content(03)의 원문 텍스트·수식을 읽어 추출
-- 읽는 모듈: (예정) 공고 상세 요약 탭 API, (예정) 룰 엔진
-- 의존: 03_bid_collection.sql
-- 이 파일은 저장 구조만 정의한다. field 이름·value JSON 형식은 AI 파트와 협의.

-- 공고별 분석 상태: 신규 공고 1회 분석·재사용, 정정 시 무효화 후 재분석
CREATE TABLE IF NOT EXISTS notice_analysis_cache (
    notice_no         TEXT PRIMARY KEY REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    document_version  TEXT,                             -- 분석에 쓴 첨부 및 전처리 본문 버전
    analysis_status   TEXT NOT NULL CHECK (analysis_status IN ('PENDING', 'DONE', 'FAILED', 'INVALIDATED')),
    analyzed_at       TEXT,
    invalidated_at    TEXT
);

-- 문서 추출값 표(document_extraction)는 2026-11-21 뺐다. 결과를 담는 표(evaluation_rule, required_document_item)가
-- 각자 근거 문서(첨부 id)·위치·문구를 갖고, 문서명은 bid_attachment 와 join 해서 얻는다.
