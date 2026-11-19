"""SRM 공고 수집 실행.

흐름 (기능명세서 FUNC-005~008, 11-18 회의 결정):
  1. Open API로 최근 공고 목록 조회 → bid_notice 저장/갱신
  2. 아직 수집이 끝나지 않은 공고만 1회 처리
     a. 공고문·적격심사기준: Open API 첨부(filenlink)에서 먼저 받고, 없으면 SRM 전체 첨부에서 보완
     b. SRM 투찰제한: 자동심사 설정(findBidBasicInfo) + 면허/제한경쟁입찰자격/지역/주력분야 목록
     c. 받은 파일을 읽어 본문 텍스트·수식 스크립트를 bid_document_content 에 저장
  3. SRM 차단 징후가 보이면 그 주기를 멈추고, 남은 공고는 다음 실행에서 이어서 처리

실행 (저장소 루트):
  python -m pip install -r backend/collector/requirements.txt
  python -m backend.collector.collect --days 7 --limit 5     # 진행 중 적격심사 공사용역 신규 5건
  python -m backend.collector.collect --dry-run              # 처리 대상만 확인 (SRM 요청 없음)
  python -m backend.collector.collect --notice E012607925 E012607914
"""
import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from backend.collector import clients
from backend.collector.doc_reader import detect_ext, read_document
from backend.db.init_db import DEFAULT_DB_PATH, init_db

ROOT = Path(__file__).resolve().parents[2]
OPEN_STATES = ("PreAttendProgress", "AttendProgress")  # 공고진행·입찰진행 (마감·유찰·개찰·종료 제외)
CATEGORIES = ("LICENSE", "QUALIFIED", "REGION", "LICENSE_MAIN")
# SRM 응답에 섞여 오는 담당자 개인정보는 저장하지 않는다
PERSONAL_KEY = re.compile(r"creator|updater|representative|phone|email|fax|contact|userId|userName", re.I)
EVAL_KEYS = ("license_auto_eval_method", "license_auto_eval_type", "qualified_auto_eval_method",
             "qualified_auto_eval_type", "region_limit_auto_eval_method", "region_limit_auto_eval_type",
             "license_main_auto_eval_type")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def camel(snake):
    head, *rest = snake.split("_")
    return head + "".join(w.title() for w in rest)


def sanitize(value):
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if not PERSONAL_KEY.search(k)}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


def doc_kind(filename):
    name = re.sub(r"\s+", "", filename or "")
    if "적격심사" in name:
        return "CRITERIA"
    if "공고문" in name:
        return "NOTICE"
    return None


def to_int(v):
    try:
        return int(v) if v not in (None, "", "-") else None
    except (TypeError, ValueError):
        return None


def connect(db_path):
    """공통 초기화 진입점(backend.db.init_db)으로 스키마를 적용한 연결."""
    return init_db(db_path)


def in_scope(row):
    """서비스 대상: 한전(COM01) 공사용역(ConstructionService) 중 낙찰방법이 적격심사(QualifiedEval)인 공고."""
    return (row.get("companyId", "COM01") == "COM01"
            and row.get("purchaseType") == "ConstructionService"
            and row.get("bidType") == "QualifiedEval")


def latest_per_notice(rows):
    """Open API가 같은 공고번호를 정정 차수별로 여러 번 주는 경우(예: E012607113) 최신 등록 행만 남긴다."""
    latest = {}
    for row in rows:
        prev = latest.get(row["no"])
        if prev is None or (row.get("createDatetime") or "") >= (prev.get("createDatetime") or ""):
            latest[row["no"]] = row
    return list(latest.values())


def is_open(row, now_text=None):
    """진행 중 공고. progressState 는 마감 후에도 'AttendProgress'로 남는 경우가 많아
    입찰참가신청 마감 일시('YYYY-MM-DD HH:MM:SS', KST)도 함께 확인한다."""
    now_text = now_text or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    close = row.get("bidAttendReqCloseDatetime")
    return row.get("progressState") in OPEN_STATES and (not close or close >= now_text)


