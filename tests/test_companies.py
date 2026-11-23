"""업체정보 API·점수 저장 테스트 — 수집 DB 사본(임시 폴더)에 가상 업체를 넣고 확인한다. 실제 DB는 건드리지 않는다.

    python -m unittest tests.test_companies -v
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from backend import auth, companies_api, favorites, schedule
from backend.db.init_db import DEFAULT_DB_PATH

VALID = {"company_name": "가상전기(주)", "business_registration_no": "123-45-67890",
         "head_office_address": "충청북도 충주시 가상로 1", "juso": {"siNm": "충청북도", "sggNm": "충주시"},
         "licenses": [], "qualifications": []}


@unittest.skipUnless(DEFAULT_DB_PATH.exists(), "수집 DB 없음")
class CompanyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = Path(self.tmp) / "t.sqlite3"
        shutil.copy(DEFAULT_DB_PATH, path)
        self.db = companies_api.connect(path)
        lic = self.db.execute("SELECT license_code FROM license_master WHERE license_name='전기공사업'").fetchone()[0]
        self.body = {**VALID,
                     "licenses": [{"license_code": lic, "license_registration_no": "충북-123",
                                   "license_registration_date": "2020-01-02"}]}
        self.user, _ = auth.signup(self.db, {"email": "owner@bidmate.test", "password": "password-1"})
        self.other, _ = auth.signup(self.db, {"email": "other@bidmate.test", "password": "password-2"})

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_and_get(self):
        c = companies_api.create_company(self.db, self.body, self.user)
        self.assertEqual(c["business_registration_no"], "1234567890")
        self.assertEqual(c["region"]["region_name"][:2], "충주")
        self.assertEqual(c["licenses"][0]["license_name"], "전기공사업")

    def test_address_must_come_from_search(self):
        no_juso = {k: v for k, v in self.body.items() if k != "juso"}
        with self.assertRaises(companies_api.InputError):  # 직접 입력(주소 검색 없음)은 거부
            companies_api.create_company(self.db, {**no_juso, "area_limit": "C"}, self.user)
        with self.assertRaises(companies_api.InputError):  # SRM 기준정보에 없는 시군구
            companies_api.create_company(self.db, {**self.body, "juso": {"siNm": "충청북도", "sggNm": "없는군"}},
                                         self.user)

    def test_invalid_business_no_rejected(self):
        with self.assertRaises(companies_api.InputError):
            companies_api.create_company(self.db, {**self.body, "business_registration_no": "12345"}, self.user)

    def test_update_replaces_licenses(self):
        c = companies_api.create_company(self.db, self.body, self.user)
        u = companies_api.update_company(self.db, c["company_id"], {"company_name": "새이름", "licenses": []},
                                       self.user)
        self.assertEqual(u["company_name"], "새이름")
        self.assertEqual(u["licenses"], [])

    def test_save_evaluation_draft(self):
        c = companies_api.create_company(self.db, self.body, self.user)
        r = companies_api.save_evaluation(self.db, c["company_id"], "E012608220", {"bid_price": 1}, self.user)
        self.assertEqual(r["status"], "IN_PROGRESS")
        d = companies_api.get_evaluation_draft(self.db, c["company_id"], "E012608220", self.user)
        self.assertEqual(d["calculation_status"], "IN_PROGRESS")
        self.assertEqual(d["inputs"], {"bid_price": 1})
        self.assertEqual(d["updated_by"], self.user["user_id"])

    def test_other_user_cannot_see_company(self):
        c = companies_api.create_company(self.db, self.body, self.user)
        self.assertIsNone(companies_api.get_company(self.db, c["company_id"], self.other))
        self.assertIsNone(companies_api.update_company(self.db, c["company_id"], {"company_name": "x"}, self.other))
        self.assertIsNone(companies_api.save_evaluation(self.db, c["company_id"], "E012608220", {}, self.other))
        self.assertEqual(companies_api.list_companies(self.db, self.other)["companies"], [])
        self.assertEqual(len(companies_api.list_companies(self.db, self.user)["companies"]), 1)

    def test_admin_sees_all(self):
        c = companies_api.create_company(self.db, self.body, self.user)
        admin = {**self.other, "is_admin": True}
        self.assertIsNotNone(companies_api.get_company(self.db, c["company_id"], admin))


    def test_join_requires_owner_approval(self):
        c = companies_api.create_company(self.db, self.body, self.user)  # 사업자번호 123-45-67890
        found = companies_api.lookup_by_brn(self.db, "1234567890")["companies"]
        self.assertEqual([x["company_id"] for x in found], [c["company_id"]])
        joiner, _ = auth.signup(self.db, {"email": "join@bidmate.test", "password": "password-3",
                                          "join_company_id": c["company_id"]})
        self.assertIsNone(companies_api.get_company(self.db, c["company_id"], joiner))  # 승인 전: 못 봄
        self.assertEqual(companies_api.list_companies(self.db, joiner)["join_requests"][0]["membership_status"], "PENDING")
        self.assertIsNone(companies_api.members(self.db, c["company_id"], self.other))  # 소유자 아님
        self.assertIsNone(companies_api.set_member_status(self.db, c["company_id"], joiner["user_id"], "ACTIVE", joiner))
        companies_api.set_member_status(self.db, c["company_id"], joiner["user_id"], "ACTIVE", self.user)
        self.assertIsNotNone(companies_api.get_company(self.db, c["company_id"], joiner))  # 승인 후: 봄
        with self.assertRaises(companies_api.InputError):  # 소유자 상태는 못 바꿈
            companies_api.set_member_status(self.db, c["company_id"], self.user["user_id"], "REJECTED", self.user)
        with self.assertRaises(ValueError):  # 없는 업체로 합류 요청
            auth.signup(self.db, {"email": "x@bidmate.test", "password": "password-4", "join_company_id": "nope"})

    def test_region_match(self):
        name = lambda r: self.db.execute("SELECT region_name FROM srm_region_district_master WHERE area_limit=? "
                                         "AND region_code=?", r).fetchone()[0]
        self.assertEqual(name(companies_api.match_region(self.db, "충청북도", "충주시")), "충주시")
        self.assertEqual(name(companies_api.match_region(self.db, "광주광역시", "광산구")), "광산구")  # 전남광주
        self.assertEqual(companies_api.match_region(self.db, "광주광역시", "광산구")[0], "I")
        self.assertEqual(name(companies_api.match_region(self.db, "전북특별자치도", "전주시 덕진구")), "전주시 덕진구")
        self.assertEqual(name(companies_api.match_region(self.db, "세종특별자치시", "")), "세종시")
        self.assertEqual(name(companies_api.match_region(self.db, "경기도", "화성시 동탄구")), "화성시")  # SRM 에 구 없음
        self.assertEqual(name(companies_api.match_region(self.db, "인천광역시", "미추홀구")), "남구")  # SRM 옛 이름
        self.assertIsNone(companies_api.match_region(self.db, "없는도", "충주시"))

    def test_address_validation_status(self):
        c = companies_api.create_company(self.db, {**self.body, "area_limit": "B"}, self.user)  # 지역 직접값은 무시
        self.assertEqual((c["area_limit"], c["region"]["region_name"], c["address_validation_status"]),
                         ("C", "충주시", "MATCH"))
        with self.assertRaises(companies_api.InputError):  # 주소만 바꾸고 주소 검색 결과가 없으면 거부
            companies_api.update_company(self.db, c["company_id"], {"head_office_address": "다른 주소"}, self.user)
        u = companies_api.update_company(self.db, c["company_id"], {
            "head_office_address": "세종특별자치시 도움6로 42", "juso": {"siNm": "세종특별자치시", "sggNm": ""}}, self.user)
        self.assertEqual((u["area_limit"], u["region"]["region_name"]), ("L", "세종시"))
        self.assertEqual(companies_api.update_company(self.db, c["company_id"], {"company_name": "새이름"},
                                                      self.user)["area_limit"], "L")  # 이름만 바꾸면 주소 그대로

@unittest.skipUnless(DEFAULT_DB_PATH.exists(), "수집 DB 없음")
class AuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = Path(self.tmp) / "t.sqlite3"
        shutil.copy(DEFAULT_DB_PATH, path)
        self.db = companies_api.connect(path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_login_and_token(self):
        auth.signup(self.db, {"email": "A@bidmate.test", "password": "password-1"})
        user, token = auth.login(self.db, {"email": "a@bidmate.test", "password": "password-1"})
        self.assertEqual(auth.user_from_token(self.db, token)["user_id"], user["user_id"])
        self.assertIsNone(auth.user_from_token(self.db, token[:-1] + ("0" if token[-1] != "0" else "1")))

    def test_wrong_password(self):
        auth.signup(self.db, {"email": "a@bidmate.test", "password": "password-1"})
        with self.assertRaises(auth.AuthError):
            auth.login(self.db, {"email": "a@bidmate.test", "password": "wrong-pass"})

    def test_password_not_stored_plain(self):
        auth.signup(self.db, {"email": "a@bidmate.test", "password": "password-1"})
        stored = self.db.execute("SELECT password_hash FROM app_user").fetchone()[0]
        self.assertNotIn("password-1", stored)
        self.assertTrue(stored.startswith("scrypt$"))

    def test_duplicate_and_short_password(self):
        auth.signup(self.db, {"email": "a@bidmate.test", "password": "password-1"})
        with self.assertRaises(ValueError):
            auth.signup(self.db, {"email": "a@bidmate.test", "password": "password-1"})
        with self.assertRaises(ValueError):
            auth.signup(self.db, {"email": "b@bidmate.test", "password": "short"})


    def test_favorites_per_user_and_calendar(self):
        a, _ = auth.signup(self.db, {"email": "a@bidmate.test", "password": "password-1"})
        b, _ = auth.signup(self.db, {"email": "b@bidmate.test", "password": "password-2"})
        no = self.db.execute("SELECT notice_no FROM notice_schedule_event WHERE event_at IS NOT NULL LIMIT 1").fetchone()[0]
        self.assertEqual(favorites.add(self.db, a, no)["count"], 1)
        self.assertEqual(favorites.add(self.db, a, no)["count"], 1)  # 두 번 눌러도 1건
        self.assertIsNone(favorites.add(self.db, a, "없는공고"))
        self.assertEqual(favorites.list_favorites(self.db, b)["count"], 0)
        events = schedule.list_events(self.db, user_id=a["user_id"])["events"]
        self.assertTrue(events and all(e["notice_no"] == no for e in events))
        self.assertEqual(schedule.list_events(self.db, user_id=b["user_id"])["events"], [])
        self.assertEqual(favorites.remove(self.db, a, no)["count"], 0)


if __name__ == "__main__":
    unittest.main()
