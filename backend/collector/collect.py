"""SRM 공고 수집 실행.

모드:
  full  — 최초 전체 적재. 공식 API 최근 180일(90일 창 2개) 서비스 대상 공고 기본정보 저장 +
          진행 중 공고 전체 상세 수집 (건수 제한 없음, 중단되면 다음 실행에서 남은 공고부터)
  daily — 매일 증분. 마지막으로 목록을 정상 반영한 조회 종료일 - 7일 ~ 오늘 공고일 범위를 조회해
          신규·변경 공고 기본정보 갱신 + 신규·정정·첨부변경·미완료 공고만 상세 수집 (완료 공고는 재사용)

흐름 (기능명세서 FUNC-005~008, 11-18 회의 결정):
  1. Open API로 공고 목록 조회 → bid_notice 저장/갱신 (신규/기본정보 갱신/정정 구분)
  2. 아직 수집이 끝나지 않은 공고만 처리
     a. 공고문·적격심사기준: Open API 첨부(filenlink)에서 먼저 받고, 없으면 SRM 전체 첨부에서 보완
     b. SRM 투찰제한: 자동심사 설정(findBidBasicInfo) + 면허/제한경쟁입찰자격/지역/주력분야 목록
     c. 받은 파일을 읽어 본문 텍스트·수식 스크립트를 bid_document_content 에 저장
  3. SRM 차단 징후가 보이면 그 주기를 멈추고, 남은 공고는 다음 실행에서 이어서 처리

실행 (저장소 루트):
  python -m pip install -r backend/collector/requirements.txt
  python -m backend.collector.collect --mode full           # 최초 전체 적재 (중단 후 다시 실행하면 이어서)
  python -m backend.collector.collect                        # 매일 증분 (--mode daily)
  python -m backend.collector.collect --dry-run              # 처리 대상만 확인 (SRM 요청 없음)
  python -m backend.collector.collect --notice E012607925 E012607914
"""
import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from backend.collector import clients
from backend.collector.doc_reader import PARSER_VERSION, document_problem, detect_ext, read_document
from backend.db.extract_evaluation_rules import refresh_notice as refresh_evaluation_rules, top_tables
from backend.schedule import refresh as refresh_schedule
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


# '공고 문의 담당자 안내'는 공백을 지우면 '공고문의…'가 되어 공고문으로 잘못 분류됐다 (E012607149 등)
NOT_NOTICE = ("안내", "담당자")
# 이름에 '적격심사'가 없지만 적격심사기준이 들어 있을 수 있는 파일 (PQ심사기준, 입찰참가자격사전심사기준 등)
NOT_CRITERIA = ("인원장비",)  # 인원·장비 심사기준은 적격심사기준이 아님


def is_criteria_candidate(filename):
    name = re.sub(r"\s+", "", filename or "")
    return (doc_kind(filename) is None and "심사기준" in name and not any(w in name for w in NOT_CRITERIA)
            and name.lower().rsplit(".", 1)[-1] in ("hwp", "hwpx", "pdf"))


def has_criteria_text(filename, data):
    """'적격심사기준'(공백 무시)이 제목처럼 짧은 문단(40자 이하)으로 나오는 문서인지.
    본문 중간에 '적격심사기준에 따라' 정도로 언급만 하는 문서는 제외한다. 읽을 수 없으면 False."""
    if document_problem(filename, data):
        return False
    try:
        paragraphs, _ = read_document(filename, data)
    except Exception:
        return False
    heading = re.compile(r"적격심사.{0,6}기준")  # 적격심사기준, 적격심사 세부기준, 적격심사 및 세부평가기준
    return any(heading.search(p) for p in (re.sub(r"\s+", "", x) for x in paragraphs) if len(p) <= 40)


def criteria_has_score_table(db, notice_no):
    """받아 둔 적격심사기준 중 배점표(입찰가격 행이 있는 표)가 있는 문서가 있는지"""
    texts = [r[0] for r in db.execute("""
        SELECT c.text FROM bid_attachment a JOIN bid_document_content c ON c.attachment_id = a.id
        WHERE a.notice_no=? AND a.doc_kind='CRITERIA' AND c.read_status='SUCCESS'""", (notice_no,))]
    return any(top_tables(t.split("\n"), None, False) for t in texts if t)


