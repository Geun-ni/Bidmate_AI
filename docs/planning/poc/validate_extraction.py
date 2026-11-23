"""적격심사기준 추출값 자동 검증 PoC (규칙 기반, LLM 미사용).

LLM 등이 적격심사기준 파일에서 뽑은 값(JSON)을 Open API 값과 원문에 대조해
공고별로 '자동 반영' 또는 '확인 필요'를 판정한다.

검사 항목
  1. 사정액 = API 기초금액(estimatedPriceBasicAmount 등)인지
  2. API 추정가격(presumedPrice, VAT 제외)이 산식 적용 금액 구간 안에 있는지
  3. 값마다 적은 '근거' 문장이 원문(본문+수식 스크립트)에 실제로 있는지
  4. 산식 상수·계수가 근거 문자열과 일치, A 차감 여부 일치, 특례점수 < 상수 > 최저평점,
     특례 비율이 원문에 있는지
  5. 낙찰방법(bidType)이 적격심사(QualifiedEval)인지

입력 JSON 형식 (공고번호별):
{
  "E012607925": {
    "사정액": {"값": 503486765, "근거": "사정액(VAT포함) : ￦503,486,765원"},   # 원문에 없으면 값 null
    "적용금액구간": {"이상": 300000000, "미만": 1000000000, "근거": "..."},      # 미정 경계는 null
    "입찰가격산식": {"상수": 70, "계수": 20, "기준비율": 0.90, "A차감": true, "근거": "<수식 스크립트 또는 본문>"},
    "특례": {"비율이상": 0.9025, "평점": 65, "근거": "..."},
    "최저평점": {"값": 2, "근거": "..."},
    "통과점수": {"값": 95, "근거": "..."}
  }
}
산식을 뽑지 못했으면 "입찰가격산식": null.

실행 (프로젝트 루트):
  python docs/planning/poc/validate_extraction.py extracted.json <적격심사기준 폴더> [--api-json rows.json] [--days 60]
  - 적격심사기준 폴더에는 파일명이 공고번호로 시작하는 파일(E012607925.hwp 등)을 둔다.
  - --api-json 이 없으면 .env 의 SRM_API_KEY 로 Open API(최근 --days 일)를 조회한다.
"""
import argparse
import importlib.util
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
API_URL = "https://bigdata.kepco.co.kr/openapi/v1/electContract.do"
AMOUNT_FIELDS = ("estimatedPriceBasicAmount", "subcontractBudgetAmount", "approximateAmount")
EVIDENCE_KEYS = ("사정액", "적용금액구간", "입찰가격산식", "특례", "최저평점", "통과점수")

_spec = importlib.util.spec_from_file_location("extract_all", HERE / "extract_all.py")
extract_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract_all)


def norm(s):
    return re.sub(r"\s+", "", s or "")


def load_api_rows(api_json, days):
    if api_json:
        return json.loads(Path(api_json).read_text(encoding="utf-8"))
    import requests
    key = next((l.split("=", 1)[1].strip() for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
                if l.startswith("SRM_API_KEY=")), None)
    if not key:
        sys.exit(".env 에 SRM_API_KEY 가 없습니다.")
    end = date.today()
    res = requests.get(API_URL, timeout=60, params={
        "companyId": "COM01", "noticeBeginDate": (end - timedelta(days=days)).strftime("%Y%m%d"),
        "noticeEndDate": end.strftime("%Y%m%d"), "apiKey": key, "returnType": "json"})
    res.raise_for_status()
    return res.json().get("data", [])


def raw_text(files_dir, notice_no):
    """원문 = 본문 문단 + 수식 스크립트 (공백 제거)"""
    matches = [f for f in Path(files_dir).iterdir()
               if f.name.startswith(notice_no) and f.suffix.lower() in extract_all.READERS]
    if not matches:
        return None
    equations, paragraphs, _ = extract_all.READERS[matches[0].suffix.lower()](matches[0])
    return norm("\n".join(paragraphs) + "\n" + "\n".join(e["script"] for e in equations))


