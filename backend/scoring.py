"""적격심사 예상 점수 계산 (FUNC-019~023). evaluation_rule 계층 + 사용자 입력 → 항목별 점수·합계·통과점수 비교.

원칙 (기능명세서 4.6)
- 입력하지 않은 항목은 0점으로 확정하지 않고 '미산정'으로 둔다. 미산정이 하나라도 있으면 총점·통과 여부를 단정하지 않는다.
- A값·산식·기준금액이 없으면 임의 값을 넣지 않고 '산정 보류'로 둔다.
- 검증 전(REVIEW) 규칙으로 낸 점수는 '원문 확인 필요'를 붙인다.

입력 (inputs, JSON) — 키는 규칙 행 id(문자열) 또는 아래 공통 키
- "bid_price", "planned_price"   : 입찰가격, 예정가격 (원)
- "management_method"           : 경영상태 평가 방식 "ratio"(재무비율) | "credit"(신용평가등급)
- "<규칙 key>"                   : 그 행의 값 (배점 조회 API 의 key = 공고 안 순서. 행 id 는 재추출 때 바뀌어서 쓰지 않음)
    FORMULA 실적 비율 → 금액·규모, 인원당 점수 → 인원, 배치기술자 → 경력일수, 감리 가감점 → 건수
    TABLE 재무비율 → 비율(%) 또는 영업기간(년), 신용평가등급 → 등급 문자열("A0", "BBB+" …)
    TABLE 숙지도 → "해당사업소" | "인접사업소" | "비인접사업소" | "광역 밖"
    TABLE 신인도(input_type) → count: 숫자, check: true/false, select: 등급 번호(0부터) 또는 null(해당 없음)
    FIXED → true/false (필수장비는 보유 장비 이름 목록)
    세부 표가 없는 GROUP → 직접 점수 (배점 범위 안으로 자름)
"""
from backend import notices_api