def doc_kind(filename):
    name = re.sub(r"\s+", "", filename or "")
    if "적격심사" in name:
        return "CRITERIA"
    if "공고문" in name and not any(word in name for word in NOT_NOTICE):
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
ATTACHMENT_KEYS = tuple(f"{k}{i}" for i in range(1, 6) for k in ("filename", "filenlink"))
SAME, NEW, UPDATED, CORRECTED = "same", "new", "updated", "corrected"


def upsert_notice(db, row):
    """공식 API 행 저장/갱신 → 변경 종류.
    - NEW: 처음 본 공고번호
    - CORRECTED: createDatetime 이 저장값보다 늦음(정정 공고) 또는 첨부 목록이 바뀜 → 상세 재수집 표시
    - UPDATED: 등록일시·첨부는 같고 다른 기본정보(일정·금액·상태 등)만 바뀜 → 기본정보만 갱신, 상세 재사용
    - SAME: 저장된 원본과 같음 (또는 저장값보다 이전 차수 행 → 무시)
    """
    t = now()
    created = row.get("createDatetime")
    prev = db.execute("SELECT api_created_at, api_raw FROM bid_notice WHERE notice_no=?", (row["no"],)).fetchone()
    if prev is None:
        change = NEW
    else:
        old = json.loads(prev[1] or "{}")
        if prev[0] and created and created < prev[0]:
            return SAME  # 이전 차수 행: 기준값을 되돌리지 않음
        if old == row:
            return SAME
        newer = bool(prev[0] and created and created > prev[0])
        attachments_changed = any(old.get(k) != row.get(k) for k in ATTACHMENT_KEYS)
        change = CORRECTED if newer or attachments_changed else UPDATED
    db.execute("""
        INSERT INTO bid_notice (notice_no, company_id_src, title, purchase_type, item_type, bid_type,
            competition_type, place_name, notice_date, attend_req_begin_at, attend_req_close_at, bid_begin_at,
            bid_end_at, progress_state, presumed_price, estimated_price_basic_amount, approximate_amount,
            presumed_amount, api_raw, first_seen_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(notice_no) DO UPDATE SET title=excluded.title, progress_state=excluded.progress_state,
            place_name=excluded.place_name, notice_date=excluded.notice_date,
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
    # 기준값은 더 늦은 값으로만 갱신 (이전 차수 행이 늦게 와도 되돌아가지 않게)
    db.execute("UPDATE bid_notice SET api_created_at=? WHERE notice_no=? "
               "AND (api_created_at IS NULL OR api_created_at < ?)", (created, row["no"], created))
    if change == CORRECTED:
        reason = (f"Open API 등록일시 변경 {prev[0]} → {created}" if created and prev[0] and created > prev[0]
                  else "Open API 첨부 목록 변경")
        db.execute("UPDATE bid_notice SET needs_recollect=1, recollect_reason=? WHERE notice_no=?",
                   (reason, row["no"]))
    return change


def is_done(db, notice_no):
    """수집 완료 = 투찰제한 SUCCESS + 문서 확보 여부 확인 + 정정 재수집 표시 없음."""
    snap = db.execute("SELECT fetch_status FROM bid_restriction_snapshot WHERE notice_no=?", (notice_no,)).fetchone()
    row = db.execute("SELECT notice_doc_found, criteria_doc_found, needs_recollect FROM bid_notice "
                     "WHERE notice_no=?", (notice_no,)).fetchone()
    return bool(snap and snap[0] == "SUCCESS" and row and None not in row[:2] and not row[2])


def attachment_version(db, notice_no):
    """첨부 내용 버전: 성공한 첨부의 sha256 을 정렬해 이은 값 (notice_analysis_cache.document_version 과 같은 정의)."""
    shas = [r[0] for r in db.execute("SELECT sha256 FROM bid_attachment WHERE notice_no=? AND sha256 IS NOT NULL "
                                     "ORDER BY sha256", (notice_no,))]
    return ",".join(shas)


def save_attachment(db, notice_no, kind, filename, source, data=None, error=None):
    """첨부 1건 기록. 받은 내용이 문서가 아니면(HTML 오류 페이지 등) 파일을 남기지 않고 FAILED 로 기록한다.
    → 문서를 확보했으면 True (False 면 호출자가 SRM 보완을 시도)."""
    folder = ROOT / "downloads" / notice_no
    path, sha, size, ext = None, None, None, None
    if data is not None:
        problem = document_problem(filename, data)
        if problem:
            data, error = None, problem
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
            equation_count, error, read_at, parser_version)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(attachment_id) DO UPDATE SET read_status=excluded.read_status, text=excluded.text,
            equations=excluded.equations, paragraph_count=excluded.paragraph_count,
            equation_count=excluded.equation_count, error=excluded.error, read_at=excluded.read_at,
            parser_version=excluded.parser_version
    """, (attachment_id, *values, now(), PARSER_VERSION))


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


