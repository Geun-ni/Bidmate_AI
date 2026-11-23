"""업체가 직접 올린 적격심사기준 (zip 이거나 수집기가 기준서를 못 찾은 공고).

올린 업체만 쓴다: company_uploaded_criteria 에 본문과 규칙 추출 결과(JSON)를 남기고, 공용 evaluation_rule 은 건드리지 않는다.
원본 파일은 저장하지 않는다. 공용 배점이 없는 공고에서만 점수 계산(scoring.calculate)에 쓴다.
"""
import hashlib
import json
from datetime import datetime

from backend import auth, notices_api
from backend.collector.doc_reader import detect_ext, document_problem, read_document
from backend.db.extract_evaluation_rules import extract_notice

MAX_BYTES = 20 * 1024 * 1024
ALLOWED = ("hwp", "hwpx", "pdf")


class UploadError(ValueError):
    pass


def _now():
    return datetime.now().isoformat(timespec="seconds")


def upload(db, company_id, notice_no, filename, data, user):
    """기준서 파일 → 본문 전처리 → 규칙 추출 → 저장 (같은 업체·공고는 덮어씀). → get() 결과, 권한·공고 없으면 None"""
    if not auth.can_access_company(db, user, company_id):
        return None
    if db.execute("SELECT 1 FROM bid_notice WHERE notice_no=?", (notice_no,)).fetchone() is None:
        return None
    filename = (filename or "").strip().replace("/", "_").replace("\\", "_")[:200]
    if not filename:
        raise UploadError("파일 이름(filename)이 필요합니다.")
    if not data:
        raise UploadError("빈 파일입니다.")
    if len(data) > MAX_BYTES:
        raise UploadError(f"파일이 너무 큽니다 (최대 {MAX_BYTES // 1024 // 1024}MB).")
    if data[:2] == b"PK" and filename.lower().endswith(".zip"):
        raise UploadError("zip 은 풀어서 적격심사기준 파일(hwp·hwpx·pdf)만 올려 주세요.")
    problem = document_problem(filename, data)
    ext = detect_ext(filename, data)
    if problem or ext not in ALLOWED:
        raise UploadError(problem or f"hwp·hwpx·pdf 파일만 올릴 수 있습니다 ({ext or '알 수 없는 형식'}).")
    sha = hashlib.sha256(data).hexdigest()
    try:
        lines, _ = read_document(filename, data)
        text, read_status, error = "\n".join(lines), "SUCCESS", None
    except Exception as exc:  # 손상·암호화
        text, read_status, error = None, "FAILED", f"{type(exc).__name__}: {exc}"[:500]
    rules, issues, flag = [], [], 0
    if text is not None:
        res = extract_notice(db, notice_no, docs=[(None, sha, text, ext)])
        rules, issues = res["rows"], res["issues"] + res.get("detail_issues", [])
        flag = int(bool(res.get("rule_extracted")))
    else:
        issues = ["본문을 읽지 못함"]
    with db:
        db.execute("""INSERT INTO company_uploaded_criteria (company_id, notice_no, filename, file_ext, size_bytes,
                      sha256, read_status, text, error, rule_extracted, rules, issues, rules_updated_by, uploaded_by,
                      uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'PARSER',?,?)
                      ON CONFLICT(company_id, notice_no) DO UPDATE SET filename=excluded.filename,
                      file_ext=excluded.file_ext, size_bytes=excluded.size_bytes, sha256=excluded.sha256,
                      read_status=excluded.read_status, text=excluded.text, error=excluded.error,
                      rule_extracted=excluded.rule_extracted, rules=excluded.rules, issues=excluded.issues,
                      rules_updated_by='PARSER', uploaded_by=excluded.uploaded_by, uploaded_at=excluded.uploaded_at""",
                   (company_id, notice_no, filename, ext, len(data), sha, read_status, text, error, flag,
                    json.dumps(rules, ensure_ascii=False), json.dumps(issues, ensure_ascii=False),
                    user["user_id"], _now()))
    return get(db, company_id, notice_no, user)


def _row(db, company_id, notice_no):
    return db.execute("SELECT * FROM company_uploaded_criteria WHERE company_id=? AND notice_no=?",
                      (company_id, notice_no)).fetchone()


def evaluation(db, company_id, notice_no):
    """업로드한 기준서의 배점·산식 (notices_api.get_evaluation 과 같은 모양) 또는 None. 권한 확인은 호출자가 한다."""
    row = _row(db, company_id, notice_no)
    if row is None:
        return None
    rules = json.loads(row["rules"])
    if not rules:
        reason = "올린 기준서를 자동으로 읽지 못함 — AI 분석 대기" if row["read_status"] == "SUCCESS" \
            else "올린 파일의 본문을 읽지 못함"
        return {"notice_no": notice_no, "status": "확인 필요", "reason": reason, "summary": None, "rules": []}
    nodes = [{"id": i, "position": i, "parent_id": r["parent"] + 1 if r.get("parent") is not None else None,
              "category": r["category"], "item_name": r["item_name"], "rule_type": r["rule_type"],
              "max_score": r.get("max_score"), "formula": r.get("formula"), "formula_source": r.get("formula_source"),
              "parameters": json.dumps(r["parameters"], ensure_ascii=False) if r.get("parameters") is not None
              else None, "input_key": r.get("input_key"), "rounding_digits": r.get("rounding_digits"),
              "rounding_mode": r.get("rounding_mode"), "verification_status": r["verification_status"],
              "updated_by": row["rules_updated_by"], "filename": row["filename"], "updated_at": row["uploaded_at"]}
             for i, r in enumerate(rules, 1)]
    return notices_api.build_evaluation(notice_no, nodes, source="업체 업로드")


def get(db, company_id, notice_no, user):
    """GET: 올린 파일 정보 + 배점·산식. 권한 없거나 올린 적 없으면 None"""
    if not auth.can_access_company(db, user, company_id):
        return None
    row = _row(db, company_id, notice_no)
    if row is None:
        return None
    return {"company_id": company_id, "notice_no": notice_no, "filename": row["filename"],
            "file_ext": row["file_ext"], "size_bytes": row["size_bytes"], "read_status": row["read_status"],
            "error": row["error"], "rule_extracted": row["rule_extracted"], "issues": json.loads(row["issues"]),
            "uploaded_by": row["uploaded_by"], "uploaded_at": row["uploaded_at"],
            "evaluation": evaluation(db, company_id, notice_no)}


def remove(db, company_id, notice_no, user):
    if not auth.can_access_company(db, user, company_id):
        return None
    with db:
        n = db.execute("DELETE FROM company_uploaded_criteria WHERE company_id=? AND notice_no=?",
                       (company_id, notice_no)).rowcount
    return {"deleted": n}
