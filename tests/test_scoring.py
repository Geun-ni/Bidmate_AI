"""점수 계산 엔진 테스트 — 실제 수집 DB의 E012608220(배전 1.6억~3억, 기준서 원문과 대조한 공고) 규칙으로 계산한다.

    python -m unittest tests.test_scoring -v

기준서 값: 입찰가격 90 - 20×|(90/100 - (입찰가격-A)/(예정가격-A))×100|, 비율 소수점 다섯째자리 반올림,
90.25% 이상 85점, 최저 2점 / 시공경험 6 = 실적(사정액×1/2 대비, 4점) + 숙지도(2·1·0) / 경영상태 4 /
기준치 부채비율 120.77%·유동비율 142.57% / 통과 95점. A값 10,171,627원, 사정액 271,946,271원.
"""
import json
import unittest

from backend import notices_api, scoring
from backend.db.init_db import DEFAULT_DB_PATH

NOTICE = "E012608220"
A = 10_171_627
PLAN = 300_000_000  # 예정가격 (가정)


def bid_at(ratio):
    """(입찰가격-A)/(예정가격-A) = ratio 가 되는 입찰가격"""
    return A + ratio * (PLAN - A)


def full_inputs(**over):
    base = {"bid_price": bid_at(0.90), "planned_price": PLAN, "management_method": "ratio",
            "12": "해당사업소", "13": 271_946_271,          # 숙지도, 실적누계액 (사정액의 1배 → 상한 4점)
            "9": 100, "10": 150,                           # 부채비율 100% (0.83배 → 2.0), 유동비율 150% (1.05배 → 2.0)
            "14": 0, "15": None, "16": 0, "17": 0, "18": 0, "19": False,  # 가감점 해당 없음
            "7": False}                                     # 기술자 보유 미달 아님
    base.update(over)
    return base


@unittest.skipUnless(DEFAULT_DB_PATH.exists(), "수집 DB 없음")
class ScoringTest(unittest.TestCase):
    def setUp(self):
        self.db = notices_api.connect()

    def tearDown(self):
        self.db.close()

    def calc(self, **over):
        return scoring.calculate(self.db, NOTICE, full_inputs(**over))

    def item(self, result, key):
        stack = list(result["items"])
        while stack:
            node = stack.pop()
            if node["key"] == key:
                return node
            stack += node["children"]
        raise KeyError(key)

    def test_full_score_passes(self):
        r = self.calc()
        self.assertEqual(r["status"], "CALCULATED")
        self.assertEqual(self.item(r, "2")["score"], 90)       # 기준율 90% 정확히 → 만점
        self.assertEqual(self.item(r, "3")["score"], 10)       # 수행능력 6 + 4
        self.assertEqual(r["total"], 100)
        self.assertTrue(r["passed"])

    def test_price_cap_and_slope(self):
        self.assertEqual(self.item(self.calc(bid_price=bid_at(0.89)), "2")["score"], 70)   # 90 - 20×1
        self.assertEqual(self.item(self.calc(bid_price=bid_at(0.9025)), "2")["score"], 85)  # 90.25% 이상 85점
        self.assertEqual(self.item(self.calc(bid_price=bid_at(0.95)), "2")["score"], 85)
        self.assertEqual(self.item(self.calc(bid_price=bid_at(0.80)), "2")["score"], 2)    # 최저평점

    def test_missing_input_is_not_zero(self):
        r = scoring.calculate(self.db, NOTICE, full_inputs(**{"13": None}))
        self.assertIsNone(r["total"])
        self.assertEqual(self.item(r, "13")["status"], scoring.UNCALC)
        self.assertNotIn("passed", r)

    def test_management_method_required(self):
        r = self.calc(management_method=None)
        self.assertEqual(self.item(r, "5")["status"], scoring.UNCALC)

    def test_ratio_boundaries(self):
        # 부채비율: 기준치×1.0이하 2.0 / 1.0초과~1.3미만 1.8
        self.assertEqual(self.item(self.calc(**{"9": 120.77}), "9")["score"], 2.0)
        self.assertEqual(self.item(self.calc(**{"9": 121}), "9")["score"], 1.8)
        self.assertEqual(self.item(self.calc(**{"9": -5}), "9")["score"], 1.2)  # 부(-) 수치 → 최저

    def test_credit_grade(self):
        r = self.calc(management_method="credit", **{"11": "BBB0"})
        self.assertEqual(self.item(r, "5")["score"], 4.0)
        r = self.calc(management_method="credit", **{"11": "CCC"})
        self.assertEqual(self.item(r, "11")["score"], 3.4)  # CCC+이하

    def test_performance_amount_and_cap(self):
        half = 271_946_271 * 0.5 * 0.5  # 사정액×1/2 의 절반 → 2점
        self.assertEqual(self.item(self.calc(**{"13": half}), "13")["score"], 2.0)
        self.assertEqual(self.item(self.calc(**{"13": 10 ** 10}), "13")["score"], 4.0)

    def test_bonus_and_cap_of_performance(self):
        r = self.calc(**{"14": 3, "15": 0})  # 건설재해 3회 이상 -1.0, 일자리 A +2.5
        self.assertEqual(self.item(r, "14")["score"], -1.0)
        self.assertEqual(self.item(r, "15")["score"], 2.5)
        self.assertEqual(self.item(r, "3")["score"], 10)  # 6+4+1.5 → 수행능력 한도 10

    def test_penalty(self):
        self.assertEqual(self.item(self.calc(**{"7": True}), "7")["score"], -10)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(DEFAULT_DB_PATH.exists(), "수집 DB 없음")
