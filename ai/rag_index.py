"""Searchable public tender text. The source of truth remains the collected DB."""

import argparse
import hashlib
import re
from contextlib import closing
from pathlib import Path

from backend.db.init_db import DEFAULT_DB_PATH
from backend.notices_api import SCOPE
from backend.companies_api import connect


SCHEMA = """
CREATE TABLE IF NOT EXISTS rag_document (
    attachment_id INTEGER PRIMARY KEY REFERENCES bid_attachment(id) ON DELETE CASCADE,
    version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rag_chunk (
    id INTEGER PRIMARY KEY,
    attachment_id INTEGER NOT NULL REFERENCES rag_document(attachment_id) ON DELETE CASCADE,
    section_path TEXT NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rag_chunk_attachment ON rag_chunk(attachment_id);
CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunk_fts
    USING fts5(section_path, body, content='rag_chunk', content_rowid='id', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS rag_chunk_insert AFTER INSERT ON rag_chunk BEGIN
    INSERT INTO rag_chunk_fts(rowid, section_path, body) VALUES (new.id, new.section_path, new.body);
END;
CREATE TRIGGER IF NOT EXISTS rag_chunk_delete AFTER DELETE ON rag_chunk BEGIN
    INSERT INTO rag_chunk_fts(rag_chunk_fts, rowid, section_path, body)
    VALUES ('delete', old.id, old.section_path, old.body);
END;
"""
HEADING = re.compile(r"^제\s*\d+\s*조\b|^\[?별\s*표\s*\d+|^\d+[.)]\s+[^|]{2,66}$")
TABLE_START = re.compile(r"^\[표(?:\s*시작)?\]")
TABLE_END = "[표 끝]"
CHUNK_SIZE = 1200


def chunks(text):
    """Group clauses; carry the surrounding lines when a long section is split."""
    section = "본문"
    lines = []
    size = 0
    in_table = False
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not in_table and HEADING.match(line) and len(line) <= 100:
            if lines:
                yield section, lines[0][0], lines[-1][0], "\n".join(value for _, value in lines)
            section, lines, size = line, [], 0
        if TABLE_START.match(line):
            in_table = True
        for offset in range(0, len(line), CHUNK_SIZE):
            part = line[offset:offset + CHUNK_SIZE]
            if lines and size + len(part) + 1 > CHUNK_SIZE:
                yield section, lines[0][0], lines[-1][0], "\n".join(value for _, value in lines)
                lines = lines[-2:] if len(lines) > 2 and sum(len(v) + 1 for _, v in lines[-2:]) + len(part) <= CHUNK_SIZE else []
                size = sum(len(value) + 1 for _, value in lines)
            lines.append((number, part))
            size += len(part) + 1
        if line == TABLE_END:
            in_table = False
    if lines:
        yield section, lines[0][0], lines[-1][0], "\n".join(value for _, value in lines)


