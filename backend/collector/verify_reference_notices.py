"""대표 공고로 투찰제한(자동심사 조건) 수집 검증.

기획 정리본(공사유형별 대표 공고번호)의 기대값과 SRM 투찰제한 수집 결과를 비교한다.
수집기와 같은 클라이언트(clients.SrmClient)와 같은 해석 함수(collect.parse_restriction_item)를 쓴다.
DB에는 쓰지 않고 결과를 JSON으로 저장한다. 첨부는 받지 않는다(공고당 SRM 요청 6번).

SRM 차단을 피하려고 요청 간격(--interval)을 두고, --batch 건마다 --pause 초 쉰다.
차단으로 보이면 그때까지의 결과를 저장하고 멈춘다. 다시 실행하면 남은 공고부터 이어서 한다.

실행 (저장소 루트):
  python -m backend.collector.verify_reference_notices --out 결과.json
  python -m backend.collector.verify_reference_notices --out 결과.json --report   # 수집 없이 비교표만
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from backend.collector import clients
from backend.collector.collect import CATEGORIES, EVAL_KEYS, camel, now, parse_restriction_item, sanitize

# 기획 정리본(11-19) 기준 기대값. license/qualified 는 이름에 들어 있어야 하는 키워드(공백 무시)
# license_type="Or" 는 면허 결합 방식이 OR 여야 함, region="NONE" 은 지역 제한이 없어야 함
EXPECTED = {
    "E012605917": {"type": "배전공사 총액계약 1.6억~3억", "license": ["전기공사업"]},
    "E012608056": {"type": "배전공사 총액계약 3억~10억", "license": ["전기공사업"]},
    "E012601740": {"type": "배전공사 총액계약 10억~50억", "license": ["전기공사업"], "region": "NONE"},
    "E012605388": {"type": "배전공사 감리용역 ~4천만", "license": ["감리"]},
    "E012607867": {"type": "배전공사 감리용역 4천만~1억", "license": ["감리"]},
    "E012608071": {"type": "배전공사 감리용역 1억~2.3억", "license": ["감리"]},
    "E012603596": {"type": "배전공사 감리용역 2.3억 이상(PQ)", "license": ["감리"]},
    "E012607179": {"type": "정보통신공사 3억~50억", "license": ["정보통신공사업"]},
    "E012607232": {"type": "정보통신공사 8천~3억", "license": ["정보통신공사업"]},
    "E012606812": {"type": "변전 건설공사 8천~3억", "qualified": ["변압기2급"]},
    "E012606905": {"type": "변전 건설공사 8천~3억", "qualified": ["345"]},
    "E012608013": {"type": "변전 건설공사 3억~50억", "qualified": ["변압기2급"]},
    "E012607141": {"type": "변전 건설공사 3억~50억", "qualified": ["개폐기1급"]},
    # 11-19 담당자 직접 확인: 정리본에 조건 명시가 없던 2건은 수집 결과가 맞음
    "E012604973": {"type": "가공송전선로 건설공사 8천~3억", "license": ["전기공사업"]},
    "E012607077": {"type": "가공송전선로 건설공사 3억~50억", "license": ["전기공사업"], "region": "NONE"},
    "E012607127": {"type": "가공송전선로 건설공사 50억~100억", "qualified": ["복도체"]},
    "E012606980": {"type": "지중송전공사 8천~3억", "qualified": ["154kV지중송전"]},
    "E012607149": {"type": "지중송전공사 3억~50억", "qualified": ["154kV지중송전"]},
    "E012606227": {"type": "지중송전공사 50억~100억", "qualified": ["154kV지중송전"]},
    "E012608116": {"type": "공중 배전설비 진단용역 ~2.3억", "license": ["비파괴검사업"]},
    "E012604208": {"type": "공중 배전설비 진단용역 2.3억~5억",
                   "license": ["엔지니어링사업(전기설비)", "엔지니어링사업(전기전자응용)"], "license_type": "Or"},
    # 11-19 담당자 직접 확인: 정리본의 '엔지니어링사업(전기전자응용)'은 실제로 '기술사사무소(전기전자응용)'(7372)
    "E012606484": {"type": "지중 배전설비 진단용역 ~2.3억",
                   "license": ["기술사사무소(발송배전)", "기술사사무소(철도신호)",
                               "엔지니어링사업(전기설비)", "기술사사무소(전기전자응용)"], "license_type": "Or"},
    "E012603958": {"type": "지중 배전설비 진단용역 2.3억~5억",
                   "license": ["기술사사무소(발송배전)", "기술사사무소(철도신호)",
                               "엔지니어링사업(전기설비)", "기술사사무소(전기전자응용)"], "license_type": "Or"},
}


def norm(s):
    return re.sub(r"\s+", "", (s or "")).lower()


def fetch(srm, notice_no):
    row = srm.find_bid(notice_no, days=400)
    if row is None:
        return {"status": "NOT_FOUND"}
    basic = srm.basic_info(row["id"])
    result = {"status": "OK", "title": basic.get("name") or row.get("announceName"),
              "bid_type": row.get("bidType"), "area_name": row.get("areaCodeName"),
              "evals": {k: basic.get(camel(k)) for k in EVAL_KEYS}, "items": {}, "raw_keys": {}, "errors": {}}
    for cat in CATEGORIES:
        try:
            rows = srm.restriction(row["id"], cat)
        except clients.SrmBlocked:
            raise
        except Exception as exc:  # 범주 조회 실패는 기록하고 계속
            result["errors"][cat] = str(exc)[:200]
            continue
        result["items"][cat] = [parse_restriction_item(cat, it) for it in rows]
        result["raw_keys"][cat] = sorted({k for it in sanitize(rows) for k in it})  # 주력분야 응답 필드명 확인용
    return result


def compare(no, exp, res):
    """→ (판정, 메모 목록). 판정: 일치 / 불일치 / 수집 실패 / 기대값 없음(조회만)"""
    if res.get("status") != "OK":
        return "수집 실패", [res.get("status", "미수집")]
    notes, ok = [], True
    names = {cat: [norm(i[1]) for i in res["items"].get(cat, [])] for cat in CATEGORIES}
    for key, cat in (("license", "LICENSE"), ("qualified", "QUALIFIED")):
        for word in exp.get(key, []):
            if not any(norm(word) in n for n in names[cat]):
                ok = False
                notes.append(f"{cat}에 '{word}' 없음")
    if exp.get("license_type") and res["evals"].get("license_auto_eval_type") != exp["license_type"]:
        ok = False
        notes.append(f"면허 결합 {res['evals'].get('license_auto_eval_type')} (기대 {exp['license_type']})")
    if exp.get("region") == "NONE" and res["items"].get("REGION"):
        ok = False
        notes.append("지역 제한이 있음 (기대: 없음)")
    if res["errors"]:
        ok = False
        notes.append(f"조회 실패 범주 {sorted(res['errors'])}")
    has_expectation = any(k in exp for k in ("license", "qualified", "license_type", "region"))
    if not has_expectation:
        return "조회만(기대값 없음)", notes
    return ("일치" if ok else "불일치"), notes


LABEL = {"LICENSE": "면허", "QUALIFIED": "적격자격", "REGION": "지역", "LICENSE_MAIN": "주력분야"}
EVAL_OF = {"LICENSE": ("license_auto_eval_method", "license_auto_eval_type"),
           "QUALIFIED": ("qualified_auto_eval_method", "qualified_auto_eval_type"),
           "REGION": ("region_limit_auto_eval_method", "region_limit_auto_eval_type"),
           "LICENSE_MAIN": (None, "license_main_auto_eval_type")}


def fmt_items(res, cat):
    """조회 실패 / 제한 없음(조회 성공·0건) / 값 목록을 구분해 표시."""
    if cat in res.get("errors", {}):
        return "조회 실패"
    items = res.get("items", {}).get(cat)
    if items is None:
        return "미조회"
    if not items:
        return "제한 없음(조회 성공·0건)"
    if cat == "REGION":
        return ", ".join(f"{i[1]}({i[2]}{'/' + i[3] if i[3] else ''})" for i in items)
    return ", ".join(f"{i[1]}({i[0]})" for i in items)


def fmt_actual(res):
    if res.get("status") != "OK":
        return f"미확인({res.get('status', '미수집')})"
    ev, parts = res.get("evals", {}), []
    for cat in ("LICENSE", "QUALIFIED", "REGION"):
        method_key, type_key = EVAL_OF[cat]
        setting = "/".join(str(ev.get(k)) for k in (method_key, type_key) if k)
        parts.append(f"{LABEL[cat]}: {fmt_items(res, cat)} [{setting}]")
    return "<br>".join(parts)


def fmt_expected(exp):
    parts = []
    if exp.get("license"):
        joiner = " OR " if exp.get("license_type") == "Or" else ", "
        parts.append("면허: " + joiner.join(exp["license"]))
    if exp.get("qualified"):
        parts.append("적격자격: " + ", ".join(exp["qualified"]))
    if exp.get("region") == "NONE":
        parts.append("지역 제한 없음")
    return "<br>".join(parts) or "명시 없음(유형만)"


def report(results):
    """→ (비교표, 판정 집계, 주력분야 보고)."""
    lines = ["| 공고번호 | 유형 | 영래 정리본 기대값 | 수집기 해석 값 | 판정 | 차이 원인 |",
             "|---|---|---|---|---|---|"]
    tally, license_main = {}, []
    for no, exp in EXPECTED.items():
        res = results.get(no, {"status": "미수집"})
        verdict, notes = compare(no, exp, res)
        verdict = {"수집 실패": "미확인", "조회만(기대값 없음)": "조회 성공(기대값 없음)"}.get(verdict, verdict)
        tally[verdict] = tally.get(verdict, 0) + 1
        lines.append(f"| {no} | {exp['type']} | {fmt_expected(exp)} | {fmt_actual(res)} | {verdict} | "
                     f"{'; '.join(notes) or '-'} |")
        if res.get("status") == "OK":
            license_main.append((no, fmt_items(res, "LICENSE_MAIN"), res.get("evals", {}).get("license_main_auto_eval_type"),
                                 res.get("raw_keys", {}).get("LICENSE_MAIN", [])))
    return "\n".join(lines), tally, license_main


def main():
    ap = argparse.ArgumentParser(description="대표 공고 투찰제한 수집 검증")
    ap.add_argument("--out", required=True, help="결과 JSON (이어서 실행할 때 같은 파일)")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--pause", type=float, default=120.0)
    ap.add_argument("--report", action="store_true", help="수집 없이 비교표만 출력")
    args = ap.parse_args()

    out = Path(args.out)
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    todo = [no for no in EXPECTED if results.get(no, {}).get("status") != "OK"]
    if not args.report and todo:
        print(f"[{now()}] 수집 대상 {len(todo)}건 (이미 완료 {len(EXPECTED) - len(todo)}건)", flush=True)
        try:
            srm = clients.SrmClient(interval=args.interval)
            for i, no in enumerate(todo):
                if i and i % args.batch == 0:
                    print(f"  {args.pause:.0f}초 쉬는 중...", flush=True)
                    time.sleep(args.pause)
                results[no] = fetch(srm, no)
                out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"  {no} {results[no]['status']}", flush=True)
        except clients.SrmBlocked as exc:
            out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[{now()}] SRM 차단 의심으로 중단: {exc} — 다시 실행하면 이어서 합니다.", flush=True)
            print_report(results)
            sys.exit(2)
    print_report(results)


def print_report(results):
    table, tally, license_main = report(results)
    print(table)
    print("\n요약:", tally)
    print("미확인 공고:", [no for no in EXPECTED if results.get(no, {}).get("status") != "OK"])
    print("\n[주력분야 제한]")
    restricted = [r for r in license_main if not r[1].startswith("제한 없음")]
    for no, value, eval_type, keys in restricted:
        print(f"  {no}: {value} / licenseMainAutoEvalType={eval_type} / 응답 필드={keys}")
    print(f"  제한 있음 {len(restricted)}건 / 조회한 {len(license_main)}건"
          + ("" if restricted else " → 대표 공고에서 제한 사례 미확인, 응답 필드 미검증"))


if __name__ == "__main__":
    main()