UNCALC = "미산정"      # 입력이 없음
HOLD = "산정 보류"      # 규칙·기준값이 없어 계산할 수 없음
CREDIT_ORDER = ["AAA", "AA+", "AA0", "AA-", "A+", "A0", "A-", "BBB+", "BBB0", "BBB-", "BB+", "BB0", "BB-",
                "B+", "B0", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D"]


def _round(value, rounding):
    if value is None or not rounding:
        return value
    return round(value + 1e-12 if value >= 0 else value - 1e-12, rounding["digits"])


def _in_row(value, row):
    lo, hi = row.get("min"), row.get("max")
    if lo is not None and (value < lo if row.get("min_inc", True) else value <= lo):
        return False
    if hi is not None and (value > hi if row.get("max_inc", True) else value >= hi):
        return False
    return True


def _match_rows(value, rows):
    """구간표 rows 에서 value 가 들어가는 첫 행"""
    return next((r for r in rows if _in_row(value, r)), None)


def _credit_match(grade, rows):
    """회사채 등급('A0') → 신용평가등급표 행. 'A+이상', 'CCC+이하', 'BB+, BB0', 'AAA / AA+ …' 형식을 처리"""
    g = grade.replace(" ", "").upper()
    if g not in CREDIT_ORDER:
        for r in rows:  # 기업어음 등급(A1, A2+ …)은 표의 기업어음 칸과 그대로 비교
            if g in [x.strip() for x in r.get("기업어음", "").replace(" / ", ",").split(",")]:
                return r
        return None
    gi = CREDIT_ORDER.index(g)
    for r in rows:
        for token in r["회사채"].replace(" / ", ",").split(","):
            t = token.replace(" ", "").upper()
            if t.endswith("이상") and t[:-2] in CREDIT_ORDER and gi <= CREDIT_ORDER.index(t[:-2]):
                return r
            if t.endswith("이하") and t[:-2] in CREDIT_ORDER and gi >= CREDIT_ORDER.index(t[:-2]):
                return r
            if t == g:
                return r
    return None


def _result(node, score=None, status="산정", note=None, **extra):
    out = {"key": node["key"], "name": node["name"], "category": node["category"], "max_score": node["max_score"],
           "score": score, "status": status, "note": note, "children": [], **extra}
    if node["verification_status"] == "REVIEW" and status == "산정":
        out["note"] = ((note + " / ") if note else "") + "원문 확인 필요"
    return out


def _leaf(node, inputs, ctx):
    """세부 행 1개 점수"""
    p, key = node["parameters"] or {}, node["key"]
    value = inputs.get(key)
    rt, ik = node["rule_type"], node["input_key"]
    if rt == "FORMULA" and ik in ("performance_amount", "same_kind_amount") and "denominator" in p:
        base = p.get("base_amount")
        if not base:
            return _result(node, None, HOLD, f"{p['denominator']} 값이 없음")
        if value is None:
            return _result(node, None, UNCALC)
        raw = float(value) / (base * p["multiplier"]) * p["points"]
        return _result(node, _round(min(raw, p["cap"]), node["rounding"]), base_amount=base)
    if rt == "FORMULA" and "per_unit" in p and ik != "bonus_count":  # 인원당 점수
        if value is None:
            return _result(node, None, UNCALC)
        return _result(node, _round(min(float(value) * p["per_unit"], p["cap"]), node["rounding"]))
    if rt == "FORMULA" and ik == "engineer_days":
        if value is None:
            return _result(node, None, UNCALC)
        return _result(node, _round(min(p["cap"] * float(value) / p["days"], p["cap"]), node["rounding"]))
    if rt == "FORMULA" and ik == "bonus_count":  # 감리 가점·감점: 건수 × 건당 점수, 최대점
        if value is None:
            return _result(node, None, UNCALC)
        score = min(float(value) * p["per_unit"], abs(p["limit"])) * p["sign"]
        return _result(node, round(score, 4))
    if rt == "FIXED" and "items" in p:  # 필수장비: 보유 장비 이름 목록
        if value is None:
            return _result(node, None, UNCALC)
        owned = set(value or [])
        return _result(node, sum(i["score"] for i in p["items"] if i["name"] in owned))
    if rt == "FIXED":  # 숙지도(광역 안이면 배점), 결격 감점 등: 해당 여부
        if value is None:
            return _result(node, None, UNCALC)
        return _result(node, (p.get("score") if p.get("score") is not None else node["max_score"]) if value else 0)
    if rt == "TABLE" and ik == "office_location":
        if value is None:
            return _result(node, None, UNCALC)
        if value == "광역 밖":
            return _result(node, p.get("outside_region_score") or 0)
        row = next((r for r in p["rows"] if r["level"] == str(value).replace(" ", "")), None)
        return _result(node, row["score"]) if row else _result(node, None, UNCALC, f"모르는 선택값: {value}")
    if rt == "TABLE" and ik == "credit_grade":
        if value is None:
            return _result(node, None, UNCALC)
        if value in ("", "없음"):
            return _result(node, p.get("no_grade_score", 0), note="유효한 신용평가등급 없음")
        row = _credit_match(str(value), p["rows"])
        return _result(node, row["score"]) if row else _result(node, None, UNCALC, f"표에 없는 등급: {value}")
    if rt == "TABLE" and ik in ("debt_ratio", "current_ratio", "equity_ratio", "business_years"):
        if value is None:
            return _result(node, None, UNCALC)
        v = float(value)
        if p.get("value_unit") == "기준치 배수":
            if not p.get("basis"):
                return _result(node, None, HOLD, "기준치 값이 없음")
            if v < 0:  # 부(-)의 수치는 최저 배점
                return _result(node, min(r["score"] for r in p["rows"]), note="부(-)의 수치 → 최저 배점")
            v = v / p["basis"]
        row = _match_rows(v, p["rows"])
        return _result(node, row["score"]) if row else _result(node, None, UNCALC, "구간에 안 맞음")
    if rt == "TABLE" and ik == "bonus_grade":
        kind = p.get("input_type")
        if key not in inputs:
            return _result(node, None, UNCALC)
        if kind == "count":
            if value is None:
                return _result(node, None, UNCALC)
            row = _match_rows(float(value), p["rows"])
            return _result(node, row["score"] if row else 0, note=None if row else "해당 등급 없음")
        if kind == "check":
            return _result(node, p["rows"][0]["score"] if value else 0)
        if value is None:  # select: 해당 없음
            return _result(node, 0)
        return _result(node, p["rows"][int(value)]["score"])
    if rt == "GROUP" and not node["children"]:  # 세부 표가 없는 분야: 직접 점수
        if value is None:
            return _result(node, None, UNCALC, "세부 규칙 없음 — 점수 직접 입력")
        lo = p.get("min", 0 if (node["max_score"] or 0) >= 0 else node["max_score"])
        hi = p.get("max", node["max_score"] if node["max_score"] is not None else float(value))
        return _result(node, max(lo, min(hi, float(value))), note="직접 입력")
    return _result(node, None, HOLD, f"계산 방법 미지원 ({rt}/{ik})")


def _group(node, inputs, ctx):
    """묶음 점수: 하위 합계를 묶음 한도·가감점 범위로 자른다. 경영상태는 재무비율/신용평가 중 하나만."""
    if not node["children"]:
        return _leaf(node, inputs, ctx)
    p = node["parameters"] or {}
    kids = node["children"]
    note = None
    if p.get("choose_one"):
        method = inputs.get("management_method")
        if method not in ("ratio", "credit"):
            res = _result(node, None, UNCALC, "재무비율/신용평가등급 중 평가 방식 선택 필요")
            res["children"] = [_score(k, inputs, ctx) for k in kids]
            return res
        want = "재무비율" if method == "ratio" else "신용평가등급"
        kids = [k for k in kids if k["name"] == want] or kids
        note = f"{want}로 평가"
    results = [_score(k, inputs, ctx) for k in kids]
    res = _result(node, None, "산정", note)
    res["children"] = results
    pending = [r for r in results if r["status"] != "산정"]
    if pending:
        res["status"] = HOLD if any(r["status"] == HOLD for r in pending) else UNCALC
        return res
    total = sum(r["score"] for r in results)
    scale = p.get("scale")
    if scale:  # '득점 = (①+②+③) × 15/45'
        total = total * scale["numerator"] / scale["denominator"]
    if node["category"] == "BONUS" and ("min" in p or "max" in p):
        total = max(p.get("min", total), min(p.get("max", total), total))
    elif node["max_score"] is not None and node["category"] != "BONUS":
        total = min(total, node["max_score"])  # 분야·수행능력 배점 한도
    res["score"] = round(total, 4)
    return res


def _score(node, inputs, ctx):
    return _group(node, inputs, ctx) if node["rule_type"] == "GROUP" else _leaf(node, inputs, ctx)


def _price(node, inputs, ctx):
    """입찰가격 점수 (FUNC-020 1안): 사용자 입찰가격·예정가격 + 공고 A값"""
    p = node["parameters"]
    if not p or node["formula"] is None:
        return _result(node, None, HOLD, "입찰가격 산식 없음 — 원문 확인 필요")
    bid, plan = inputs.get("bid_price"), inputs.get("planned_price")
    if bid is None or plan is None:
        return _result(node, None, UNCALC, "입찰가격·예정가격 입력 필요")
    bid, plan = float(bid), float(plan)
    a = 0.0
    if p.get("subtract_a"):
        if ctx["a_amount"] is None:
            return _result(node, None, HOLD, "A값이 없음")
        a = float(ctx["a_amount"])
    if plan - a <= 0:
        return _result(node, None, HOLD, "예정가격이 A값 이하")
    ratio = _round((bid - a) / (plan - a), node["rounding"])
    base, coef, std = node["max_score"], p["vars"]["계수"], p["vars"]["기준율"]
    score = base - coef * abs(std / 100 - ratio) * 100
    note = None
    cap = p.get("cap")
    if cap and bid <= plan and ratio * 100 >= cap["ratio"] - 1e-9:
        score, note = cap["score"], f"예정가격의 {cap['ratio']:g}% 이상 → {cap['score']:g}점"
    if p.get("min_score") is not None and score < p["min_score"]:
        score, note = p["min_score"], f"최저평점 {p['min_score']:g}점"
    if bid > plan:
        note = ((note + " / ") if note else "") + "입찰가격이 예정가격보다 큼 (낙찰 대상 아님)"
    return _result(node, round(score, 4), note=note, ratio=ratio, a_amount=a if p.get("subtract_a") else None)


def calculate(db, notice_no, inputs):
    """→ {status, total, pass_score, gap, items:[…]} 또는 None(공고 없음)"""
    ev = notices_api.get_evaluation(db, notice_no)
    if ev is None:
        return None
    if not ev["rules"]:
        return {"notice_no": notice_no, "status": HOLD, "reason": ev["reason"], "total": None, "items": []}
    a = db.execute("SELECT a_amount FROM bid_notice WHERE notice_no=?", (notice_no,)).fetchone()
    ctx = {"a_amount": a[0] if a else None}
    items, pass_score = [], None
    for node in ev["rules"]:
        if node["category"] == "PASS_SCORE":
            pass_score = node["parameters"]["pass_score"]
        elif node["category"] == "PRICE":
            items.append(_price(node, inputs, ctx))
        else:
            items.append(_score(node, inputs, ctx))
    pending = [i["name"] for i in items if i["status"] != "산정"]
    total = None if pending else round(sum(i["score"] for i in items), 4)
    out = {"notice_no": notice_no, "pass_score": pass_score, "total": total,
           "status": "CALCULATED" if total is not None else "IN_PROGRESS",
           "pending": pending, "items": items,
           "notice": "참고용 시뮬레이션입니다. 실제 점수·낙찰 여부는 공고문·적격심사기준과 발주기관 심사를 따릅니다."}
    if total is not None and pass_score is not None:
        out["gap"] = round(total - pass_score, 4)
        out["passed"] = total >= pass_score
    return out