class PqScoringTest(unittest.TestCase):
    """감리 PQ 세부 표(E012608054, 1억원 이상 고시금액 미만 구간)·기술용역 사업수행능력 환산(E012606818, 50점).
    DB 사본에서 두 공고만 다시 추출해 계산한다. 값은 기준서 [별표 12] 와 대조."""

    @classmethod
    def setUpClass(cls):
        import shutil, sqlite3, tempfile
        from pathlib import Path
        from backend.db.extract_evaluation_rules import refresh_notice
        cls.tmp = tempfile.mkdtemp()
        path = Path(cls.tmp) / "t.sqlite3"
        shutil.copy(DEFAULT_DB_PATH, path)
        from backend.db.init_db import init_db
        init_db(path).close()  # 사본에 최신 스키마 적용 (새 칸 rule_extracted 등)
        db = sqlite3.connect(path)
        for no in ("E012608054", "E012606818"):
            refresh_notice(db, no)
        db.commit()
        db.close()
        cls.db = notices_api.connect(path)

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.db.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def key(self, notice, name):
        return str(self.db.execute("SELECT position FROM evaluation_rule WHERE notice_no=? AND item_name=?",
                                   (notice, name)).fetchone()[0])

    def find(self, items, name):
        for it in items:
            if it["name"] == name:
                return it
            got = self.find(it["children"], name)
            if got:
                return got
        return None

    def test_gamri_pq_tables(self):
        k = lambda n: self.key("E012608054", n)
        inputs = {k("상주감리원 전기분야 경력"): 6, k("상주감리원 참여분야 경력"): 4.5,
                  k("상주감리원 참여분야 경력(공사감독·공사감리)"): 3, k("비상주감리원 등급"): 1,  # 고급감리원
                  k("비상주감리원 참여분야 경력"): 2,
                  k("감리업체 실적"): 0.9 * 176_230_378,                     # 사정금액의 90% → 5.4
                  k("자본비율"): 43.14, k("유동비율"): 100,                    # 평균 대비 1.0배 → 2.0, 0.79배 → 3.4
                  k("상주감리원 중복배치 없음"): True, k("비상주감리원 중복배치"): 6}
        items = scoring.calculate(self.db, "E012608054", inputs)["items"]
        self.assertAlmostEqual(self.find(items, "참여감리원")["score"], 4.8 + 4.32 + 1.92 + 0.78 + 1.42)
        self.assertAlmostEqual(self.find(items, "유사용역수행실적")["score"], 5.4)
        self.assertAlmostEqual(self.find(items, "신용도")["score"], 2.0 + 3.4)
        self.assertAlmostEqual(self.find(items, "업무중첩도")["score"], 1.8 + 0.9)

    def test_pq_score_conversion(self):
        k = self.key("E012606818", "사업수행능력")
        items = scoring.calculate(self.db, "E012606818", {k: 87.555})["items"]
        self.assertEqual(self.find(items, "사업수행능력")["score"], 43.78)  # 87.555/100×50 = 43.7775 → 셋째자리 반올림
        items = scoring.calculate(self.db, "E012606818", {})["items"]
        self.assertEqual(self.find(items, "사업수행능력")["status"], scoring.UNCALC)


