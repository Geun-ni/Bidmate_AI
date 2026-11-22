"""점수 계산 엔진 테스트 — 실제 수집 DB의 E012608220(배전 1.6억~3억, 기준서 원문과 대조한 공고) 규칙으로 계산한다.

    python -m unittest tests.test_scoring -v

기준서 값: 입찰가격 90 - 20×|(90/100 - (입찰가격-A)/(예정가격-A))×100|, 비율 소수점 다섯째자리 반올림,
90.25% 이상 85점, 최저 2점 / 시공경험 6 = 실적(사정액×1/2 대비, 4점) + 숙지도(2·1·0) / 경영상태 4 /
기준치 부채비율 120.77%·유동비율 142.57% / 통과 95점. A값 10,171,627원, 사정액 271,946,271원.
"""
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