def parse_restriction_item(category, item):
    """SRM 투찰제한 응답 1행 → (code, name, area_code, sub_area_code, sub_area_name)."""
    if category == "REGION":
        return (item.get("areaCode"), item.get("areaCodeName"), item.get("areaCode"),
                item.get("subAreaCode"), item.get("subAreaCodeName"))
    if category == "QUALIFIED":
        return (item.get("qualifiedQualificationCode"), item.get("qualifiedQualificationCodeName"), None, None, None)
    if category == "LICENSE_MAIN":
        # 실제 응답 키는 licenseMainCode/licenseMainCodeName (E012608201 등). 예전 키는 혹시 몰라 대체로 둔다
        return (item.get("licenseMainCode") or item.get("licenseMainQualificationCode"),
                item.get("licenseMainCodeName") or item.get("licenseMainQualificationCodeName"),
                None, None, None)
    return (item.get("licenseQualificationCode"), item.get("licenseQualificationCodeName"), None, None, None)


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
            db.execute("""INSERT INTO bid_restriction_item (notice_no, category, code, name, area_code,
                          sub_area_code, sub_area_name) VALUES (?,?,?,?,?,?,?)""",
                       (notice_no, cat, *parse_restriction_item(cat, it)))

    # A금액·경비·개찰일시·정정 차수 (SRM 상세에만 있는 값, 없으면 기존 값 유지)
    # overHeadCostAmount 는 공고문 금액표의 '경비'다(순공사원가 아님). 순공사원가는 SRM에 없어
    # net_construction_cost 는 쓰지 않는다(NULL=미확인).
    revision = basic.get("bidRevision", basic.get("revision"))
    db.execute("""UPDATE bid_notice SET a_amount=COALESCE(?, a_amount),
                  overhead_cost_amount=COALESCE(?, overhead_cost_amount),
                  open_bid_at=COALESCE(?, open_bid_at), srm_revision=COALESCE(?, srm_revision)
                  WHERE notice_no=?""",
               (to_int(basic.get("totalAAmt")), to_int(basic.get("overHeadCostAmount")),
                basic.get("openBidDateTime"), None if revision is None else str(revision), notice_no))
    evals = {k: basic.get(camel(k)) for k in EVAL_KEYS}
    raw = {"search": sanitize({k: row.get(k) for k in ("announceNo", "areaCodeName", "limitedReasonCode")}),
           "basic": {camel(k): v for k, v in evals.items()}, "items": sanitize(items)}
    db.execute(f"""INSERT OR REPLACE INTO bid_restriction_snapshot (notice_no, fetch_status, error,
                   {", ".join(EVAL_KEYS)}, limited_reason_code, raw_response, fetched_at)
                   VALUES (?,?,?,{",".join("?" * len(EVAL_KEYS))},?,?,?)""",
               (notice_no, "PARTIAL" if failed else "SUCCESS", f"조회 실패: {failed}" if failed else None,
                *evals.values(), basic.get("limitedReasonCode"), json.dumps(raw, ensure_ascii=False), t))

    # API에 없던 문서 종류만 SRM 전체 첨부에서 보완.
    # 기준서를 받았어도 배점표(입찰가격 행)가 없으면 진짜 기준서를 놓친 것으로 보고 더 찾는다
    # (E012606999: API 5개 중 'PQ 세부기준'만 잡히고 배점표가 든 '적격심사 세부기준'은 6번째 이후 첨부였음)
    missing = {"NOTICE", "CRITERIA"} - found_kinds
    weak = "CRITERIA" in found_kinds and not criteria_has_score_table(db, notice_no)
    if weak:
        missing.add("CRITERIA")
    saved = {r[0] for r in db.execute("SELECT filename FROM bid_attachment WHERE notice_no=? "
                                      "AND download_status='SUCCESS'", (notice_no,))}
    if missing and basic.get("attachFileGroupId"):
        attachments = srm.attachments(basic["attachFileGroupId"])
        for att in attachments:
            kind = doc_kind(att.get("name"))
            if kind not in missing or (weak and att.get("name") in saved):
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
        # 이름에 '적격심사'가 없는 기준서 (예: E012606754 'pq심사기준' 안의 '5.2.1 적격심사기준'):
        # '심사기준' 후보를 받아 본문에 적격심사기준이 있을 때만 기준서로 저장
        if "CRITERIA" not in found_kinds or (weak and not criteria_has_score_table(db, notice_no)):
            for att in (a for a in attachments if is_criteria_candidate(a.get("name")) and a.get("name") not in saved):
                try:
                    data = srm.download(att["id"])
                except clients.SrmBlocked:
                    raise
                except Exception:
                    continue
                if has_criteria_text(att["name"], data) and save_attachment(
                        db, notice_no, "CRITERIA", att["name"], "SRM_FALLBACK", data):
                    found_kinds.add("CRITERIA")
                    break
    return found_kinds


