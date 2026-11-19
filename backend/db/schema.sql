PRAGMA foreign_keys = ON;

BEGIN IMMEDIATE;

CREATE TABLE procurement_sources (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE CHECK (length(trim(code)) > 0),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0)
);

CREATE TABLE notices (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES procurement_sources(id),
    external_notice_id TEXT NOT NULL CHECK (length(trim(external_notice_id)) > 0),
    UNIQUE (source_id, external_notice_id)
);

-- A correction or cancellation is stored as a new revision.
CREATE TABLE notice_revisions (
    id INTEGER PRIMARY KEY,
    notice_id INTEGER NOT NULL REFERENCES notices(id),
    revision_number INTEGER NOT NULL CHECK (typeof(revision_number) = 'integer' AND revision_number >= 0),
    status TEXT NOT NULL CHECK (status IN ('published', 'cancelled')),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    issuer TEXT NOT NULL CHECK (length(trim(issuer)) > 0),
    published_at INTEGER CHECK (typeof(published_at) IN ('null', 'integer')),
    opens_at INTEGER CHECK (typeof(opens_at) IN ('null', 'integer')),
    deadline_at INTEGER CHECK (typeof(deadline_at) IN ('null', 'integer')),
    budget_krw INTEGER CHECK (budget_krw IS NULL OR (typeof(budget_krw) = 'integer' AND budget_krw >= 0)),
    eligibility_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (eligibility_status IN ('pending', 'validated')),
    CHECK (opens_at IS NULL OR deadline_at IS NULL OR opens_at <= deadline_at),
    UNIQUE (notice_id, revision_number)
);

-- Include cancellations so the previous published revision cannot resurface.
CREATE VIEW current_notice_revisions AS
SELECT revision.*
FROM notice_revisions AS revision
JOIN (
    SELECT notice_id, MAX(revision_number) AS revision_number
    FROM notice_revisions
    GROUP BY notice_id
) AS latest USING (notice_id, revision_number);

CREATE INDEX notice_revisions_deadline_idx ON notice_revisions (deadline_at)
WHERE status = 'published';

CREATE TABLE notice_documents (
    id INTEGER PRIMARY KEY,
    revision_id INTEGER NOT NULL REFERENCES notice_revisions(id),
    kind TEXT NOT NULL CHECK (kind IN ('notice', 'attachment')),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    source_url TEXT CHECK (length(trim(source_url)) > 0),
    UNIQUE (revision_id, id)
);

CREATE TABLE notice_citations (
    id INTEGER PRIMARY KEY,
    revision_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    page_number INTEGER CHECK (typeof(page_number) = 'integer' AND page_number > 0),
    section TEXT CHECK (length(trim(section)) > 0),
    excerpt TEXT NOT NULL CHECK (length(trim(excerpt)) > 0),
    CHECK (page_number IS NOT NULL OR section IS NOT NULL),
    FOREIGN KEY (revision_id, document_id) REFERENCES notice_documents(revision_id, id),
    UNIQUE (revision_id, id)
);

CREATE INDEX notice_citations_document_idx ON notice_citations (revision_id, document_id);

CREATE TABLE licenses (
    code TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0)
);

CREATE TABLE regions (
    code TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0)
);

CREATE TABLE document_types (
    code TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0)
);

CREATE TABLE company_profiles (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    region_code TEXT NOT NULL REFERENCES regions(code),
    min_budget_krw INTEGER NOT NULL CHECK (typeof(min_budget_krw) = 'integer' AND min_budget_krw >= 0),
    max_budget_krw INTEGER NOT NULL CHECK (typeof(max_budget_krw) = 'integer' AND max_budget_krw >= min_budget_krw)
);

CREATE TABLE company_licenses (
    company_id INTEGER NOT NULL REFERENCES company_profiles(id) ON DELETE CASCADE,
    license_code TEXT NOT NULL REFERENCES licenses(code),
    PRIMARY KEY (company_id, license_code)
);

CREATE TABLE company_held_documents (
    company_id INTEGER NOT NULL REFERENCES company_profiles(id) ON DELETE CASCADE,
    document_type_code TEXT NOT NULL REFERENCES document_types(code),
    PRIMARY KEY (company_id, document_type_code)
);

-- The current P0 matching rule requires all listed licenses and one listed region.
CREATE TABLE notice_required_licenses (
    revision_id INTEGER NOT NULL REFERENCES notice_revisions(id),
    license_code TEXT NOT NULL REFERENCES licenses(code),
    PRIMARY KEY (revision_id, license_code)
);

CREATE TABLE notice_allowed_regions (
    revision_id INTEGER NOT NULL REFERENCES notice_revisions(id),
    region_code TEXT NOT NULL REFERENCES regions(code),
    PRIMARY KEY (revision_id, region_code)
);

CREATE TABLE notice_summary_items (
    revision_id INTEGER NOT NULL REFERENCES notice_revisions(id),
    field TEXT NOT NULL CHECK (field IN ('project', 'budget', 'schedule', 'eligibility', 'evaluation')),
    value TEXT CHECK (length(trim(value)) > 0),
    citation_id INTEGER,
    CHECK ((value IS NULL) = (citation_id IS NULL)),
    PRIMARY KEY (revision_id, field),
    FOREIGN KEY (revision_id, citation_id) REFERENCES notice_citations(revision_id, id)
);

CREATE TABLE notice_required_documents (
    revision_id INTEGER NOT NULL REFERENCES notice_revisions(id),
    position INTEGER NOT NULL CHECK (typeof(position) = 'integer' AND position > 0),
    document_type_code TEXT NOT NULL REFERENCES document_types(code),
    instructions TEXT,
    citation_id INTEGER NOT NULL,
    PRIMARY KEY (revision_id, position),
    FOREIGN KEY (revision_id, citation_id) REFERENCES notice_citations(revision_id, id)
);

-- A manual decision overrides the company's default document inventory.
CREATE TABLE company_checklist_items (
    company_id INTEGER NOT NULL REFERENCES company_profiles(id) ON DELETE CASCADE,
    revision_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    is_ready INTEGER NOT NULL CHECK (is_ready IN (0, 1)),
    updated_at INTEGER NOT NULL CHECK (typeof(updated_at) = 'integer'),
    PRIMARY KEY (company_id, revision_id, position),
    FOREIGN KEY (revision_id, position) REFERENCES notice_required_documents(revision_id, position)
);

CREATE INDEX company_checklist_requirement_idx ON company_checklist_items (revision_id, position);

COMMIT;
