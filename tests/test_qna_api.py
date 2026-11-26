"""POST /api/qna 연결 테스트 — 수집 DB 사본(임시 폴더)으로 서버를 띄워 확인한다. LLM 은 부르지 않는다 (한도 0).

    python -m unittest tests.test_qna_api -v
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from backend import companies_api, notices_api, server
from backend.db.init_db import DEFAULT_DB_PATH


@unittest.skipUnless(DEFAULT_DB_PATH.exists(), "수집 DB 없음")
class QnaApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        path = Path(cls.tmp) / "t.sqlite3"
        shutil.copy(DEFAULT_DB_PATH, path)
        cls.patches = [mock.patch.object(notices_api, "DEFAULT_DB_PATH", path),
                       mock.patch.object(companies_api, "DEFAULT_DB_PATH", path),
                       mock.patch.dict(os.environ, {"QNA_DAILY_LLM_LIMIT": "0"})]
        for p in cls.patches:
            p.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(server.DemoHandler, directory=cls.tmp))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/api/qna"
        db = notices_api.connect(path)
        cls.notice = db.execute("SELECT notice_no FROM bid_notice ORDER BY notice_no DESC LIMIT 1").fetchone()[0]
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def post(self, data):
        req = urllib.request.Request(self.url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def ask(self, body):
        return self.post(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def test_general_question_delegates_to_screen(self):
        self.assertEqual(self.ask({"question": "오늘 참가 가능한 공고 알려줘"}), (200, {"delegate": "local"}))

    def test_notice_question_answers_with_sources(self):
        status, body = self.ask({"question": "면허 조건 알려줘", "notice_no": self.notice})
        self.assertEqual(status, 200)
        self.assertIn("answer", body)
        self.assertIsInstance(body["sources"], list)

    def test_bad_input_is_400(self):
        self.assertEqual(self.ask({"question": ""})[0], 400)
        self.assertEqual(self.ask({"question": "x" * 1001})[0], 400)
        self.assertEqual(self.post(b"{not json")[0], 400)

    def test_too_large_request_is_400(self):
        status, body = self.ask({"question": "면허", "history": [{"role": "user", "text": "가" * 30000}]})
        self.assertEqual((status, body["error"]), (400, "질문 요청이 너무 큽니다."))