class DirectInputTest(unittest.TestCase):
    """세부 규칙 없는 묶음(직접 입력): 감점 항목은 0 ~ 감점 한도 사이로 자른다"""

    def node(self, max_score):
        return {"key": "1", "name": "부정당업체 영업정지", "category": "BONUS", "max_score": max_score,
                "rule_type": "GROUP", "input_key": None, "parameters": {}, "children": [],
                "verification_status": "UNVERIFIED", "rounding": None}

    def test_penalty_range(self):
        leaf = lambda mx, v: scoring._leaf(self.node(mx), {"1": v}, {})["score"]
        self.assertEqual(leaf(-1.4, 0), 0)        # 해당 없음 → 0 (예전에는 -1.4 로 잘림)
        self.assertEqual(leaf(-1.4, -0.3), -0.3)
        self.assertEqual(leaf(-1.4, -5), -1.4)    # 한도 넘으면 한도
        self.assertEqual(leaf(0.4, 1), 0.4)       # 가점은 0 ~ 한도


class ExceptionRuleTest(unittest.TestCase):
    """원문 대조로 보강한 규칙: OF전기원 예외 점수, 배치기술자 단기 재직 80%, 여성기업 존속기간 구간"""

    def node(self, ik, params, rt="FORMULA"):
        return {"key": "1", "name": "", "category": "PERFORMANCE", "max_score": 3.0, "rule_type": rt,
                "input_key": ik, "parameters": params, "children": [], "verification_status": "UNVERIFIED",
                "rounding": None}

    def test_special_worker(self):
        n = self.node("developed_workers", {"per_unit": 1.5, "unit": "인", "cap": 3.0,
                                            "special": {"name": "OF전기원", "per_unit": 3.0}})
        leaf = lambda inputs: scoring._leaf(n, inputs, {})["score"]
        self.assertEqual(leaf({"1": 1}), 1.5)
        self.assertEqual(leaf({"1_special": 1}), 3.0)          # OF전기원 1인 → 3.0점
        self.assertEqual(leaf({"1": 1, "1_special": 1}), 3.0)  # 한도 3점

    def test_short_tenure(self):
        n = self.node("engineer_days", {"days": 1095, "cap": 2.0, "short_tenure": {"days": 180, "ratio": 0.8}})
        self.assertEqual(scoring._leaf(n, {"1": 1095}, {})["score"], 2.0)
        self.assertAlmostEqual(scoring._leaf(n, {"1": 1095, "1_short": True}, {})["score"], 1.6)

    def test_period_penalty(self):  # '합산기간 3월마다 0.1점씩 감점하되 최대 0.6점 (3월 미만인 경우 3월로 계산)'
        n = self.node("bonus_count", {"per_unit": 0.1, "sign": -1, "limit": -0.6, "period": 3, "round_up": True})
        leaf = lambda v: scoring._leaf(n, {"1": v}, {})["score"]
        self.assertEqual(leaf(0), 0)
        self.assertEqual(leaf(2), -0.1)   # 3월 미만 → 3월
        self.assertEqual(leaf(7), -0.2)
        self.assertEqual(leaf(40), -0.6)  # 한도

    def test_ratio_terms(self):  # 교육훈련 (이수인원 / 평가대상인원) × 점수 합, 최대 0.4
        n = self.node("bonus_ratio", {"terms": [{"label": "1주", "points": 0.2}, {"label": "2주", "points": 0.4}],
                                      "numerator": "교육이수인원", "denominator": "평가대상인원수", "limit": 0.4, "sign": 1})
        self.assertAlmostEqual(scoring._leaf(n, {"1_total": 4, "1_0": 2}, {})["score"], 0.1)
        self.assertAlmostEqual(scoring._leaf(n, {"1_total": 2, "1_0": 1, "1_1": 2}, {})["score"], 0.4)

    def test_tech_dev(self):  # 개발실적 건당 점수 × 경과기간 비율 ÷ 출원자 수, 최대 0.4
        n = self.node("tech_dev", {"kinds": [{"name": "특허", "per_unit": 0.2, "age_pct": [1.0, 0.8, 0.6]},
                                             {"name": "실용신안", "per_unit": 0.1, "age_pct": [1.0, 0.8, None]}],
                                   "age_bounds": [5, 10, 20], "limit": 0.4, "non_field_ratio": 0.5,
                                   "split_by_inventors": True})
        leaf = lambda v: scoring._leaf(n, {"1": v}, {})["score"]
        self.assertAlmostEqual(leaf([{"kind": 0, "years": 7, "inventors": 2}]), 0.08)   # 0.2 × 80% ÷ 2
        self.assertEqual(leaf([{"kind": 1, "years": 15}]), 0)                           # 실용신안 10년 초과 '-'
        self.assertAlmostEqual(leaf([{"kind": 0, "years": 1, "non_field": True}]), 0.1)
        self.assertEqual(leaf([{"kind": 0, "years": 1}] * 3), 0.4)                       # 한도

    def test_method_table_helpers(self):
        from backend.db.extract_evaluation_rules import amount_band_rows, grade_point_rows, period_rules
        text = ("○ 감리업체가 최근 1년간 영업정지를 받은 기간을 합산하여 합산기간 1월마다 0.1점씩 감점하되 최대 0.8점까지 감점. "
                "(1월 미만인 경우 1월로 계산) / ○ 참여감리원이 기술자격정지를 받은 기간을 합산하여 합산기간 3월마다 0.1점씩 "
                "감점하되 최대 0.6점까지 감점.")
        self.assertEqual([(p["subject"], p["period"], p["limit"], p["round_up"]) for p in period_rules(text)],
                         [("감리업체", 1, 0.8, True), ("참여감리원", 3, 0.6, False)])
        lic = ("구 분 / 배 점 / 적용대상 자격증 / 기술사 / 0.2점 / 발송배전 / 기사 / 0.14점 / 전기 / 산업기사 / 0.10점 / 전기 / "
               "OA 산업기사 / 0.05점 / ITQ / ※ 적용대상 자격증이외의 전기관련 자격증은 적용배점의 50% 적용")
        self.assertEqual([r["score"] for r in grade_point_rows(lic)], [0.2, 0.14, 0.1, 0.05, 0.1, 0.07, 0.05])
        band = ("부실벌점 / 배 점 / 4천만원미만 / 4천만원이상 / 1억원미만 / 1억원이상 / 고시금액미만 / "
                "2점 초과 5점 이하 / 0.04 / 0.08 / 0.12 / 25점 초과 / 1 / 2 / 3")
        self.assertEqual([r["score"] for r in amount_band_rows(band, 50_000_000)], [0.08, 2.0])
        self.assertIsNone(amount_band_rows(band, None))

    def test_sub_tier_rows(self):
        from backend.db.extract_evaluation_rules import detail_item_table
        lines = ["[표]", "심사항목 | 세부항목 | 배점 | 비고",
                 "나. 약자기업지원 | A. 여성기업으로서 그 존속기간에 따라 / · 3년 이상 / · 3년 미만 | 0.5 / (0.5) / (0.25) | ",
                 "B. 장애인기업 | 0.2 | ", "[표 끝]"]
        rows = detail_item_table(lines, 0, 4)[0]["rows"]
        self.assertEqual([r["score"] for r in rows], [0.5, 0.25, 0.2])
        self.assertIn("3년 미만", rows[1]["grade"])