def invalidate_edited_rules(db, notice_no):
    """정정으로 첨부(기준서)가 바뀐 공고: LLM·사용자가 옛 기준서로 넣은 배점 행을 지운다.
    지우지 않으면 파서가 이 공고를 '고친 공고'로 보고 건드리지 않아 옛 배점이 계속 쓰인다.
    바로 뒤 refresh_evaluation_rules 가 새 기준서로 다시 뽑고, 못 뽑으면 rule_extracted=0 → 다시 LLM 대상. → 지운 행 수"""
    return db.execute("DELETE FROM evaluation_rule WHERE notice_no=? AND updated_by <> 'PARSER'", (notice_no,)).rowcount


def process_notice(db, srm, row):
    notice_no = row["no"]
    started = now()
    old_version = attachment_version(db, notice_no)
    found = collect_api_documents(db, row)
    db.commit()
    finished = False
    try:
        found = collect_srm(db, srm, notice_no, found)
        finished = True
    finally:
        # SRM 보완 도중 차단·오류로 끝나면 '없음(0)'이 아니라 '미확인(NULL)'으로 남겨 다음 실행에서 다시 찾게 한다
        # (예전엔 0으로 저장돼 완료로 간주 → E012608004 공고문·기준서를 다시 찾지 않았음)
        flag = (lambda kind: int(kind in found)) if finished else (lambda kind: 1 if kind in found else None)
        db.execute("UPDATE bid_notice SET notice_doc_found=?, criteria_doc_found=? WHERE notice_no=?",
                   (flag("NOTICE"), flag("CRITERIA"), notice_no))
        db.commit()
    snap = db.execute("SELECT fetch_status FROM bid_restriction_snapshot WHERE notice_no=?", (notice_no,)).fetchone()
    if snap and snap[0] == "SUCCESS":
        # 이번 수집에서 다시 받지 않은 첨부 = 정정으로 빠진 파일 → 삭제 (원문은 CASCADE, AI 추출은 SET NULL)
        db.execute("DELETE FROM bid_attachment WHERE notice_no=? AND fetched_at < ?", (notice_no, started))
        # 첨부 내용이 바뀌었으면 AI 분석 캐시 무효화 → AI 파트가 재분석 대상으로 인식
        if old_version and attachment_version(db, notice_no) != old_version:
            db.execute("UPDATE notice_analysis_cache SET analysis_status='INVALIDATED', invalidated_at=? "
                       "WHERE notice_no=? AND analysis_status <> 'INVALIDATED'", (now(), notice_no))
            invalidate_edited_rules(db, notice_no)
        db.execute("UPDATE bid_notice SET needs_recollect=0, recollect_reason=NULL, last_collected_at=? "
                   "WHERE notice_no=?", (now(), notice_no))
        db.commit()
        # 적격심사 배점(evaluation_rule)·일정(notice_schedule_event) 갱신. 실패해도 수집 결과는 그대로 두고 다음 공고로
        try:
            refresh_schedule(db, notice_no)
            n, issues, detail = refresh_evaluation_rules(db, notice_no)
            db.commit()
            note = " / ".join(issues[:2] + detail[:2])
            print(f"    배점 {n}행" + (f" (확인 필요: {note})" if note else ""))
        except Exception as exc:
            db.rollback()
            print(f"    배점·일정 갱신 실패: {type(exc).__name__}: {exc}"[:300])
    return found