def prepare(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rag_chunk_delete' AND type='trigger'").fetchone():
        db.executescript(SCHEMA)


def prepare_budget(db):
    db.execute("""CREATE TABLE IF NOT EXISTS qna_llm_daily (
        day TEXT PRIMARY KEY,
        attempts INTEGER NOT NULL CHECK(attempts >= 0)
    )""")


def source_version(row):
    return hashlib.sha256(
        f"1|{row['sha256']}|{row['parser_version']}|{row['text']}".encode()).hexdigest()


def indexable_sources(db, notice):
    return db.execute("""SELECT a.id, a.sha256, c.text, c.parser_version, r.version
        FROM bid_attachment a JOIN bid_document_content c ON c.attachment_id=a.id
        LEFT JOIN rag_document r ON r.attachment_id=a.id
        WHERE a.notice_no=? AND c.read_status='SUCCESS' AND length(trim(c.text))>0""", (notice,)).fetchall()


def sync_notice(db, notice):
    """Refresh changed documents using the same transaction for chunks and FTS."""
    prepare(db)
    sources = indexable_sources(db, notice)
    versions = {row["id"]: source_version(row) for row in sources}
    if any(row["version"] != versions[row["id"]] for row in sources):
        db.execute("BEGIN IMMEDIATE")
        with db:
            # Use committed input after the write lock is acquired.
            versions = {}
            for row in indexable_sources(db, notice):
                version = source_version(row)
                versions[row["id"]] = version
                if row["version"] == version:
                    continue
                attachment = row["id"]
                db.execute("DELETE FROM rag_chunk WHERE attachment_id=?", (attachment,))
                db.execute("""INSERT INTO rag_document VALUES (?,?)
                    ON CONFLICT(attachment_id) DO UPDATE SET version=excluded.version""", (attachment, version))
                db.executemany("""INSERT INTO rag_chunk(attachment_id,section_path,line_start,line_end,body)
                    VALUES (?,?,?,?,?)""", ((attachment, *chunk) for chunk in chunks(row["text"])))
    return versions


def search(db, notice, terms, limit=3):
    if not terms:
        return []
    sync_notice(db, notice)
    db.execute("BEGIN")
    with db:
        versions = {row["id"]: row["version"] for row in indexable_sources(db, notice)
                    if row["version"] and row["version"] == source_version(row)}
        return search_current(db, notice, terms, versions, limit) if versions else []


def search_current(db, notice, terms, versions, limit):
    base = """SELECT c.id,c.section_path,c.line_start,c.line_end,c.body,a.filename,a.doc_kind,a.id AS attachment_id
        FROM rag_chunk c JOIN rag_document d ON d.attachment_id=c.attachment_id
        JOIN bid_attachment a ON a.id=c.attachment_id
        JOIN bid_document_content b ON b.attachment_id=a.id"""
    # Pair each attachment with its indexed version, so another writer cannot
    # swap in different chunks between sync and search.
    pairs = " OR ".join("(a.id=? AND d.version=?)" for _ in versions)
    where = f"WHERE a.notice_no=? AND b.read_status='SUCCESS' AND ({pairs})"
    args = [notice, *(value for pair in versions.items() for value in pair)]
    long_terms = [term for term in terms if len(term) >= 3]
    rows = []
    if long_terms:
        query = " OR ".join('"' + term.replace('"', '""') + '"' for term in long_terms)
        rows = db.execute(f"""{base} JOIN rag_chunk_fts f ON f.rowid=c.id
            {where} AND rag_chunk_fts MATCH ?
            ORDER BY bm25(rag_chunk_fts, 2, 1), c.id LIMIT ?""", (*args, query, limit * 3)).fetchall()
    if len(rows) < limit:
        filters = " OR ".join("instr(c.body, ?) > 0 OR instr(c.section_path, ?) > 0" for _ in terms)
        rows += db.execute(f"{base} {where} AND ({filters}) ORDER BY c.id LIMIT ?",
                           (*args, *(term for term in terms for _ in range(2)), limit * 3)).fetchall()
    chosen = []
    for row in rows:
        if any(old["attachment_id"] == row["attachment_id"]
               and min(old["line_end"], row["line_end"]) - max(old["line_start"], row["line_start"]) + 1
               >= min(old["line_end"] - old["line_start"] + 1, row["line_end"] - row["line_start"] + 1) * .7
               for old in chosen):
            continue
        chosen.append(dict(row))
        if len(chosen) == limit:
            break
    return chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--notice", help="Index only this notice")
    args = parser.parse_args()
    with closing(connect(args.db)) as db:
        prepare(db)
        notices = db.execute(f"""SELECT notice_no FROM bid_notice WHERE {SCOPE}
            AND (? IS NULL OR notice_no=?) AND EXISTS (SELECT 1 FROM bid_attachment a
            WHERE a.notice_no=bid_notice.notice_no)""", (args.notice, args.notice)).fetchall()
        if args.notice and not notices:
            parser.error("No attachments for this notice")
        total = sum(len(sync_notice(db, row[0])) for row in notices)
        print(f"공고 {len(notices)}건, 검색 가능 첨부 {total}개")


if __name__ == "__main__":
    main()