@unittest.skipUnless(DEFAULT_DB_PATH.exists(), "수집 DB 없음")
class RuleExtractedTest(unittest.TestCase):
    """기준서 본문 rule_extracted 표시와 LLM 대상 목록 (팀 결정 2026-11-22)
    성공 → 1 / 항목만 못 읽음 → 0 + 행 유지(그 행만 REVIEW) / 배점표를 못 믿음 → 0 + 행 없음"""

    def test_flags_and_targets(self):
        import shutil, sqlite3, tempfile
        from pathlib import Path
        from backend.db.init_db import init_db
        from backend.db.extract_evaluation_rules import refresh_notice
        from backend.collector.fetch_attachments import llm_targets
        tmp = tempfile.mkdtemp()
        try:
            path = Path(tmp) / "t.sqlite3"
            shutil.copy(DEFAULT_DB_PATH, path)
            init_db(path).close()
            db = sqlite3.connect(path)
            for no in ("E012608220", "E012608054", "E012608163", "E012608048"):  # 성공 / 감리 성공 / 양성실적만 문제 / PDF
                refresh_notice(db, no)
            db.commit()
            flag = lambda no: db.execute("""SELECT DISTINCT c.rule_extracted FROM bid_document_content c
                JOIN bid_attachment a ON a.id = c.attachment_id WHERE a.notice_no=? AND a.doc_kind='CRITERIA'""",
                                         (no,)).fetchall()
            rows = lambda no, extra="": db.execute(f"SELECT count(*) FROM evaluation_rule WHERE notice_no=? {extra}",
                                                   (no,)).fetchone()[0]
            self.assertEqual(flag("E012608220"), [(1,)])
            self.assertEqual(flag("E012608054"), [(1,)])                           # 감리 가감점 5종까지 규칙으로
            self.assertEqual(flag("E012608163"), [(0,)])
            self.assertGreater(rows("E012608163"), 0)                              # 멀쩡한 행은 남김
            self.assertGreater(rows("E012608163", "AND verification_status='REVIEW'"), 0)
            self.assertEqual(flag("E012608048"), [(0,)])
            self.assertEqual(rows("E012608048"), 0)                                # 통째로 비움
            penalty = db.execute("SELECT parameters FROM evaluation_rule WHERE notice_no='E012608054' "
                                 "AND item_name='부실벌점'").fetchone()[0]
            self.assertEqual([r["score"] for r in __import__("json").loads(penalty)["rows"]][::5], [-0.12, -3.0])
            # ↑ 추정가격 1.6억 → '1억원 이상 고시금액 미만' 열
            # LLM 이 REVIEW 행을 다 고치면 대상에서 빠진다
            targets = llm_targets(path)
            self.assertIn("E012608163", targets)
            self.assertNotIn("E012608054", targets)
            self.assertIn("E012608048", targets)
            self.assertNotIn("E012608220", targets)
            db.execute("UPDATE evaluation_rule SET updated_by='LLM' WHERE notice_no='E012608163' "
                       "AND verification_status='REVIEW'")
            db.commit()
            self.assertNotIn("E012608163", llm_targets(path))
            db.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_correction_invalidates_llm_rows(self):
        """정정으로 기준서가 바뀌면 LLM이 넣은 행을 지우고 파서가 다시 뽑아 → 다시 LLM 대상"""
        import shutil, sqlite3, tempfile
        from pathlib import Path
        from backend.db.init_db import init_db
        from backend.db.extract_evaluation_rules import refresh_notice
        from backend.collector.fetch_attachments import llm_targets
        from backend.collector.collect import invalidate_edited_rules
        tmp = tempfile.mkdtemp()
        try:
            path = Path(tmp) / "t.sqlite3"
            shutil.copy(DEFAULT_DB_PATH, path)
            init_db(path).close()
            db = sqlite3.connect(path)
            refresh_notice(db, "E012608163")
            db.execute("UPDATE evaluation_rule SET updated_by='LLM' WHERE notice_no='E012608163' "
                       "AND verification_status='REVIEW'")
            db.commit()
            self.assertNotIn("E012608163", llm_targets(path))
            refresh_notice(db, "E012608163")  # 기준서 그대로면 파서가 LLM 공고를 건드리지 않음
            self.assertGreater(db.execute("SELECT count(*) FROM evaluation_rule WHERE notice_no='E012608163' "
                                          "AND updated_by='LLM'").fetchone()[0], 0)
            self.assertGreater(invalidate_edited_rules(db, "E012608163"), 0)  # 정정 → LLM 행 삭제
            refresh_notice(db, "E012608163")
            db.commit()
            self.assertEqual(db.execute("SELECT count(*) FROM evaluation_rule WHERE notice_no='E012608163' "
                                        "AND updated_by<>'PARSER'").fetchone()[0], 0)
            self.assertIn("E012608163", llm_targets(path))  # 다시 LLM 대상
            db.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(DEFAULT_DB_PATH.exists(), "수집 DB 없음")