def check(api, e, raw):
    res = []  # (항목, 상태, 설명)  상태: PASS / PASS*(부분 검사) / SKIP / CHECK

    v = e["사정액"]["값"]
    if v is None:
        res.append(("사정액", "SKIP", "원문에 금액 없음/양식 빈칸"))
    else:
        hits = [f for f in AMOUNT_FIELDS if api.get(f) not in (None, "") and int(api[f]) == v]
        res.append(("사정액", "PASS" if hits else "CHECK",
                    f"{v:,} = API {hits[0]}" if hits else f"{v:,} ≠ API {int(api['estimatedPriceBasicAmount']):,}"))

    r, p = e["적용금액구간"], int(api["presumedPrice"])
    lo, hi = r["이상"], r["미만"]
    if lo is None or hi is None:
        ok = (lo is None or p >= lo) and (hi is None or p < hi)
        res.append(("금액구간", "PASS*" if ok else "CHECK", f"추정가격 {p:,} (한쪽 경계 미정 → 반쪽만 검사)"))
    else:
        res.append(("금액구간", "PASS" if lo <= p < hi else "CHECK", f"추정가격 {p:,} in [{lo:,}, {hi:,})"))

    bad = [k for k in EVIDENCE_KEYS
           if e.get(k) and e[k].get("근거") is not None and norm(e[k]["근거"]) not in raw]
    res.append(("근거", "PASS" if not bad else "CHECK", "모두 원문에 존재" if not bad else f"원문에 없음: {bad}"))

    f = e["입찰가격산식"]
    if f is None:
        res.append(("산식", "CHECK", "산식 추출 불가 → 원문 확인 필요"))
    else:
        ev = norm(f["근거"]).replace("~", "").replace("`", "").replace("–", "-")
        nums_ok = re.search(rf"{f['상수']}-{f['계수']}", ev) is not None
        a_ok = ("-A" in ev) == f["A차감"] if "over" in ev else True  # 수식 스크립트일 때만 A 여부 대조
        sp = e["특례"]
        order_ok = e["최저평점"]["값"] < sp["평점"] < f["상수"]
        ratio_ok = norm(f"{sp['비율이상'] * 100:g}") in raw
        ok = nums_ok and a_ok and order_ok and ratio_ok
        res.append(("산식", "PASS" if ok else "CHECK",
                    f"{f['상수']}−{f['계수']}×|{f['기준비율']}−비율|×100, 특례 {sp['비율이상'] * 100:g}%→{sp['평점']}점"
                    + ("" if ok else f" [숫자일치={nums_ok}, A={a_ok}, 대소관계={order_ok}, 특례비율원문={ratio_ok}]")))

    res.append(("낙찰방법", "PASS" if api.get("bidType") == "QualifiedEval" else "CHECK", api.get("bidType")))
    return res


def main():
    ap = argparse.ArgumentParser(description="적격심사기준 추출값 자동 검증")
    ap.add_argument("extracted", help="추출 결과 JSON")
    ap.add_argument("files_dir", help="적격심사기준 파일 폴더(파일명이 공고번호로 시작)")
    ap.add_argument("--api-json", help="저장해 둔 Open API 응답 data 목록(JSON)")
    ap.add_argument("--days", type=int, default=60, help="API 직접 조회 시 기간(최대 90)")
    args = ap.parse_args()

    extracted = {k: v for k, v in json.loads(Path(args.extracted).read_text(encoding="utf-8")).items()
                 if not k.startswith("_")}
    api = {r["no"]: r for r in load_api_rows(args.api_json, args.days)}

    verdicts = {}
    for no in sorted(extracted):
        if no not in api:
            print(f"\n■ {no}  →  건너뜀 (API 조회 결과에 없음)")
            continue
        raw = raw_text(args.files_dir, no)
        if raw is None:
            print(f"\n■ {no}  →  건너뜀 (원문 파일 없음)")
            continue
        res = check(api[no], extracted[no], raw)
        verdict = "확인 필요" if any(s == "CHECK" for _, s, _ in res) else "자동 반영"
        verdicts[no] = verdict
        print(f"\n■ {no}  →  {verdict}")
        for k, s, d in res:
            print(f"   {s:6} {k:5} {d}")
    print("\n요약:", {v: sum(1 for x in verdicts.values() if x == v) for v in sorted(set(verdicts.values()))})


if __name__ == "__main__":
    main()
