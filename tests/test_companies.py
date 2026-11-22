"""업체정보 API·점수 저장 테스트 — 수집 DB 사본(임시 폴더)에 가상 업체를 넣고 확인한다. 실제 DB는 건드리지 않는다.

    python -m unittest tests.test_companies -v
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from backend import companies_api
from backend.db.init_db import DEFAULT_DB_PATH

VALID = {"company_name": "가상전기(주)", "business_registration_no": "123-45-67890",
         "head_office_address": "충북 충주시 가상로 1", "area_limit": "C", "sub_area_code": None,
         "licenses": [], "qualifications": []}


@unittest.skipUnless(DEFAULT_DB_PATH.exists(), "수집 DB 없음")
class CompanyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = Path(self.tmp) / "t.sqlite3"
        shutil.copy(DEFAULT_DB_PATH, path)
        self.db = companies_api.connect(path)
        sub = self.db.execute("SELECT region_code FROM srm_region_district_master WHERE area_limit='C' "
                              "AND region_name LIKE '충주%'").fetchone()[0]
        lic = self.db.execute("SELECT license_code FROM license_master WHERE license_name='전기공사업'").fetchone()[0]
        self.body = {**VALID, "sub_area_code": sub,
                     "licenses": [{"license_code": lic, "license_registration_no": "충북-123",
                                   "license_registration_date": "2020-01-02"}]}

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_and_get(self):
        c = companies_api.create_company(self.db, self.body)
        self.assertEqual(c["business_registration_no"], "1234567890")
        self.assertEqual(c["region"]["region_name"][:2], "충주")
        self.assertEqual(c["licenses"][0]["license_name"], "전기공사업")

    def test_invalid_region_rejected(self):
        with self.assertRaises(companies_api.InputError):
            companies_api.create_company(self.db, {**self.body, "area_limit": "B"})  # 부산 + 충주 조합

    def test_invalid_business_no_rejected(self):
        with self.assertRaises(companies_api.InputError):
            companies_api.create_company(self.db, {**self.body, "business_registration_no": "12345"})

    def test_update_replaces_licenses(self):
        c = companies_api.create_company(self.db, self.body)
        u = companies_api.update_company(self.db, c["company_id"], {"company_name": "새이름", "licenses": []})
        self.assertEqual(u["company_name"], "새이름")
        self.assertEqual(u["licenses"], [])

    def test_save_evaluation_draft(self):
        c = companies_api.create_company(self.db, self.body)
        r = companies_api.save_evaluation(self.db, c["company_id"], "E012608220", {"bid_price": 1})
        self.assertEqual(r["status"], "IN_PROGRESS")
        d = companies_api.get_evaluation_draft(self.db, c["company_id"], "E012608220")
        self.assertEqual(d["calculation_status"], "IN_PROGRESS")
        self.assertEqual(d["inputs"], {"bid_price": 1})


if __name__ == "__main__":
    unittest.main()