class UploadedCriteriaTest(unittest.TestCase):
    """업체가 올린 기준서: 올린 업체만 쓰고, 공용 배점이 없을 때 점수 계산에 쓴다"""

    def test_upload_and_score(self):
        import shutil, sqlite3, tempfile
        from pathlib import Path
        from backend import uploaded_criteria
        from backend.db.init_db import init_db
        tmp = tempfile.mkdtemp()
        try:
            path = Path(tmp) / "t.sqlite3"
            shutil.copy(DEFAULT_DB_PATH, path)
            init_db(path).close()
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            no = "E012608054"
            local = db.execute("SELECT local_path FROM bid_attachment WHERE notice_no=? AND doc_kind='CRITERIA'",
                               (no,)).fetchone()[0]
            data = (DEFAULT_DB_PATH.parent / local).read_bytes()
            admin = {"user_id": None, "is_admin": True}
            shared = db.execute("SELECT count(*) FROM evaluation_rule WHERE notice_no=?", (no,)).fetchone()[0]
            got = uploaded_criteria.upload(db, "C1", no, "기준서.hwp", data, admin)
            self.assertEqual(got["rule_extracted"], 1)
            self.assertEqual(got["evaluation"]["summary"]["source"], "업체 업로드")
            # 공용 배점이 없어진 상황: 업로드한 업체만 그 배점으로 계산된다
            db.execute("DELETE FROM evaluation_rule WHERE notice_no=?", (no,))
            self.assertEqual(scoring.calculate(db, no, {})["items"], [])
            items = scoring.calculate(db, no, {}, company_id="C1")["items"]
            self.assertGreater(len(items), 0)
            self.assertEqual(len(json.loads(db.execute(
                "SELECT rules FROM company_uploaded_criteria WHERE company_id='C1'").fetchone()[0])), shared)
            self.assertEqual(scoring.calculate(db, no, {}, company_id="C2")["items"], [])  # 다른 업체는 못 씀
            with self.assertRaises(uploaded_criteria.UploadError):
                uploaded_criteria.upload(db, "C1", no, "a.zip", b"PK\x03\x04", admin)
            with self.assertRaises(uploaded_criteria.UploadError):
                uploaded_criteria.upload(db, "C1", no, "a.txt", b"hello", admin)
            self.assertEqual(uploaded_criteria.remove(db, "C1", no, admin)["deleted"], 1)
            db.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(DEFAULT_DB_PATH.exists(), "수집 DB 없음")
