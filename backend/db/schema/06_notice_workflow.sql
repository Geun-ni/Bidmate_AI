-- 06. 필요서류·즐겨찾기·일정·알림
-- 쓰는 모듈:
--   required_document_item  → LLM 추출 저장 (칸 형식은 AI 파트와 확정)
--   company_document_check  → (예정) 서류 체크 API ← 프론트 필요서류 탭(UI-05). 자동 심사 입력
--   favorite_notice         → backend/favorites.py (관심공고 API) ← 프론트 별표 토글(UI-01·06), 사용자 개인별
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
    source_quote         TEXT,
    source_sha256        TEXT,
    updated_by           TEXT CHECK (updated_by IN ('PARSER', 'LLM', 'USER')),
    verification_status  TEXT NOT NULL DEFAULT 'UNVERIFIED'
                             CHECK (verification_status IN ('UNVERIFIED', 'VERIFIED', 'REVIEW')),
    UNIQUE (notice_no, stage, position)
);

-- 표준 제출서류 목록(document_template_item)은 2026-11-21 뺐다 (공고별 필요서류는 LLM 추출로 대체)

-- 업체별 서류 준비 체크 (자동 심사 입력). 발급일은 사용자가 선택 입력, 비면 유효기간 판단은 '확인 필요'
-- 같은 업체가 다른 공고에서 입력한 같은 서류의 발급일은 API가 다시 보여 준다
CREATE TABLE IF NOT EXISTS company_document_check (
    company_id   TEXT NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
    item_id      INTEGER NOT NULL REFERENCES required_document_item(id) ON DELETE CASCADE,
    status       TEXT NOT NULL CHECK (status IN ('READY', 'NOT_READY', 'NOT_APPLICABLE', 'REVIEW')),
    issued_at    TEXT,                                   -- 서류 발급일 YYYY-MM-DD (선택)
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (company_id, item_id)
);

-- 관심공고 (별표) — 사용자 개인별. 캘린더도 내 관심공고만 보여 준다
CREATE TABLE IF NOT EXISTS favorite_notice (
    user_id     TEXT NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    notice_no   TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, notice_no)
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

-- 알림 발송 이력 (중복 발송 방지) — 수신 설정이 사용자 단위라 이력도 사용자 단위.
-- company_id 는 어느 소속 업체의 맞춤 판정으로 보냈는지 기록 (업체 삭제 시 이력은 남김)
CREATE TABLE IF NOT EXISTS notice_notification (
    id           INTEGER PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    company_id   TEXT REFERENCES company(company_id) ON DELETE SET NULL,
    notice_no    TEXT NOT NULL REFERENCES bid_notice(notice_no) ON DELETE CASCADE,
    channel      TEXT NOT NULL CHECK (channel IN ('EMAIL', 'TELEGRAM')),
    reason       TEXT NOT NULL DEFAULT 'NEW' CHECK (reason IN ('NEW', 'CORRECTED', 'CANCELLED')),
    send_status  TEXT NOT NULL CHECK (send_status IN ('PENDING', 'SENT', 'FAILED')),
    error        TEXT,
    sent_at      TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (user_id, notice_no, channel, reason)
);

-- 베타 의견 (POST /api/feedback): general=의견 보내기, reaction=👍👎, survey=베타 설문.
-- user_id 는 로그인 세션 기준 (화면이 보낸 context 의 user_id 는 믿지 않는다). 첨부 이미지는 원본 바이트(최대 3MB)
CREATE TABLE IF NOT EXISTS beta_feedback (
    id               INTEGER PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ('general', 'reaction', 'survey')),
    user_id          TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,
    notice_no        TEXT,                  -- context.notice_no (공고가 지워져도 의견은 남긴다)
    category         TEXT CHECK (category IN ('BUG', 'INCONVENIENT', 'IDEA', 'GOOD')),
    target           TEXT CHECK (target IN ('judgment', 'score', 'qna')),
    ref_id           TEXT,
    value            TEXT CHECK (value IN ('up', 'down')),
    message          TEXT,                  -- general 내용 / reaction 이유 한 줄
    email            TEXT,
    payload          TEXT,                  -- survey answers 또는 reaction extra (JSON)
    context          TEXT,                  -- 보던 화면·브라우저 등 (JSON)
    attachment_name  TEXT,
    attachment_type  TEXT,
    attachment_data  BLOB,
    created_at       TEXT NOT NULL
);
