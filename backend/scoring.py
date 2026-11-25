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
    감리 PQ: 경력·중복 개소 → 숫자(pq_grade count), 비상주 등급 → 번호(pq_grade select), 유사용역 → 실적 합계(원),
             자본·유동비율 → 업체 비율(%), 상주 중복배치 없음 → true/false
    FORMULA 사업수행능력 → 평가점수(0~100)
    FIXED → true/false (필수장비는 보유 장비 이름 목록)
    세부 표가 없는 GROUP → 직접 점수 (배점 범위 안으로 자름)
"""
import math

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
        sp, sp_value = p.get("special"), inputs.get(key + "_special")  # 'OF전기원은 1인 3.0점'처럼 따로 세는 인원
        if value is None and sp_value is None:
            return _result(node, None, UNCALC)
        raw = float(value or 0) * p["per_unit"] + (float(sp_value or 0) * sp["per_unit"] if sp else 0)
        return _result(node, _round(min(raw, p["cap"]), node["rounding"]))
    if rt == "FORMULA" and ik == "workforce_grade":  # 변전 전기원: 양성(상한) + 보유(등급별) → 평점합계 구간 점수
        held = [inputs.get(f"{key}_held_{i}") for i in range(len(p["held"]))]  # 보유 인원 (예: 1급, 2급 순)
        if value is None and all(h is None for h in held):
            return _result(node, None, UNCALC)
        tr = p["trained"]
        total = min(float(value or 0) * tr["per_unit"], tr["cap"]) + sum(
            float(c or 0) * h["per_unit"] for c, h in zip(held, p["held"]))
        row = next((r for r in p["rows"] if total >= r["min"] - 1e-9), None)  # rows: min 큰 순
        return _result(node, row["score"] if row else 0, note=f"평점합계 {total:g}점")
    if rt == "FORMULA" and ik == "engineer_days":
        if value is None:
            return _result(node, None, UNCALC)
        score = min(p["cap"] * float(value) / p["days"], p["cap"])
        short = p.get("short_tenure")
        if short and inputs.get(key + "_short"):  # 재직 기간이 짧으면 평가점수의 일부만 인정
            return _result(node, _round(score * short["ratio"], node["rounding"]),
                           note=f"재직 {short['days']}일 미만 → {short['ratio']:.0%} 인정")
        return _result(node, _round(score, node["rounding"]))
    if rt == "FORMULA" and ik == "pq_score":  # 사업수행능력: 평가점수/100 × 배점
        if value is None:
            return _result(node, None, UNCALC)
        v = float(value)
        if not 0 <= v <= p["full"]:
            return _result(node, None, UNCALC, f"평가점수는 0~{p['full']:g}")
        return _result(node, _round(v / p["full"] * p["points"], node["rounding"]))
    if rt == "FORMULA" and ik == "bonus_count":  # 감리 가점·감점: 건수 × 건당 점수, 최대점
        if value is None:
            return _result(node, None, UNCALC)
        count = float(value)
        if p.get("period"):  # 'N월마다 X점씩': 입력은 기간(월), N월 단위로 센다 ('N월 미만은 N월로' 이면 올림)
            count = math.floor(count / p["period"])
            if p.get("round_up") and float(value) > 0 and count == 0:
                count = 1
        score = min(count * p["per_unit"], abs(p["limit"])) * p["sign"]
        return _result(node, round(score, 4))
    if rt == "FORMULA" and ik == "bonus_ratio":  # 교육훈련: Σ (이수인원 / 평가대상인원) × 점수, 최대점
        total = inputs.get(key + "_total")
        counts = [inputs.get(f"{key}_{i}") for i in range(len(p["terms"]))]
        if not total or all(c is None for c in counts):
            return _result(node, None, UNCALC, f"{p['denominator']}와 {p['numerator']} 입력 필요")
        raw = sum(float(c or 0) / float(total) * t["points"] for c, t in zip(counts, p["terms"]))
        return _result(node, round(min(raw, p["limit"]) * p["sign"], 4))
    if rt == "FORMULA" and ik == "tech_dev":  # 개발실적: 건마다 건당 점수 × 경과기간 비율 (÷ 출원자 수), 최대점
        if value is None:
            return _result(node, None, UNCALC)
        total = 0.0
        for it in value:  # [{kind: 종류 번호, years: 경과 연수, inventors: 출원자 수, non_field: 배전분야 아님}]
            kind = p["kinds"][int(it["kind"])]
            band = next((i for i, b in enumerate(p["age_bounds"]) if float(it["years"]) <= b), None)
            pct = kind["age_pct"][band] if band is not None and band < len(kind["age_pct"]) else None
            if pct is None:
                continue  # 원문 표에 없는 연수('-' 또는 칸 없음)는 인정 안 함
            pts = kind["per_unit"] * pct
            if it.get("non_field") and p.get("non_field_ratio"):
                pts *= p["non_field_ratio"]
            if p.get("split_by_inventors"):
                pts /= max(int(it.get("inventors") or 1), 1)
            total += pts
        return _result(node, round(min(total, p["limit"]), 4))
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
    if rt == "TABLE" and ik in ("debt_ratio", "current_ratio", "equity_ratio", "business_years", "similar_amount"):
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
    if rt == "TABLE" and ik == "pq_grade":  # 감리 PQ: 구간(count) 또는 등급 번호(select)
        if value is None:
            return _result(node, None, UNCALC)
        if p.get("input_type") == "select":
            return _result(node, p["rows"][int(value)]["score"])
        row = _match_rows(float(value), p["rows"])
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
        mx = node["max_score"]
        lo = p.get("min", 0 if (mx or 0) >= 0 else mx)
        hi = p.get("max", float(value) if mx is None else mx if mx >= 0 else 0)  # 감점 항목은 [감점 한도, 0]
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


def calculate(db, notice_no, inputs, company_id=None):
    """→ {status, total, pass_score, gap, items:[…]} 또는 None(공고 없음).
    company_id: 공용 배점이 없을 때 그 업체가 올린 기준서(company_uploaded_criteria) 배점을 쓴다 (권한 확인은 호출자)."""
    ev = notices_api.get_evaluation(db, notice_no)
    if ev is None:
        return None
    if not ev["rules"] and company_id:
        from backend import uploaded_criteria
        ev = uploaded_criteria.evaluation(db, company_id, notice_no) or ev
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