# ---------------------------------------------------------------- 저장
def upsert_notice(db, row):
    t = now()
    db.execute("""
        INSERT INTO bid_notice (notice_no, company_id_src, title, purchase_type, item_type, bid_type,
            competition_type, place_name, notice_date, attend_req_begin_at, attend_req_close_at, bid_begin_at,
            bid_end_at, progress_state, presumed_price, estimated_price_basic_amount, approximate_amount,
            presumed_amount, api_raw, first_seen_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(notice_no) DO UPDATE SET title=excluded.title, progress_state=excluded.progress_state,
            attend_req_begin_at=excluded.attend_req_begin_at, attend_req_close_at=excluded.attend_req_close_at,
            bid_begin_at=excluded.bid_begin_at, bid_end_at=excluded.bid_end_at,
            presumed_price=excluded.presumed_price, estimated_price_basic_amount=excluded.estimated_price_basic_amount,
            approximate_amount=excluded.approximate_amount, presumed_amount=excluded.presumed_amount,
            api_raw=excluded.api_raw, updated_at=excluded.updated_at
    """, (row["no"], row.get("companyId") or "COM01", row.get("name") or "", row.get("purchaseType"),
          row.get("itemType"), row.get("bidType"), row.get("competitionType"), row.get("placeName"),
          row.get("noticeDate"), row.get("bidAttendReqBeginDatetime"), row.get("bidAttendReqCloseDatetime"),
          row.get("beginDatetime"), row.get("endDatetime"), row.get("progressState"),
          to_int(row.get("presumedPrice")), to_int(row.get("estimatedPriceBasicAmount")),
          to_int(row.get("approximateAmount")), to_int(row.get("presumedAmount")),
          json.dumps(row, ensure_ascii=False), t, t))


def is_done(db, notice_no):
    snap = db.execute("SELECT fetch_status FROM bid_restriction_snapshot WHERE notice_no=?", (notice_no,)).fetchone()
    docs = db.execute("SELECT notice_doc_found, criteria_doc_found FROM bid_notice WHERE notice_no=?",
                      (notice_no,)).fetchone()
    return bool(snap and snap[0] == "SUCCESS" and docs and None not in docs)


