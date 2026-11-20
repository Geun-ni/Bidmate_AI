"""대표 공고 공고문·적격심사기준 확보·읽기 검증 (2번 업무, LLM 미사용).

공고별로 공고문(NOTICE)·적격심사기준(CRITERIA)을 Open API 첨부에서 먼저 받고, 없으면 SRM 전체 첨부에서 보완한다.
받은 파일은 수집기와 같은 doc_reader 로 읽어 문단·수식을 저장한다. SRM 상세의 A금액·경비(overHeadCostAmount)도 함께 기록한다.
DB에는 쓰지 않는다. 파일은 downloads/reference/<공고번호>/ (git 제외), 결과는 --out JSON.

SRM 차단을 피하려고 요청 간격·묶음 휴식을 두고, 차단으로 보이면 저장 후 멈춘다. 다시 실행하면 이어서 한다.
실행 (저장소 루트):
  python -m backend.collector.verify_reference_documents --out 결과.json
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from backend.collector import clients
from backend.collector.collect import ROOT, doc_kind, latest_per_notice, now, to_int
from backend.collector.doc_reader import detect_ext, read_document
from backend.collector.verify_reference_notices import EXPECTED

KINDS = ("NOTICE", "CRITERIA")
SAVE_DIR = ROOT / "downloads" / "reference"


def read_file(name, data):
    """→ (상태, 형식, 문단, 수식, 오류)"""
    ext = detect_ext(name, data)
    try:
        paragraphs, equations = read_document(name, data)
        return "OK", ext, paragraphs, equations, None
    except Exception as exc:
        return "READ_FAILED", ext, None, None, f"{type(exc).__name__}: {exc}"[:300]


def save(no, name, data):
    folder = SAVE_DIR / no
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / re.sub(r'[\\/:*?"<>|]', "_", name)
    path.write_bytes(data)
    return str(path.relative_to(ROOT))


def add_file(entry, kind, no, name, source, data=None, error=None):
    rec = {"filename": name, "source": source}
    if data is None:
        rec.update(status="DOWNLOAD_FAILED", error=error)
    else:
        status, ext, paragraphs, equations, err = read_file(name, data)
        rec.update(status=status, format=ext, size=len(data), path=save(no, name, data), error=err)
        if status == "OK":
            rec.update(paragraphs=paragraphs, equations=equations, equation_count=len(equations))
    entry["files"].setdefault(kind, []).append(rec)
    return rec["status"] == "OK"


def fetch_notice(srm, no, api_row):
    entry = {"files": {}, "attempts": {k: [] for k in KINDS}, "srm": {}}
    ok_kinds = set()
    # 1) Open API 첨부 (SRM 요청 아님)
    if api_row:
        for i in range(1, 6):
            name, link = api_row.get(f"filename{i}"), api_row.get(f"filenlink{i}")
            kind = doc_kind(name)
            if kind and link:
                entry["attempts"][kind].append("OPEN_API")
                try:
                    data = clients.download_open_api_file(link)
                    if add_file(entry, kind, no, name, "OPEN_API", data):
                        ok_kinds.add(kind)
                except Exception as exc:
                    add_file(entry, kind, no, name, "OPEN_API", error=str(exc)[:200])
    else:
        entry["api_note"] = "Open API 조회 기간(최근 90일)에 없음"
    # 2) SRM 상세 (A금액·경비) + 누락 문서 보완
    row = srm.find_bid(no, days=400)
    if row is None:
        entry["srm"]["status"] = "NOT_FOUND"
        return entry
    basic = srm.basic_info(row["id"])
    entry["srm"] = {"status": "OK", "a_amount": to_int(basic.get("totalAAmt")),
                    "overhead_cost_amount": to_int(basic.get("overHeadCostAmount")),  # = 공고문 '경비'
                    "estimated_price_basic_amount": to_int(basic.get("estimatedPriceBasicAmount")),
                    "title": basic.get("name")}
    missing = set(KINDS) - ok_kinds
    if missing and basic.get("attachFileGroupId"):
        atts = srm.attachments(basic["attachFileGroupId"])
        entry["srm"]["attachment_names"] = [a.get("name") for a in atts]
        for att in atts:
            kind = doc_kind(att.get("name"))
            if kind not in missing:
                continue
            entry["attempts"][kind].append("SRM")
            try:
                data = srm.download(att["id"])
                add_file(entry, kind, no, att["name"], "SRM", data)
            except clients.SrmBlocked:
                raise
            except Exception as exc:
                add_file(entry, kind, no, att["name"], "SRM", error=str(exc)[:200])
    return entry


def main():
    ap = argparse.ArgumentParser(description="대표 공고 공고문·적격심사기준 확보·읽기 검증")
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=6.0)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--pause", type=float, default=180.0)
    args = ap.parse_args()

    out = Path(args.out)
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    todo = [no for no in EXPECTED if results.get(no, {}).get("srm", {}).get("status") not in ("OK", "NOT_FOUND")]
    print(f"[{now()}] 대상 {len(todo)}건 (완료 {len(EXPECTED) - len(todo)}건)", flush=True)
    if not todo:
        return
    api = {r["no"]: r for r in latest_per_notice(
        clients.fetch_open_api_notices(clients.load_api_key(ROOT), days=90))}
    try:
        srm = clients.SrmClient(interval=args.interval)
        for i, no in enumerate(todo):
            if i and i % args.batch == 0:
                print(f"  {args.pause:.0f}초 쉬는 중...", flush=True)
                time.sleep(args.pause)
            results[no] = fetch_notice(srm, no, api.get(no))
            out.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
            files = results[no]["files"]
            summary = {k: [f["status"] for f in files.get(k, [])] or "미확보" for k in KINDS}
            print(f"  {no} API={'O' if no in api else 'X'} {summary}", flush=True)
    except clients.SrmBlocked as exc:
        out.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        print(f"[{now()}] SRM 차단 의심으로 중단: {exc} — 다시 실행하면 이어서 합니다.", flush=True)
        print("미완료:", [no for no in EXPECTED if results.get(no, {}).get("srm", {}).get("status") != "OK"])
        sys.exit(2)
    print(f"[{now()}] 완료", flush=True)


if __name__ == "__main__":
    main()
