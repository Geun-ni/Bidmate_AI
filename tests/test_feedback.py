"""베타 의견 저장 테스트 — 수집 DB 사본(임시 폴더)에 스키마를 적용해 확인한다. 실제 DB는 건드리지 않는다.

    python -m unittest tests.test_feedback -v
"""
import base64
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from backend import auth, companies_api, feedback
from backend.db.init_db import DEFAULT_DB_PATH, init_db

CTX = {"page": "/notices/E1", "notice_no": "E1", "user_id": "위조", "viewport": "1280x900"}
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()


@unittest.skipUnless(DEFAULT_DB_PATH.exists(), "수집 DB 없음")
class FeedbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = Path(self.tmp) / "t.sqlite3"
        shutil.copy(DEFAULT_DB_PATH, path)
        init_db(path).close()
        self.db = companies_api.connect(path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def row(self, fid):
        return self.db.execute("SELECT * FROM beta_feedback WHERE id=?", (fid,)).fetchone()

    def test_general_with_image_and_session_user(self):
        user, _ = auth.signup(self.db, {"email": "fb@bidmate.test", "password": "password-1"})
        r = feedback.save(self.db, {"kind": "general", "category": "BUG", "message": " 점수가 이상해요 ",
                                    "email": None, "attachment": {"name": "a.png", "type": "image/png", "data": PNG},
                                    "context": CTX}, user)
        row = self.row(r["id"])
        self.assertEqual((row["user_id"], row["notice_no"], row["message"], row["attachment_type"]),
                         (user["user_id"], "E1", "점수가 이상해요", "image/png"))
        self.assertEqual(row["attachment_data"], b"\x89PNG fake")

    def test_reaction_and_survey(self):
        r = feedback.save(self.db, {"kind": "reaction", "target": "score", "ref_id": "E1", "value": "down",
                                    "comment": "통과점수가 달라요", "extra": {"total": 88.2}, "context": CTX})
        row = self.row(r["id"])
        self.assertEqual((row["user_id"], row["value"], json.loads(row["payload"])), (None, "down", {"total": 88.2}))
        r = feedback.save(self.db, {"kind": "survey", "answers": {"likert": {"find_easy": 4}, "nps": 0},
                                    "context": CTX})
        self.assertEqual(json.loads(self.row(r["id"])["payload"])["nps"], 0)

    def test_bad_input(self):
        bad = [{"kind": "x"}, {"kind": "general", "category": "BUG", "message": "  "},
               {"kind": "general", "category": "ETC", "message": "a"},
               {"kind": "general", "category": "BUG", "message": "a" * 2001},
               {"kind": "general", "category": "BUG", "message": "a",
                "attachment": {"data": "data:text/plain;base64,YQ=="}},
               {"kind": "reaction", "target": "score", "value": "meh"},
               {"kind": "survey", "answers": {"nps": 11}}, {"kind": "survey", "answers": {"nps": True}}]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(ValueError):
                feedback.save(self.db, body)
        self.assertEqual(self.db.execute("SELECT count(*) FROM beta_feedback").fetchone()[0], 0)

    def test_admin_list_and_detail(self):
        user, _ = auth.signup(self.db, {"email": "fb@bidmate.test", "password": "password-1"})
        g = feedback.save(self.db, {"kind": "general", "category": "BUG", "message": "오류",
                                    "attachment": {"name": "a.png", "data": PNG}, "context": CTX}, user)
        feedback.save(self.db, {"kind": "reaction", "target": "qna", "ref_id": "1", "value": "up", "context": CTX})
        admin = {**user, "is_admin": True}
        everything = feedback.list_feedback(self.db, admin)
        self.assertEqual((everything["count"], everything["summary"]), (2, {"general": 1, "reaction": 1}))
        self.assertEqual(everything["items"][0]["kind"], "reaction")                     # 최신순
        bugs = feedback.list_feedback(self.db, admin, {"kind": "general", "category": "BUG"})
        item = bugs["items"][0]
        self.assertEqual((bugs["count"], item["user_email"], item["has_attachment"], item["context"]["page"]),
                         (1, "fb@bidmate.test", True, "/notices/E1"))
        self.assertNotIn("attachment_data", item)
        self.assertEqual(feedback.get_feedback(self.db, admin, g["id"])["attachment"], PNG)
        self.assertIsNone(feedback.get_feedback(self.db, admin, 9999))
        for who in (None, user):                                                         # 관리자 아니면 막음
            with self.assertRaises(PermissionError):
                feedback.list_feedback(self.db, who)
        with self.assertRaises(ValueError):
            feedback.list_feedback(self.db, admin, limit=500)

    def test_status_and_csv(self):
        user, _ = auth.signup(self.db, {"email": "fb@bidmate.test", "password": "password-1"})
        admin = {**user, "is_admin": True}
        g = feedback.save(self.db, {"kind": "general", "category": "IDEA", "message": '엑셀, "따옴표"\n줄바꿈',
                                    "context": CTX}, user)
        feedback.save(self.db, {"kind": "survey", "answers": {"role": "공사 업체", "likert": {"find_easy": 4},
                                                              "best_features": ["점수 계산", "일정"], "nps": 9},
                                "context": CTX})
        self.assertEqual(feedback.get_feedback(self.db, admin, g["id"])["status"], "NEW")
        done = feedback.set_status(self.db, admin, g["id"], {"status": "DONE"})
        self.assertEqual((done["status"], done["status_updated_by"]), ("DONE", user["user_id"]))
        self.assertEqual(feedback.list_feedback(self.db, admin, {"status": "DONE"})["count"], 1)
        self.assertIsNone(feedback.set_status(self.db, admin, 9999, {"status": "DONE"}))
        with self.assertRaises(ValueError):
            feedback.set_status(self.db, admin, g["id"], {"status": "WHAT"})
        with self.assertRaises(PermissionError):
            feedback.set_status(self.db, user, g["id"], {"status": "CHECKED"})

        name, data = feedback.export_csv(self.db, admin)
        self.assertTrue(name.endswith(".csv") and data.startswith(b"\xef\xbb\xbf"))   # 엑셀용 BOM
        import csv
        import io
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        survey, general = rows                                                          # 최신순
        self.assertEqual((survey["종류"], survey["likert.find_easy"], survey["best_features"], survey["nps"]),
                         ("survey", "4", "점수 계산; 일정", "9"))
        self.assertEqual((general["내용"], general["처리 상태"], general["보던 화면"]),
                         ('엑셀, "따옴표"\n줄바꿈', "DONE", "/notices/E1"))
        with self.assertRaises(PermissionError):
            feedback.export_csv(self.db, user)