class CorrectionFlagTest(unittest.TestCase):
    """공고 상세의 정정 여부: SRM 정정 차수 1 = 원공고, 2 이상 = 정정, 모르면 null"""

    def test_revision(self):
        import shutil, sqlite3, tempfile
        from pathlib import Path
        tmp = tempfile.mkdtemp()
        try:
            path = Path(tmp) / "t.sqlite3"
            shutil.copy(DEFAULT_DB_PATH, path)
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            no = "E012606808"
            n = notices_api.get_notice(db, no)
            self.assertEqual((n["revision"], n["corrected"]), (1, False))
            db.execute("UPDATE bid_notice SET srm_revision='2' WHERE notice_no=?", (no,))
            n = notices_api.get_notice(db, no)
            self.assertEqual((n["revision"], n["corrected"]), (2, True))
            db.execute("UPDATE bid_notice SET srm_revision=NULL WHERE notice_no=?", (no,))
            n = notices_api.get_notice(db, no)
            self.assertEqual((n["revision"], n["corrected"]), (None, None))
            db.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class AttachmentFileTest(unittest.TestCase):
    """원본 다운로드: 받아 둔 파일만 돌려주고, 실패·없는 파일·downloads 밖 경로는 None"""

    def test_attachment_file(self):
        import shutil, sqlite3, tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "downloads" / "E1").mkdir(parents=True)
            (tmp / "downloads" / "E1" / "기준서.hwp").write_bytes(b"HWP")
            (tmp / "secret.txt").write_bytes(b"no")
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE bid_attachment (id INTEGER PRIMARY KEY, filename TEXT, local_path TEXT, "
                       "download_status TEXT)")
            db.executemany("INSERT INTO bid_attachment VALUES (?,?,?,?)", [
                (1, "기준서.hwp", "downloads\E1\기준서.hwp", "SUCCESS"),   # Windows 에서 수집한 경로
                (2, "실패.hwp", None, "FAILED"),
                (3, "없음.hwp", "downloads/E1/없음.hwp", "SUCCESS"),
                (4, "secret.txt", "downloads/../secret.txt", "SUCCESS")])
            self.assertEqual(notices_api.attachment_file(db, 1, root=tmp), ("기준서.hwp", b"HWP"))
            for i in (2, 3, 4, 99):
                self.assertIsNone(notices_api.attachment_file(db, i, root=tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class LlmEvaluationTargetTest(unittest.TestCase):
    """수집 끝 LLM 산식 보완 대상(실행당 1건): 규칙 추출 실패 공고를 고르고, LLM 결과가 생기면 다음 공고로 넘어간다"""

    def test_target(self):
        import shutil, sqlite3, tempfile
        from pathlib import Path
        from ai.llm_evaluation import needs_evaluation_fallback
        from backend.collector.collect import llm_evaluation_target
        tmp = tempfile.mkdtemp()
        try:
            path = Path(tmp) / "t.sqlite3"
            shutil.copy(DEFAULT_DB_PATH, path)
            db = sqlite3.connect(path)
            first = llm_evaluation_target(db)
            self.assertIsNotNone(first)
            self.assertTrue(needs_evaluation_fallback(db, first))
            db.execute("UPDATE evaluation_rule SET updated_by='LLM' WHERE notice_no=?", (first,))
            if not db.execute("SELECT 1 FROM evaluation_rule WHERE notice_no=?", (first,)).fetchone():
                db.execute("INSERT INTO evaluation_rule (notice_no, position, category, item_name, rule_type, "
                           "verification_status, updated_by, extracted_at, updated_at) "
                           "VALUES (?, 1, 'PASS', 't', 'PASS_SCORE', 'REVIEW', 'LLM', '', '')", (first,))
            self.assertNotEqual(llm_evaluation_target(db), first)
            db.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class SrmListRowTest(unittest.TestCase):
    """--srm-list: SRM 목록 행을 공식 API 행 모양으로 바꾼다 (취소 공고는 진행 중이 아님)"""

    def test_row(self):
        from backend.collector.collect import in_scope, is_open, srm_list_row
        r = {"announceNo": "E012608290", "announceName": "테스트 공사", "rfxType": "ServiceBid",
             "bidType": "QualifiedEval", "startRequestDate": None, "endRequestDate": "2026-12-01T18:00:00.000+09:00",
             "startProposalDate": "2026-11-24T00:00:00.000+09:00", "endProposalDate": "2026-12-02T10:00:00.000+09:00",
             "announceDate": "2026-11-24T00:00:00.000+09:00", "progressStatus": "NotDetermined",
             "presumedprice": "172533240"}
        row = srm_list_row(r)
        self.assertEqual((row["no"], row["noticeDate"], row["bidAttendReqCloseDatetime"], row["endDatetime"]),
                         ("E012608290", "20261124", "2026-12-01 18:00:00", "2026-12-02 10:00:00"))
        self.assertTrue(in_scope(row))
        self.assertTrue(is_open(row, "2026-11-25 00:00:00"))
        self.assertFalse(is_open(srm_list_row({**r, "progressStatus": "Cancel"}), "2026-11-25 00:00:00"))


class RuleReinforce1125Test(unittest.TestCase):
    """2026-11-25 보강: 변전 전기원 양성·보유, 금액 구간별 시공경험 산식, 【별표】 신인도, ①② 합산 가점, 주석 안 구간"""

    @classmethod
    def setUpClass(cls):
        import sqlite3
        from backend.db.extract_evaluation_rules import extract_notice
        db = sqlite3.connect(DEFAULT_DB_PATH.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        cls.r = {n: extract_notice(db, n) for n in
                 ("E012608197", "E012608263", "E012608270", "E012608295", "E012608280")}
        db.close()

    def test_all_extracted(self):
        for n, r in self.r.items():
            self.assertTrue(r["rule_extracted"], (n, r.get("issues"), r.get("detail_issues")))

    def test_workforce(self):
        wf = [x for x in self.r["E012608197"]["rows"] if x.get("input_key") == "workforce_grade"]
        self.assertEqual(len(wf), 1)
        p = wf[0]["parameters"]
        self.assertEqual((p["trained"], [h["per_unit"] for h in p["held"]]), ({"per_unit": 2.5, "cap": 2.5}, [3.0, 2.5]))
        from backend.scoring import _leaf
        node = {"key": "9", "name": "변전전기원", "category": "PERFORMANCE", "max_score": 3.5, "parameters": p,
                "rule_type": "FORMULA", "input_key": "workforce_grade", "verification_status": "UNVERIFIED",
                "rounding": None}
        self.assertEqual(_leaf(node, {"9": 1, "9_held_0": 1}, {})["score"], 3.5)   # 2.5 + 3.0 = 5.5
        self.assertEqual(_leaf(node, {"9": 3}, {})["score"], 1.2)                  # 양성 상한 2.5
        self.assertEqual(_leaf(node, {"9": 0, "9_held_1": 0}, {})["score"], 0)

    def test_band_formula(self):  # 3.6억 → 3~10억 구간 산식(예비가격기초금액 × 1/2)만
        perf = [x for x in self.r["E012608263"]["rows"] if x.get("input_key") == "performance_amount"]
        self.assertEqual([x["parameters"]["multiplier"] for x in perf], [0.5])

    def test_additive_bonus(self):
        items = [x for x in self.r["E012608295"]["rows"] if x["rule_type"] == "TABLE" and x["category"] == "BONUS"]
        self.assertEqual([x["parameters"]["rows"][0]["score"] for x in items], [2.5, 0.5])


class OfficeConditionTest(unittest.TestCase):
    """숙지도(표 없음): 조건은 본문 평가기준에서, 배점과 다른 점수가 있으면 못 읽음(None)"""

    def test_condition_from_body(self):
        from backend.db.extract_evaluation_rules import office_condition
        body = ["○ 평가기준 : 입찰공고 전일기준 해당 공사현장이 속한 광역시, 특별시, 특별자치도 또는 도에 "
                "법인등기부상 본점소재지(개인사업자인 경우 … 말함)를 두고 있어 … 적용한다.",
                "- 단, 공동계약의 경우 … 시공비율에 배점(4점)을 곱하여 산정한다."]
        self.assertEqual(office_condition(body, 4.0), "입찰공고 전일기준 해당 공사현장이 속한 광역시, 특별시, "
                                                      "특별자치도 또는 도에 법인등기부상 본점소재지")
        self.assertIsNone(office_condition(body + ["- 인접 시·군이면 2점"], 4.0))
        self.assertIsNone(office_condition(["본점이 있으면 배점한다."], 4.0))
