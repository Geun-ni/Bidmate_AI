"""Safety checks for evaluation LLM routing and SQLite updates."""

import sqlite3
import unittest

from ai.llm_evaluation import apply, load_case, source_version, validate


def database():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE bid_attachment(id INTEGER PRIMARY KEY, notice_no TEXT, filename TEXT,
            doc_kind TEXT, sha256 TEXT);
        CREATE TABLE bid_document_content(attachment_id INTEGER PRIMARY KEY, read_status TEXT,
            text TEXT, rule_extracted INTEGER);
        CREATE TABLE evaluation_rule(id INTEGER PRIMARY KEY, notice_no TEXT, parent_id INTEGER,
            position INTEGER, category TEXT, item_name TEXT, rule_type TEXT, max_score REAL,
            formula TEXT, formula_source TEXT, parameters TEXT, input_key TEXT,
            rounding_digits INTEGER, rounding_mode TEXT, attachment_id INTEGER,
            source_sha256 TEXT, source_location TEXT, source_quote TEXT,
            verification_status TEXT, updated_by TEXT, extracted_at TEXT, updated_at TEXT,
            UNIQUE(notice_no,position));
        INSERT INTO bid_attachment VALUES (1,'N1','criteria.hwp','CRITERIA','hash1');
        INSERT INTO bid_document_content VALUES (1,'SUCCESS','가격 50점 / 실적 50점',0);
    """)
    return db


def row(**changes):
    result = dict(row_id=None, parent_position=None, category="PRICE", item_name="가격",
                  rule_type="FORMULA", max_score=50, formula="가격비율×50", parameters=None,
                  input_key="bid_price", rounding_digits=None, rounding_mode=None,
                  attachment_id=1, source_location="배점표", source_quote="가격 50점",
                  verification_status="UNVERIFIED")
    result.update(changes)
    return result


class EvaluationLLMTest(unittest.TestCase):
    def test_full_insert_and_source_change_guard(self):
        db = database()
        mode, sources, existing = load_case(db, "N1")
        self.assertEqual(mode, "FULL")
        version = source_version(sources, existing)
        result = {"issues": [], "rows": [row()]}
        apply(db, "N1", version, mode, result)
        saved = db.execute("SELECT verification_status,updated_by,formula FROM evaluation_rule").fetchone()
        self.assertEqual(tuple(saved), ("REVIEW", "LLM", "가격비율×50"))
        with self.assertRaisesRegex(ValueError, "Existing LLM"):
            apply(db, "N1", version, mode, result)

    def test_partial_updates_only_review_row(self):
        db = database()
        db.execute("""INSERT INTO evaluation_rule(id,notice_no,position,category,item_name,rule_type,
            verification_status,updated_by,attachment_id,source_quote)
            VALUES(10,'N1',1,'PRICE','가격','FORMULA','REVIEW','PARSER',1,'가격 50점')""")
        db.execute("""INSERT INTO evaluation_rule(id,notice_no,position,category,item_name,rule_type,
            verification_status,updated_by,attachment_id,source_quote)
            VALUES(11,'N1',2,'PERFORMANCE','실적','FIXED','UNVERIFIED','PARSER',1,'실적 50점')""")
        db.commit()
        mode, sources, existing = load_case(db, "N1")
        self.assertEqual(mode, "REVIEW")
        with self.assertRaisesRegex(ValueError, "REVIEW may update"):
            validate({"issues": [], "rows": [row(row_id=11)]}, mode, sources,
                     [dict(r) for r in db.execute("SELECT * FROM evaluation_rule")])
        apply(db, "N1", source_version(sources, existing), mode, {"issues": [], "rows": [row(row_id=10)]})
        self.assertEqual([tuple(r) for r in db.execute(
            "SELECT id,updated_by FROM evaluation_rule ORDER BY id")],
            [(10, "LLM"), (11, "PARSER")])

    def test_rejects_fabricated_quote(self):
        db = database()
        _, sources, existing = load_case(db, "N1")
        with self.assertRaisesRegex(ValueError, "Source quote"):
            validate({"issues": [], "rows": [row(source_quote="없는 근거")]}, "FULL", sources, existing)


if __name__ == "__main__":
    unittest.main()
