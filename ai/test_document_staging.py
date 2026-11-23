import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai.document_staging import apply_review, export, ingest, migrate, open_database


class StagingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.db = open_database(self.folder / "stage.sqlite3")
        self.text = "제8조 (제출서류)\n1. 사업자등록증 사본을 제출한다."
        self.write_sources(self.text)
        ingest(self.db, self.folder)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def write_sources(self, text):
        for filename, row in (
            ("bid_attachment.csv", {"id": "1", "notice_no": "E001", "filename": "기준.hwp",
                                    "doc_kind": "CRITERIA", "sha256": "filehash"}),
            ("bid_document_content.csv", {"attachment_id": "1", "read_status": "SUCCESS",
                                         "text_1": text[:15], "text_2": text[15:], "text_3": ""}),
        ):
            with (self.folder / filename).open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)

    def review_record(self):
        export(self.db, self.folder / "export")
        record = json.loads((self.folder / "export/review.jsonl").read_text(encoding="utf-8"))
        record.update(reviewer="fixture reviewer", review_complete=True)
        return record

    def save_review(self, record):
        path = self.folder / "review.jsonl"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return path

    def test_chunks_and_repeat_keep_review(self):
        self.assertEqual(self.db.execute("SELECT content FROM staging_source").fetchone()[0], self.text)
        apply_review(self.db, self.save_review(self.review_record()))
        self.assertEqual(ingest(self.db, self.folder), 0)
        self.assertEqual(self.db.execute("SELECT verification_status FROM required_document_item").fetchone()[0], "VERIFIED")

    def test_invalid_quote_rolls_back_replacement(self):
        record = self.review_record()
        valid_count = len(record["items"])
        record["items"].append({**record["items"][0], "source_quote": "원문에 없는 서류"})
        with self.assertRaises(ValueError):
            apply_review(self.db, self.save_review(record))
        self.assertEqual(self.db.execute("SELECT count(*) FROM required_document_item").fetchone()[0], valid_count)
        self.assertEqual(self.db.execute("SELECT analysis_status FROM notice_analysis_cache").fetchone()[0], "PENDING")

    def test_source_change_invalidates_and_rejects_stale_review(self):
        record = self.review_record()
        apply_review(self.db, self.save_review(record))
        self.write_sources(self.text + "\n2. 실적증명서를 제출한다.")
        ingest(self.db, self.folder)
        with self.assertRaises(ValueError):
            apply_review(self.db, self.save_review(record))
        self.assertEqual(self.db.execute("SELECT count(*) FROM required_document_item WHERE verification_status='VERIFIED'").fetchone()[0], 0)

    def test_unreviewed_export_and_id_remapping(self):
        self.assertEqual(export(self.db, self.folder / "export")["verified_export_rows"], 0)
        apply_review(self.db, self.save_review(self.review_record()))
        target_path = self.folder / "target.sqlite3"
        target = sqlite3.connect(target_path)
        target.executescript("""
            CREATE TABLE bid_attachment(id INTEGER PRIMARY KEY, notice_no TEXT, filename TEXT, sha256 TEXT);
            CREATE TABLE bid_document_content(attachment_id INTEGER, read_status TEXT, text TEXT);
        """)
        # Copy the target column layout with its FK removed for this focused fixture.
        definition = self.db.execute("SELECT sql FROM sqlite_master WHERE name='required_document_item'").fetchone()[0]
        target.execute(definition.replace(" REFERENCES staging_notice(notice_no)", "")
                       .replace(" REFERENCES staging_source(attachment_id)", ""))
        target.execute("INSERT INTO bid_attachment VALUES (99,'E001','기준.hwp','filehash')")
        target.execute("INSERT INTO bid_document_content VALUES (99,'SUCCESS',?)", (self.text,))
        target.commit()
        self.assertGreater(migrate(self.db, target_path)["would_insert"], 0)
        self.assertEqual(target.execute("SELECT count(*) FROM required_document_item").fetchone()[0], 0)
        result = migrate(self.db, target_path, apply=True)
        self.assertGreater(result["inserted"], 0)
        self.assertEqual(target.execute("SELECT source_attachment_id FROM required_document_item").fetchone()[0], 99)
        self.assertEqual(migrate(self.db, target_path, apply=True)["inserted"], 0)
        target.execute("UPDATE required_document_item SET name='다른 검수 결과'")
        target.commit()
        with self.assertRaises(ValueError):
            migrate(self.db, target_path, apply=True)
        target.close()


if __name__ == "__main__":
    unittest.main()