# ---------------------------------------------------------------- 실행 모드
FULL_DAYS = 180       # 공고일→신청마감이 최대 104일(최근 180일 실측)이라 진행 중 공고를 모두 덮도록 90일 창 2개
OVERLAP_DAYS = 7      # 일일 수집 겹침: 공식 API 반영 지연(당일 공고는 다음 날 조회됨)·늦은 등록 대비
MODE_DEFAULTS = {     # SRM 요청 간격·쉬는 주기·차단 시 재시도 (최초 적재는 요청이 많아 더 천천히)
    # 실측(2026-11-20): 5초 간격이어도 공고 16건(요청 약 160번)쯤마다 연결 끊김 → 10분 쉬면 풀림. 최초 적재는 여러 번 허용
    "full": {"interval": 5.0, "pause_every": 5, "pause_seconds": 60, "max_blocks": 20, "cooldown": 600},
    "daily": {"interval": 3.0, "pause_every": 10, "pause_seconds": 30, "max_blocks": 1, "cooldown": 600},
}


def last_synced_end(db):
    """목록을 마지막으로 정상 반영한 실행의 조회 종료일(YYYYMMDD). 없으면 None."""
    row = db.execute("SELECT window_end FROM collect_run WHERE list_synced=1 AND window_end IS NOT NULL "
                     "ORDER BY window_end DESC, id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def list_window(db, args, today):
    """→ (시작일, 종료일, 설명). 공식 API는 공고일(noticeDate) 범위로만 조회된다(수정시각 조회 없음)."""
    if args.days:
        return today - timedelta(days=args.days), today, f"--days {args.days}"
    if args.mode == "full":
        return today - timedelta(days=args.full_days), today, f"최초 적재 {args.full_days}일"
    last = last_synced_end(db)
    if last is None:
        return today - timedelta(days=args.full_days), today, "이전 목록 반영 기록 없음 → 최초 적재 범위"
    begin = datetime.strptime(last, "%Y%m%d").date() - timedelta(days=args.overlap_days)
    return begin, today, f"마지막 반영 {last} 이후 (겹침 {args.overlap_days}일)"


def fetch_list(api_key, begin, end):
    """90일 창으로 나눠 조회 → (행 목록, 실패한 창 목록)."""
    rows, failed = [], []
    for b, e in clients.date_windows(begin, end):
        try:
            rows += clients.fetch_open_api_window(api_key, b, e)
        except Exception as exc:
            failed.append(f"{b}~{e}: {type(exc).__name__}: {str(exc)[:120]}")
    return rows, failed


def detail_targets(db, args, new_nos):
    """상세 수집 대상 (DB 기준): 신규·정정·첨부변경(needs_recollect)·이전에 못 끝낸 공고. 완료 공고는 재사용."""
    if args.notice:
        rows = db.execute(f"SELECT notice_no, api_raw FROM bid_notice WHERE notice_no IN "
                          f"({','.join('?' * len(args.notice))})", args.notice).fetchall()
    else:
        where = "company_id_src='COM01' AND purchase_type='ConstructionService' AND bid_type='QualifiedEval'"
        if not args.include_closed:
            where += " AND is_open=1 AND (attend_req_close_at IS NULL OR attend_req_close_at >= ?)"
        params = () if args.include_closed else (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)
        rows = db.execute(f"SELECT notice_no, api_raw FROM bid_notice WHERE {where} "
                          "ORDER BY needs_recollect DESC, attend_req_close_at, notice_no", params).fetchall()
    targets = []
    for notice_no, raw in rows:
        if args.refresh or args.notice or not is_done(db, notice_no):
            row = json.loads(raw)
            row["no"] = notice_no
            targets.append(row)
    # 정정·첨부변경 → 신규 → 마감 임박 순
    targets.sort(key=lambda r: (not is_flagged(db, r["no"]), r["no"] not in new_nos))
    return targets


def is_flagged(db, notice_no):
    return bool(db.execute("SELECT needs_recollect FROM bid_notice WHERE notice_no=?", (notice_no,)).fetchone()[0])


def check_srm_revisions(db, srm, count):
    """SRM 상세만 바뀐 공고 감지 (선택): 완료된 진행 중 공고의 SRM 정정 차수를 다시 조회해 저장값과 비교.
    공식 API 목록에는 SRM 차수가 없어 공고마다 1회 요청이 든다. → 바뀐 공고번호 목록."""
    rows = db.execute("""SELECT notice_no, bid_id, srm_revision FROM bid_notice
                         WHERE bid_id IS NOT NULL AND needs_recollect=0 AND is_open=1
                           AND (attend_req_close_at IS NULL OR attend_req_close_at >= ?)
                         ORDER BY last_collected_at LIMIT ?""",
                      (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), count)).fetchall()
    changed = []
    for notice_no, bid_id, stored in rows:
        if not is_done(db, notice_no):
            continue
        basic = srm.basic_info(bid_id)
        revision = basic.get("bidRevision", basic.get("revision"))
        if revision is not None and stored is not None and str(revision) != str(stored):
            db.execute("UPDATE bid_notice SET needs_recollect=1, recollect_reason=? WHERE notice_no=?",
                       (f"SRM 정정 차수 변경 {stored} → {revision}", notice_no))
            changed.append(notice_no)
    db.commit()
    return changed


