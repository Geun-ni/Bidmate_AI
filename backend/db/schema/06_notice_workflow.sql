-- 06. 필요서류·즐겨찾기·일정·알림
-- 쓰는 모듈:
--   required_document_item  → 팀 결정 필요 (AI 추출 결과를 AI 파트가 직접 저장 vs 백엔드가 검증 후 저장)
--   document_template_item  → (예정) 시드 스크립트 (팀 정리본 1~7번)
--   company_document_check  → (예정) 서류 체크 API ← 프론트 필요서류 탭(UI-05). 현재 프론트는 브라우저 메모리(Map)에만 보관
--   favorite_notice         → (예정) 관심공고 API ← 프론트 별표 토글(UI-01·06)
--   notice_schedule_event   → (예정) 수집기 또는 백엔드 (API 일정 + SRM 개찰일시 + 문서 추출 제출기한) — 팀 결정 필요
--   notice_notification     → (예정) 알림 발송 모듈 — 담당 팀 결정 필요 (기획서상 알림 연동은 AI 팀)
-- 읽는 모듈: (예정) 필요서류 탭·달력·추천 목록 API, 알림 발송
-- 의존: 02_company.sql, 03_bid_collection.sql

-- 공고별 필요서류 (공고문 + 적격심사기준에서 추출)
CREATE TABLE IF NOT EXISTS required_document_item (
    id                   INTEGER PRIMARY KEY,
    notice_no            TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    stage                TEXT NOT NULL CHECK (stage IN
                             ('BASIC',          -- 1) 입찰참가신청 및 기본서류
                              'ELIGIBILITY',    -- 2) 입찰참가자격 증명 서류
                              'QUALIFICATION',  -- 3) 적격심사서류
                              'SUPERVISOR')),   -- 라) 감독부서 제출서류(인원장비 서류심사)
    submit_to            TEXT NOT NULL DEFAULT 'CONTRACT_DEPT' CHECK (submit_to IN ('CONTRACT_DEPT', 'SUPERVISOR_DEPT')),
    position             INTEGER NOT NULL,
    name                 TEXT NOT NULL,
    requirement_type     TEXT NOT NULL CHECK (requirement_type IN ('REQUIRED', 'CONDITIONAL', 'OPTIONAL')),
    alternative_group    TEXT,                          -- '택1' 묶음: 같은 값끼리 하나만 충족하면 됨
    condition_note       TEXT,                          -- 예: 가점 필요시, 1억 이상일 시, 원본대조필 필수
    validity_note        TEXT,                          -- 예: 입찰공고일 기준 최근 2년간
    source_attachment_id INTEGER REFERENCES bid_attachment(id) ON DELETE SET NULL,
    source_location      TEXT,
    verification_status  TEXT NOT NULL DEFAULT 'UNVERIFIED'
                             CHECK (verification_status IN ('UNVERIFIED', 'VERIFIED', 'REVIEW')),
    UNIQUE (notice_no, stage, position)
);

-- 공고유형별 표준 제출서류 (팀 정리본 1~7번). 공고별 추출 전 참고/비교용
CREATE TABLE IF NOT EXISTS document_template_item (
    id                  INTEGER PRIMARY KEY,
    work_type           TEXT NOT NULL,                  -- 예: 배전공사 총액계약
    price_min           INTEGER,                        -- 추정가격 구간(VAT 제외), NULL = 하한 없음
    price_max           INTEGER,                        -- 미만, NULL = 상한 없음
    stage               TEXT NOT NULL CHECK (stage IN ('BASIC', 'ELIGIBILITY', 'QUALIFICATION', 'SUPERVISOR')),
    submit_to           TEXT NOT NULL DEFAULT 'CONTRACT_DEPT' CHECK (submit_to IN ('CONTRACT_DEPT', 'SUPERVISOR_DEPT')),
    position            INTEGER NOT NULL,
    name                TEXT NOT NULL,
    requirement_type    TEXT NOT NULL CHECK (requirement_type IN ('REQUIRED', 'CONDITIONAL', 'OPTIONAL')),
    alternative_group   TEXT,
    condition_note      TEXT,
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
-- 개찰 전에는 확정 불가 → confirmed=0 인 참고 일정(개찰일+7일)으로 표시
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
