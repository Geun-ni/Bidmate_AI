PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS staging_notice (
    notice_no TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS staging_source (
    attachment_id INTEGER PRIMARY KEY,
    notice_no TEXT NOT NULL REFERENCES staging_notice(notice_no),
    filename TEXT NOT NULL,
    doc_kind TEXT NOT NULL,
    source_sha256 TEXT,
    text_sha256 TEXT NOT NULL,
    read_status TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS required_document_item (
    id INTEGER PRIMARY KEY,
    notice_no TEXT NOT NULL REFERENCES staging_notice(notice_no),
    stage TEXT NOT NULL CHECK(stage IN ('BASIC','ELIGIBILITY','QUALIFICATION','SUPERVISOR')),
    submit_to TEXT NOT NULL CHECK(submit_to IN ('CONTRACT_DEPT','SUPERVISOR_DEPT')),
    position INTEGER NOT NULL,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    requirement_type TEXT NOT NULL CHECK(requirement_type IN ('REQUIRED','CONDITIONAL','OPTIONAL')),
    alternative_group TEXT,
    condition_note TEXT,
    validity_note TEXT,
    source_attachment_id INTEGER NOT NULL REFERENCES staging_source(attachment_id),
    source_location TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK(verification_status IN ('UNVERIFIED','VERIFIED','REVIEW')),
    UNIQUE(notice_no,stage,position)
);
CREATE TABLE IF NOT EXISTS staging_evidence (
    item_id INTEGER PRIMARY KEY REFERENCES required_document_item(id),
    source_quote TEXT NOT NULL,
    context TEXT NOT NULL,
    source_sha256 TEXT,
    text_sha256 TEXT NOT NULL,
    extractor_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notice_analysis_cache (
    notice_no TEXT PRIMARY KEY REFERENCES staging_notice(notice_no),
    document_version TEXT,
    analysis_status TEXT NOT NULL CHECK(analysis_status IN ('PENDING','DONE','FAILED','INVALIDATED')),
    analyzed_at TEXT,
    invalidated_at TEXT
);