def main():
    ap = argparse.ArgumentParser(description="한전 공고 수집: 최초 전체 적재(full) / 매일 증분 수집(daily)")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--mode", choices=("daily", "full"), default="daily",
                    help="full=최근 --full-days 일 목록 + 진행 중 공고 전체 상세 / daily=마지막 반영 이후 변경분")
    ap.add_argument("--full-days", type=int, default=FULL_DAYS, help="최초 적재 목록 조회 기간(일, 90일 창으로 나눠 조회)")
    ap.add_argument("--overlap-days", type=int, default=OVERLAP_DAYS, help="일일 수집 시 마지막 반영일과 겹쳐 볼 일수")
    ap.add_argument("--days", type=int, help="목록 조회 기간을 직접 지정(최근 N일). 지정하면 mode 의 기간 규칙 대신 사용")
    ap.add_argument("--limit", type=int, help="상세 수집 최대 건수 (기본: 제한 없음, 남으면 다음 실행에서 이어서)")
    ap.add_argument("--include-closed", action="store_true", help="마감된 공고도 상세 수집")
    ap.add_argument("--notice", nargs="*", help="특정 공고번호만 상세 수집 (DB에 기본정보가 있어야 함)")
    ap.add_argument("--interval", type=float, help="SRM 요청 간격(초). 기본 full 5초, daily 3초")
    ap.add_argument("--srm-revision-check", type=int, default=0, metavar="N",
                    help="완료된 진행 중 공고 N건의 SRM 정정 차수를 다시 확인 (SRM만 바뀐 경우 감지, 기본 0)")
    ap.add_argument("--refresh", action="store_true", help="이미 수집한 공고도 다시 처리")
    ap.add_argument("--dry-run", action="store_true", help="목록 저장 후 상세 수집 대상만 출력 (SRM 요청 없음)")
    args = ap.parse_args()
    conf = MODE_DEFAULTS[args.mode]
    interval = args.interval or conf["interval"]

    db = connect(args.db)
    started = now()
    run_id = db.execute("INSERT INTO collect_run (started_at, mode) VALUES (?, ?)", (started, args.mode)).lastrowid
    db.commit()
    today = date.today()
    begin, end, why = list_window(db, args, today)
    rows, list_errors = fetch_list(clients.load_api_key(ROOT), begin, end)
    rows = latest_per_notice(rows)
    list_synced = int(not list_errors)
    wanted = set(args.notice or [])
    changes = {NEW: [], UPDATED: [], CORRECTED: []}
    for row in rows:  # 서비스 범위 밖 공고는 저장하지 않음 (직접 지정한 공고는 예외)
        if not (row["no"] in wanted or in_scope(row)):
            continue
        # [알림 연결 지점] NEW = 공식 API에서 처음 본 공고 (실행 후에는 first_seen_at >= collect_run.started_at)
        change = upsert_notice(db, row)
        if change in changes:
            changes[change].append(row["no"])
    db.execute("UPDATE collect_run SET window_begin=?, window_end=?, list_synced=?, api_notice_count=?, "
               "new_notice_count=?, updated_count=?, corrected_count=? WHERE id=?",
               (begin.strftime("%Y%m%d"), end.strftime("%Y%m%d"), list_synced, len(rows), len(changes[NEW]),
                len(changes[UPDATED]), len(changes[CORRECTED]), run_id))
    db.commit()
    print(f"[{args.mode}] 목록 {begin}~{end} ({why}): 공식 API {len(rows)}건 → 서비스 범위 "
          f"{sum(map(in_scope, rows))}건 / 신규 {len(changes[NEW])}, 기본정보 갱신 {len(changes[UPDATED])}, "
          f"정정·첨부변경 {len(changes[CORRECTED])}")
    for err in list_errors:
        print(f"  목록 조회 실패 (마지막 반영 시점을 옮기지 않음): {err}")

    processed, completed, failed, reason, revision_changed = 0, 0, 0, "완료", []
    rule_checked, rule_ok = 0, 0  # 배점 규칙 추출 성공률 (새 문장 표현이 나오면 떨어진다)
    srm, blocks, streak, last_block_at = None, 0, 0, -100
    targets, pending = [], 0
    try:
        if args.srm_revision_check and not args.dry_run:
            srm = clients.SrmClient(interval=interval)
            revision_changed = check_srm_revisions(db, srm, args.srm_revision_check)
            print(f"SRM 정정 차수 확인 {args.srm_revision_check}건 중 변경 {len(revision_changed)}건 {revision_changed}")
        targets = detail_targets(db, args, set(changes[NEW]))
        pending = len(targets)
        if args.limit is not None:
            targets = targets[:args.limit]
        print(f"상세 수집 필요 {pending}건 → 이번 처리 {len(targets)}건"
              + (f" (한도 {args.limit}건)" if args.limit is not None else " (제한 없음)"))
        if args.dry_run:
            for r in targets:
                mark = "[재수집] " if is_flagged(db, r["no"]) else ("[신규] " if r["no"] in changes[NEW] else "")
                print(f"  {mark}{r['no']} 신청마감={r.get('bidAttendReqCloseDatetime')} {r.get('name', '')[:30]}")
            reason = "dry-run"
            return
        i = 0
        while i < len(targets):
            row = targets[i]
            try:
                if srm is None:
                    srm = clients.SrmClient(interval=interval)
                found = process_notice(db, srm, row)
            except clients.SrmBlocked as exc:
                db.rollback()
                blocks += 1
                if blocks > conf["max_blocks"]:
                    raise
                # 쉬고 나서 몇 건 못 가 다시 막히면 더 오래 쉰다 (10분 → 20분 → … 최대 60분)
                streak = streak + 1 if processed - last_block_at < 5 else 1
                last_block_at = processed
                wait = min(conf["cooldown"] * streak, 3600)
                print(f"  SRM 차단 의심 ({exc}) → {wait}초 쉬고 {row['no']}부터 이어서 ({blocks}/{conf['max_blocks']})",
                      flush=True)
                time.sleep(wait)
                srm = None
                continue
            except Exception as exc:  # 공고 1건 오류로 전체 실행을 멈추지 않음 (다음 실행에서 재시도)
                db.rollback()
                found = set()
                print(f"  {row['no']} 오류: {type(exc).__name__}: {str(exc)[:200]}")
            processed += 1
            i += 1
            done = is_done(db, row["no"])
            completed += done
            failed += not done
            flag = db.execute("""SELECT min(c.rule_extracted) FROM bid_document_content c JOIN bid_attachment a
                                 ON a.id = c.attachment_id WHERE a.notice_no=? AND a.doc_kind='CRITERIA'""",
                              (row["no"],)).fetchone()[0]
            if flag is not None:
                rule_checked += 1
                rule_ok += flag
            print(f"  [{processed}/{len(targets)}] {row['no']} {'완료' if done else '실패·부분'} "
                  f"공고문={'O' if 'NOTICE' in found else 'X'} 적격심사기준={'O' if 'CRITERIA' in found else 'X'}",
                  flush=True)
            if conf["pause_every"] and processed % conf["pause_every"] == 0 and i < len(targets):
                time.sleep(conf["pause_seconds"])
    except clients.SrmBlocked as exc:
        reason = f"SRM 차단 의심으로 중단: {exc}"
        print(reason)
    except KeyboardInterrupt:
        reason = "사용자 중단"
        print(reason)
    finally:
        remaining = db.execute("SELECT count(*) FROM bid_notice WHERE company_id_src='COM01' "
                               "AND purchase_type='ConstructionService' AND bid_type='QualifiedEval' AND is_open=1 "
                               "AND (attend_req_close_at IS NULL OR attend_req_close_at >= ?)",
                               (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0]
        unprocessed = pending - processed  # 한도·차단·중단으로 이번에 못 한 공고 (다음 실행에서 이어서)
        db.execute("""UPDATE collect_run SET finished_at=?, target_count=?, processed_count=?, stopped_reason=?,
                      completed_count=?, deferred_count=?, failed_count=?, rule_checked_count=?, rule_extracted_count=?
                      WHERE id=?""",
                   (now(), len(targets), processed, reason, completed, unprocessed, failed, rule_checked, rule_ok,
                    run_id))
        db.commit()
        open_done = sum(is_done(db, n) for (n,) in db.execute(
            "SELECT notice_no FROM bid_notice WHERE company_id_src='COM01' AND purchase_type='ConstructionService' "
            "AND bid_type='QualifiedEval' AND is_open=1 AND (attend_req_close_at IS NULL OR attend_req_close_at >= ?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)))
        db.close()
        print(f"결과: 신규 {len(changes[NEW])}건, 기본정보 갱신 {len(changes[UPDATED])}건, 정정·첨부변경 "
              f"{len(changes[CORRECTED])}건, 상세 완료 {completed}건, 실패·부분 {failed}건, 미처리 {unprocessed}건 "
              f"/ 진행 중 공고 {remaining}건 중 상세 완료 {open_done}건 (collect_run id={run_id}, {reason})")
        if rule_checked:
            print(f"배점 규칙 추출: {rule_checked}건 중 {rule_ok}건 성공 ({rule_ok / rule_checked:.0%})")


if __name__ == "__main__":
    main()
