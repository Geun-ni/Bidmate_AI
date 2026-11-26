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
