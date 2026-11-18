"""SRM OPEN API 첨부파일 누락 조사 (PoC, 운영용 아님).

1) OPEN API(electContract.do)로 최근 N일 공고를 받고
2) 적격심사 공사용역 공고 일부를 SRM 웹 전체 첨부목록과 대조해
   API 5개 안에 공고문/적격심사기준이 들어 있는지 기록한다.

실행 (프로젝트 루트에서):
    python docs/planning/poc/api_attachment_survey.py --sample 10 --interval 3
API 키: 프로젝트 루트 .env 의 SRM_API_KEY=... (git 제외)
주의: SRM 웹은 연속 요청 시 차단(404)될 수 있으니 표본은 작게, 간격은 넉넉히.
"""
import argparse
import importlib.util
import json
import random
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
API_URL = "https://bigdata.kepco.co.kr/openapi/v1/electContract.do"

spec = importlib.util.spec_from_file_location("poc", HERE / "kepco_srm_poc.py")
poc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poc)


def api_key():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("SRM_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(".env 에 SRM_API_KEY 가 없습니다.")


def has(names, word):
    return any(word in re.sub(r"\s+", "", n or "") for n in names)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--sample", type=int, default=10)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--out", default="downloads/api_attachment_survey.json")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    res = requests.get(API_URL, timeout=60, params={
        "companyId": "COM01", "noticeBeginDate": start.strftime("%Y%m%d"),
        "noticeEndDate": end.strftime("%Y%m%d"), "apiKey": api_key(), "returnType": "json"})
    res.raise_for_status()
    rows = res.json().get("data", [])
    print(f"API 공고 {len(rows)}건, bidAttendRestrict 값 있는 건: "
          f"{sum(1 for r in rows if r.get('bidAttendRestrict') not in (None, '', '-'))}")

    pool = [r for r in rows if r.get("bidType") == "QualifiedEval"
            and r.get("purchaseType") == "ConstructionService"]
    sample = random.sample(pool, min(args.sample, len(pool)))

    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": poc.BASE + "/index.do", "Origin": poc.BASE})
    poc.configure_csrf(s, s.get(poc.BASE + "/index.do", timeout=35).text)

    results, tid = [], 100
    for r in sample:
        tid += 3
        api_names = [r.get(f"filename{i}") for i in range(1, 6) if r.get(f"filename{i}")]
        rec = {"no": r["no"], "name": r.get("name"), "api_files": api_names}
        try:
            q = {"companyId": "COM01", "fromSearchDate": (end - timedelta(days=60)).isoformat() + "T00:00:00",
                 "toSearchDate": end.isoformat() + "T23:59:59", "searchDateType": "NoticeDateTime",
                 "searchType": "AnnounceNo", "searchText": r["no"], "page": 1, "start": 0,
                 "limit": 100, "totalCount": None}
            cands = poc.records(poc.rpc(s, poc.SEARCH, "getPageList", [q], tid))
            row = ([c for c in cands if r["no"] in json.dumps(c, ensure_ascii=False)] or cands)[0]
            detail = poc.rpc(s, poc.DETAIL, "findBidBasicInfo", [row.get("id") or row.get("bidId")], tid + 1)
            atts = poc.records(poc.rpc(s, poc.COMMON, "getAttachments",
                                       [{"groupId": detail["attachFileGroupId"], "page": 1,
                                         "start": 0, "limit": 100}], tid + 2))
            web_names = [a.get("name") for a in atts]
            rec.update(web_files=web_names, area=row.get("areaCodeName"),
                       licenseAutoEvalType=detail.get("licenseAutoEvalType"),
                       api_notice=has(api_names, "공고문"), api_criteria=has(api_names, "적격심사"),
                       web_notice=has(web_names, "공고문"), web_criteria=has(web_names, "적격심사"))
        except Exception as exc:  # 차단 시 중단
            rec["error"] = str(exc)[:200]
            results.append(rec)
            print(r["no"], "오류 → 중단:", rec["error"])
            break
        results.append(rec)
        print(r["no"], f"웹 {len(web_names)}개 | API 공고문={rec['api_notice']} 적격심사={rec['api_criteria']}")
        time.sleep(args.interval)

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    done = [x for x in results if "error" not in x]
    both = sum(1 for x in done if x["api_notice"] and x["api_criteria"])
    print(f"완료 {len(done)}건 중 API 5개 안에 두 파일 모두: {both}건 → {out}")


if __name__ == "__main__":
    main()