def save_attachment(db, notice_no, kind, filename, source, data=None, error=None):
    folder = ROOT / "downloads" / notice_no
    path, sha, size, ext = None, None, None, None
    if data is not None:
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[\\/:*?"<>|]', "_", filename)
        (folder / safe).write_bytes(data)
        path = str((folder / safe).relative_to(ROOT))
        sha, size, ext = hashlib.sha256(data).hexdigest(), len(data), detect_ext(filename, data)
    db.execute("""
        INSERT INTO bid_attachment (notice_no, doc_kind, filename, file_ext, source, download_status,
            local_path, size_bytes, sha256, error, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(notice_no, filename) DO UPDATE SET source=excluded.source,
            download_status=excluded.download_status, local_path=excluded.local_path,
            size_bytes=excluded.size_bytes, sha256=excluded.sha256, file_ext=excluded.file_ext,
            error=excluded.error, fetched_at=excluded.fetched_at
    """, (notice_no, kind, filename, ext, source, "SUCCESS" if data is not None else "FAILED",
          path, size, sha, error, now()))
    attachment_id = db.execute("SELECT id FROM bid_attachment WHERE notice_no=? AND filename=?",
                               (notice_no, filename)).fetchone()[0]
    if data is not None:
        save_content(db, attachment_id, filename, data)
    return data is not None


def save_content(db, attachment_id, filename, data):
    try:
        paragraphs, equations = read_document(filename, data)
        values = ("SUCCESS", "\n".join(paragraphs), json.dumps(equations, ensure_ascii=False),
                  len(paragraphs), len(equations), None)
    except Exception as exc:  # 손상·암호화·미지원 형식: 실패로 기록하고 계속
        values = ("FAILED", None, None, None, None, f"{type(exc).__name__}: {exc}"[:500])
    db.execute("""
        INSERT INTO bid_document_content (attachment_id, read_status, text, equations, paragraph_count,
            equation_count, error, read_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(attachment_id) DO UPDATE SET read_status=excluded.read_status, text=excluded.text,
            equations=excluded.equations, paragraph_count=excluded.paragraph_count,
            equation_count=excluded.equation_count, error=excluded.error, read_at=excluded.read_at
    """, (attachment_id, *values, now()))


# ---------------------------------------------------------------- 수집 단계
def collect_api_documents(db, row):
    """Open API filenlink 1~5 중 공고문·적격심사기준을 받는다. → 확보한 문서 종류 집합."""
    found = set()
    for i in range(1, 6):
        name, link = row.get(f"filename{i}"), row.get(f"filenlink{i}")
        kind = doc_kind(name)
        if not (kind and link):
            continue
        try:
            ok = save_attachment(db, row["no"], kind, name, "OPEN_API", clients.download_open_api_file(link))
        except Exception as exc:
            ok = save_attachment(db, row["no"], kind, name, "OPEN_API", error=str(exc)[:300])
        if ok:
            found.add(kind)
    return found


def collect_srm(db, srm, notice_no, found_kinds):
    """SRM 투찰제한 + (API에 없던) 공고문·적격심사기준 보완. SrmBlocked 는 호출자에게 전달."""
    t = now()
    row = srm.find_bid(notice_no)
    if row is None:
        db.execute("""INSERT OR REPLACE INTO bid_restriction_snapshot (notice_no, fetch_status, error, fetched_at)
                      VALUES (?, 'FAILED', 'SRM 검색 결과 없음', ?)""", (notice_no, t))
        return found_kinds
    bid_id = row["id"]
    db.execute("UPDATE bid_notice SET bid_id=? WHERE notice_no=?", (bid_id, notice_no))
    basic = srm.basic_info(bid_id)

    # 범주별 목록 (조회 실패와 '제한 없음'을 구분해서 기록)
    items, failed = {}, []
    db.execute("DELETE FROM bid_restriction_item WHERE notice_no=?", (notice_no,))
    for cat in CATEGORIES:
        try:
            items[cat] = srm.restriction(bid_id, cat)
            db.execute("""INSERT OR REPLACE INTO bid_restriction_category VALUES (?,?,'SUCCESS',?,NULL,?)""",
                       (notice_no, cat, len(items[cat]), now()))
        except clients.SrmBlocked:
            raise
        except Exception as exc:
            failed.append(cat)
            db.execute("""INSERT OR REPLACE INTO bid_restriction_category VALUES (?,?,'FAILED',NULL,?,?)""",
                       (notice_no, cat, str(exc)[:300], now()))
            continue
        for it in items[cat]:
            if cat == "REGION":
                values = (it.get("areaCode"), it.get("areaCodeName"), it.get("areaCode"),
                          it.get("subAreaCode"), it.get("subAreaCodeName"))
            elif cat == "QUALIFIED":
                values = (it.get("qualifiedQualificationCode"), it.get("qualifiedQualificationCodeName"),
                          None, None, None)
            elif cat == "LICENSE_MAIN":
                values = (it.get("licenseMainQualificationCode") or it.get("licenseQualificationCode"),
                          it.get("licenseMainQualificationCodeName") or it.get("licenseQualificationCodeName"),
                          None, None, None)
            else:
                values = (it.get("licenseQualificationCode"), it.get("licenseQualificationCodeName"),
                          None, None, None)
            db.execute("""INSERT INTO bid_restriction_item (notice_no, category, code, name, area_code,
                          sub_area_code, sub_area_name) VALUES (?,?,?,?,?,?,?)""", (notice_no, cat, *values))

    # A금액·순공사원가·개찰일시 (SRM 상세에만 있는 값, 없으면 NULL 유지)
    db.execute("""UPDATE bid_notice SET a_amount=COALESCE(?, a_amount),
                  net_construction_cost=COALESCE(?, net_construction_cost),
                  open_bid_at=COALESCE(?, open_bid_at) WHERE notice_no=?""",
               (to_int(basic.get("totalAAmt")), to_int(basic.get("overHeadCostAmount")),
                basic.get("openBidDateTime"), notice_no))
    evals = {k: basic.get(camel(k)) for k in EVAL_KEYS}
    raw = {"search": sanitize({k: row.get(k) for k in ("announceNo", "areaCodeName", "limitedReasonCode")}),
           "basic": {camel(k): v for k, v in evals.items()}, "items": sanitize(items)}
    db.execute(f"""INSERT OR REPLACE INTO bid_restriction_snapshot (notice_no, fetch_status, error,
                   {", ".join(EVAL_KEYS)}, limited_reason_code, raw_response, fetched_at)
                   VALUES (?,?,?,{",".join("?" * len(EVAL_KEYS))},?,?,?)""",
               (notice_no, "PARTIAL" if failed else "SUCCESS", f"조회 실패: {failed}" if failed else None,
                *evals.values(), basic.get("limitedReasonCode"), json.dumps(raw, ensure_ascii=False), t))

    # API에 없던 문서 종류만 SRM 전체 첨부에서 보완
    missing = {"NOTICE", "CRITERIA"} - found_kinds
    if missing and basic.get("attachFileGroupId"):
        for att in srm.attachments(basic["attachFileGroupId"]):
            kind = doc_kind(att.get("name"))
            if kind not in missing:
                continue
            try:
                data = srm.download(att["id"])
                ok = save_attachment(db, notice_no, kind, att["name"], "SRM_FALLBACK", data)
            except clients.SrmBlocked:
                raise
            except Exception as exc:
                ok = save_attachment(db, notice_no, kind, att["name"], "SRM_FALLBACK", error=str(exc)[:300])
            if ok:
                found_kinds.add(kind)
    return found_kinds


def process_notice(db, srm, row):
    notice_no = row["no"]
    found = collect_api_documents(db, row)
    db.commit()
    try:
        found = collect_srm(db, srm, notice_no, found)
    finally:
        db.execute("UPDATE bid_notice SET notice_doc_found=?, criteria_doc_found=? WHERE notice_no=?",
                   (int("NOTICE" in found), int("CRITERIA" in found), notice_no))
        db.commit()
    return found


def main():
    ap = argparse.ArgumentParser(description="SRM 공고 수집 (API 우선 + SRM 보완, 공고당 1회)")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--days", type=int, default=7, help="Open API 조회 기간(일, 최대 90)")
    ap.add_argument("--limit", type=int, default=5, help="이번 실행에서 처리할 신규 공고 수")
    ap.add_argument("--include-closed", action="store_true", help="마감·개찰·종료 공고도 처리")
    ap.add_argument("--notice", nargs="*", help="특정 공고번호만 처리 (서비스 범위 밖이어도 처리)")
    ap.add_argument("--interval", type=float, default=3.0, help="SRM 요청 간격(초)")
    ap.add_argument("--refresh", action="store_true", help="이미 수집한 공고도 다시 처리")
    ap.add_argument("--dry-run", action="store_true", help="bid_notice 저장 후 처리 대상만 출력 (SRM 요청 없음)")
    args = ap.parse_args()

    db = connect(args.db)
    run_id = db.execute("INSERT INTO collect_run (started_at) VALUES (?)", (now(),)).lastrowid
    rows = latest_per_notice(clients.fetch_open_api_notices(clients.load_api_key(ROOT), days=args.days))
    wanted = set(args.notice or [])
    for row in rows:  # 서비스 범위 밖 공고는 저장하지 않음 (직접 지정한 공고는 예외)
        if row["no"] in wanted or in_scope(row):
            upsert_notice(db, row)
    db.commit()

    if args.notice:
        targets = [r for r in rows if r["no"] in wanted]
        missing = wanted - {r["no"] for r in targets}
        if missing:
            print(f"Open API 조회 기간에 없는 공고: {sorted(missing)} (--days 를 늘려 보세요)")
    else:
        targets = [r for r in rows if in_scope(r) and (args.include_closed or is_open(r))]
        targets.sort(key=lambda r: r.get("createDatetime") or "", reverse=True)
    if not args.refresh:
        targets = [r for r in targets if not is_done(db, r["no"])]
    targets = targets[:args.limit] if not args.notice else targets
    print(f"Open API 공고 {len(rows)}건 → 서비스 범위 {sum(map(in_scope, rows))}건 → 이번 처리 대상 {len(targets)}건")
    if args.dry_run:
        for r in targets:
            print(f"  {r['no']} {r.get('bidType')} 신청마감={r.get('bidAttendReqCloseDatetime')} {r.get('name', '')[:30]}")
        db.execute("UPDATE collect_run SET finished_at=?, api_notice_count=?, target_count=?, processed_count=0, "
                   "stopped_reason='dry-run' WHERE id=?", (now(), len(rows), len(targets), run_id))
        db.commit()
        db.close()
        return

    processed, reason = 0, "완료"
    try:
        srm = clients.SrmClient(interval=args.interval) if targets else None
        for row in targets:
            found = process_notice(db, srm, row)
            processed += 1
            print(f"  {row['no']} 공고문={'O' if 'NOTICE' in found else 'X'} "
                  f"적격심사기준={'O' if 'CRITERIA' in found else 'X'}")
    except clients.SrmBlocked as exc:
        reason = f"SRM 차단 의심으로 중단: {exc}"
        print(reason)
    finally:
        db.execute("""UPDATE collect_run SET finished_at=?, api_notice_count=?, target_count=?,
                      processed_count=?, stopped_reason=? WHERE id=?""",
                   (now(), len(rows), len(targets), processed, reason, run_id))
        db.commit()
        db.close()


if __name__ == "__main__":
    main()
