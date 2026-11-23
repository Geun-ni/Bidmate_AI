"""한전 입찰 공고 조회 (웹 API용). 수집기가 쓰는 SQLite DB를 읽기 전용으로 연다.

- 목록: 공식 API 기본정보가 저장된 공고는 SRM 상세 수집 전이어도 보여 준다.
- 상세: 면허·적격자격 조건은 SRM 조회가 성공한 경우에만 값을 주고, 조회 전·실패는 '확인 필요'.
- 업체 매칭 결과가 아니다 (company_bid_eligibility 는 읽지 않음).
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from backend.db.init_db import DEFAULT_DB_PATH

SCOPE = """company_id_src = 'COM01' AND purchase_type = 'ConstructionService' AND bid_type = 'QualifiedEval'"""
OPEN = """is_open = 1 AND (attend_req_close_at IS NULL OR attend_req_close_at >= :now)"""
LIST_COLUMNS = ("notice_no", "title", "attend_req_begin_at", "attend_req_close_at", "bid_begin_at",
                "bid_end_at", "presumed_price")
CONDITION_CATEGORIES = {"LICENSE": "license", "QUALIFIED": "qualification"}


class DatabaseMissing(Exception):
    pass


def connect(db_path=None):
    path = Path(db_path or DEFAULT_DB_PATH)
    if not path.exists():
        raise DatabaseMissing(f"DB 파일이 없습니다: {path}")
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _detail_status(db, notice_no):
    """SRM 상세 수집 상태: 완료 / 부분 / 실패 / 미수집 (수집기 is_done 과 같은 기준)."""
    snap = db.execute("SELECT fetch_status FROM bid_restriction_snapshot WHERE notice_no=?", (notice_no,)).fetchone()
    if snap is None:
        return "미수집"
    if snap["fetch_status"] == "SUCCESS":
        row = db.execute("SELECT notice_doc_found, criteria_doc_found, needs_recollect FROM bid_notice "
                         "WHERE notice_no=?", (notice_no,)).fetchone()
        if row["needs_recollect"]:
            return "정정 재수집 대기"
        return "완료" if None not in (row["notice_doc_found"], row["criteria_doc_found"]) else "부분"
    return "부분" if snap["fetch_status"] == "PARTIAL" else "실패"


def list_notices(db, include_closed=False, q=""):
    where = SCOPE if include_closed else f"{SCOPE} AND {OPEN}"
    params = {"now": _now()}
    keyword = q.strip().lower()
    if keyword:
        where += " AND (instr(lower(title), :keyword) > 0 OR instr(lower(notice_no), :keyword) > 0)"
        params["keyword"] = keyword
    rows = db.execute(f"""SELECT {", ".join(LIST_COLUMNS)} FROM bid_notice WHERE {where}
                          ORDER BY attend_req_close_at IS NULL, attend_req_close_at, notice_no""",
                      params).fetchall()
    notices = [{**dict(r), "detail_status": _detail_status(db, r["notice_no"])} for r in rows]
    return {"notices": notices, "count": len(notices), "include_closed": include_closed,
            "last_run": last_run(db)}


def _conditions(db, notice_no, category):
    """→ {"status": "수집됨"|"제한 없음"|"확인 필요", "items": [...], "auto_eval": ...}"""
    cat = db.execute("SELECT fetch_status, item_count, error FROM bid_restriction_category "
                     "WHERE notice_no=? AND category=?", (notice_no, category)).fetchone()
    if cat is None or cat["fetch_status"] != "SUCCESS":
        reason = "아직 조회하지 않음" if cat is None else f"조회 실패: {cat['error'] or ''}".strip()
        return {"status": "확인 필요", "reason": reason, "items": []}
    items = [dict(r) for r in db.execute("SELECT code, name, area_code, sub_area_code, sub_area_name FROM bid_restriction_item "
                                         "WHERE notice_no=? AND category=? ORDER BY id", (notice_no, category))]
    if cat["item_count"] != len(items):  # 건수는 있는데 행이 없으면 저장이 어긋난 것
        return {"status": "확인 필요", "reason": "조회 건수와 저장 행이 다름", "items": []}
    return {"status": "수집됨" if items else "제한 없음", "items": items}


def get_notice(db, notice_no):
    columns = {r["name"] for r in db.execute("PRAGMA table_info(bid_notice)")}
    extra = [c for c in ("place_name", "notice_date", "progress_state", "estimated_price_basic_amount",
                         "approximate_amount", "a_amount", "overhead_cost_amount", "open_bid_at",
                         "notice_doc_found", "criteria_doc_found", "last_collected_at") if c in columns]
    row = db.execute(f"SELECT {', '.join(LIST_COLUMNS + tuple(extra))} FROM bid_notice WHERE notice_no=? AND {SCOPE}",
                     (notice_no,)).fetchone()
    if row is None:
        return None
    notice = dict(row)
    notice["detail_status"] = _detail_status(db, notice_no)
    snap = db.execute("SELECT license_auto_eval_method, qualified_auto_eval_method FROM bid_restriction_snapshot "
                      "WHERE notice_no=?", (notice_no,)).fetchone()
    for category, key in CONDITION_CATEGORIES.items():
        notice[key] = _conditions(db, notice_no, category)
        if snap is not None and notice[key]["status"] != "확인 필요":
            notice[key]["auto_eval"] = snap[f"{'license' if category == 'LICENSE' else 'qualified'}_auto_eval_method"]
    notice["region"] = _conditions(db, notice_no, "REGION")
    notice["license_main"] = _conditions(db, notice_no, "LICENSE_MAIN")
    notice["attachments"] = [dict(attachment) for attachment in db.execute("""
        SELECT id, doc_kind, filename, file_ext, source, download_status, size_bytes, fetched_at
        FROM bid_attachment WHERE notice_no=? ORDER BY doc_kind, filename
    """, (notice_no,))]
    return notice


def _rule_node(row):
    params = row["parameters"]
    return {
        "id": row["id"], "key": str(row["position"]),  # key: 점수 계산 입력 키 (재추출해도 문서가 같으면 그대로)
        "category": row["category"], "name": row["item_name"], "rule_type": row["rule_type"],
        "max_score": row["max_score"], "formula": row["formula"], "formula_source": row["formula_source"],
        "parameters": json.loads(params) if params else None, "input_key": row["input_key"],
        "rounding": {"digits": row["rounding_digits"], "mode": row["rounding_mode"]}
        if row["rounding_digits"] is not None else None,
        "verification_status": row["verification_status"], "updated_by": row["updated_by"],
        "source_file": row["filename"],  # 출처 기준서 파일 (근거 문장은 내보내지 않음)
        "children": [],
    }


def get_evaluation(db, notice_no):
    """적격심사 배점·산식 (evaluation_rule) → 계층 구조. 공고가 범위 밖이면 None.
    배점이 없으면 이유(기준서 없음 / 파서가 못 읽어 AI 분석 대기)를 준다."""
    notice = db.execute(f"SELECT notice_no, criteria_doc_found FROM bid_notice WHERE notice_no=? AND {SCOPE}",
                        (notice_no,)).fetchone()
    if notice is None:
        return None
    rows = db.execute("""SELECT e.*, a.filename FROM evaluation_rule e LEFT JOIN bid_attachment a ON a.id = e.attachment_id
                         WHERE e.notice_no=? ORDER BY e.position""", (notice_no,)).fetchall()
    if not rows:
        has_doc = db.execute("SELECT 1 FROM bid_attachment WHERE notice_no=? AND doc_kind='CRITERIA' "
                             "AND download_status='SUCCESS'", (notice_no,)).fetchone()
        reason = ("기준서를 자동으로 읽지 못함 — AI 분석 대기" if has_doc else
                  "적격심사기준을 아직 받지 못함" if notice["criteria_doc_found"] is None else "적격심사기준 없음")
        return {"notice_no": notice_no, "status": "확인 필요", "reason": reason, "summary": None, "rules": []}
    nodes = {r["id"]: _rule_node(r) for r in rows}
    tree = []
    for r in rows:
        parent = nodes.get(r["parent_id"]) if r["parent_id"] else None
        (parent["children"] if parent else tree).append(nodes[r["id"]])
    top = {n["category"]: n for n in tree}
    review = sum(1 for r in rows if r["verification_status"] == "REVIEW")
    summary = {
        "pass_score": top["PASS_SCORE"]["parameters"]["pass_score"] if "PASS_SCORE" in top else None,
        "price_max": top["PRICE"]["max_score"] if "PRICE" in top else None,
        "performance_max": sum(n["max_score"] or 0 for n in tree if n["rule_type"] == "GROUP") or None,
        "review_count": review,
        "source_file": rows[0]["filename"],
        "extracted_at": max(r["updated_at"] for r in rows),
    }
    return {"notice_no": notice_no, "status": "확인 필요" if review else "추출됨",
            "reason": f"원문 확인이 필요한 항목 {review}개" if review else None, "summary": summary, "rules": tree}


def last_run(db):
    columns = {r["name"] for r in db.execute("PRAGMA table_info(collect_run)")}
    wanted = [c for c in ("id", "started_at", "finished_at", "target_count", "processed_count", "stopped_reason",
                          "new_notice_count", "completed_count", "deferred_count", "failed_count", "mode",
                          "updated_count", "corrected_count") if c in columns]
    row = db.execute(f"SELECT {', '.join(wanted)} FROM collect_run WHERE stopped_reason IS NOT 'dry-run' "
                     "ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None
