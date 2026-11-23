"""AI 파트(LLM)가 읽을 첨부를 넘겨주는 도구. DB에는 쓰지 않는다.

1) 파서가 배점을 못 뽑은 공고의 적격심사기준: 수집기가 이미 받아 둔 파일을 그대로 넘긴다
   (PDF 표·분수 깨짐, 기준서 금액 구간이 공고와 안 맞음 등).
   로컬 파일이 없으면 SRM에서 같은 이름의 첨부를 받아온다.
2) 그 밖의 SRM 첨부(예: '시방서 등.zip'): 수집기는 '공고문'·'적격심사' 이름만 받으므로 필요할 때 이름으로 골라 받는다.

    python -m backend.collector.fetch_attachments --llm-targets                    # 파서가 못 뽑은 공고
    python -m backend.collector.fetch_attachments E012608048 --saved              # 받아 둔 기준서 복사
    python -m backend.collector.fetch_attachments E012608185 --list               # SRM 첨부 목록만
    python -m backend.collector.fetch_attachments E012608185 --name "\\.zip$"     # 이름이 맞는 SRM 첨부를 받음
    (저장 폴더 기본값: downloads/<공고번호>/extra)

    from backend.collector.fetch_attachments import saved_documents, fetch_attachments, llm_targets
    for no in llm_targets():
        for name, data in saved_documents(no):       # 기준서 (파일명, 바이트)
            ...
    for name, data in fetch_attachments("E012608185", r"\\.zip$"):
        ...
"""
import argparse
import re
import sqlite3

from backend.collector import clients
from backend.db.init_db import DEFAULT_DB_PATH, ROOT


def list_attachments(srm, notice_no):
    """→ SRM 첨부 목록 [{'id', 'name', ...}]. 공고를 못 찾으면 빈 목록."""
    row = srm.find_bid(notice_no)
    if row is None:
        return []
    group = srm.basic_info(row["id"]).get("attachFileGroupId")
    return srm.attachments(group) if group else []


def fetch_attachments(notice_no, pattern=r"\.zip$", srm=None):
    """이름이 pattern(정규식)에 맞는 첨부 → [(파일명, 바이트)]. SRM 요청 간격은 SrmClient 기본값(3초)."""
    srm = srm or clients.SrmClient()
    return [(att["name"], srm.download(att["id"]))
            for att in list_attachments(srm, notice_no) if re.search(pattern, att.get("name") or "", re.I)]


def saved_documents(notice_no, kinds=("CRITERIA",), db_path=DEFAULT_DB_PATH):
    """수집기가 이미 받아 둔 첨부 → [(파일명, 바이트)]. 기본은 적격심사기준 전부 (HWP·HWPX·PDF).
    로컬 파일이 없으면(다른 PC 등) SRM에서 같은 이름의 첨부를 받아온다."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = db.execute(f"""
        SELECT filename, local_path FROM bid_attachment
        WHERE notice_no=? AND download_status='SUCCESS' AND doc_kind IN ({",".join("?" * len(kinds))})
        ORDER BY id""", (notice_no, *kinds)).fetchall()
    db.close()
    out, missing = [], []
    for filename, local_path in rows:
        path = ROOT / (local_path or "")
        if local_path and path.is_file():
            out.append((filename, path.read_bytes()))
        else:
            missing.append(filename)
    if missing:
        srm = clients.SrmClient()
        out += [(att["name"], srm.download(att["id"]))
                for att in list_attachments(srm, notice_no) if att.get("name") in missing]
    return out


def llm_targets(db_path=DEFAULT_DB_PATH):
    """LLM이 아직 처리할 공고번호 (extract_evaluation_rules --apply 를 돌린 뒤 기준).
    = 기준서 본문 rule_extracted=0 (규칙 파서가 다 못 뽑음) 이면서
      ① 배점 행이 없음 → 기준서 전체를 읽고 채움 (PDF·zip·구간 불일치·표 못 찾음 등)
      ② 파서가 넣은 REVIEW 행이 남아 있음 → 그 행(parameters.설명, source_quote)만 읽고 고침
    rule_extracted 는 '규칙으로 뽑았나'라서 LLM이 채운 뒤에도 0으로 남는다. LLM이 고친 행은 updated_by='LLM' 이라
    ② 에서 빠지고, 행이 생기면 ① 에서도 빠져서 다시 부르지 않는다."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    targets = [r[0] for r in db.execute("""
        SELECT DISTINCT a.notice_no FROM bid_attachment a
        JOIN bid_document_content c ON c.attachment_id = a.id
        WHERE a.doc_kind='CRITERIA' AND c.rule_extracted = 0
          AND (NOT EXISTS (SELECT 1 FROM evaluation_rule e WHERE e.notice_no = a.notice_no)
               OR EXISTS (SELECT 1 FROM evaluation_rule e WHERE e.notice_no = a.notice_no
                          AND e.updated_by = 'PARSER' AND e.verification_status = 'REVIEW'))
        ORDER BY a.notice_no""")]
    db.close()
    return targets


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("notice_no", nargs="?")
    ap.add_argument("--name", default=r"\.zip$", help="받을 첨부 이름 정규식 (기본: zip)")
    ap.add_argument("--list", action="store_true", help="목록만 출력")
    ap.add_argument("--saved", action="store_true", help="이미 받아 둔 적격심사기준을 저장 폴더로 복사")
    ap.add_argument("--llm-targets", action="store_true", help="파서가 배점을 못 뽑은 공고번호 출력 (LLM 대상)")
    ap.add_argument("--out", help="저장 폴더 (기본: downloads/<공고번호>/extra)")
    args = ap.parse_args()
    if args.llm_targets:
        print("\n".join(llm_targets()) or "없음")
        return
    if not args.notice_no:
        ap.error("공고번호가 필요합니다")
    out = Path(args.out) if args.out else ROOT / "downloads" / args.notice_no / "extra"
    if args.saved:
        files = saved_documents(args.notice_no)
        out.mkdir(parents=True, exist_ok=True)
        for name, data in files:
            (out / re.sub(r'[\\/:*?"<>|]', "_", name)).write_bytes(data)
            print(f"{name} ({len(data):,} B) → {out}")
        if not files:
            print("받아 둔 기준서 없음")
        return
    srm = clients.SrmClient()
    if args.list:
        for att in list_attachments(srm, args.notice_no):
            print(att.get("name"))
        return
    files = fetch_attachments(args.notice_no, args.name, srm)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in files:
        (out / re.sub(r'[\\/:*?"<>|]', "_", name)).write_bytes(data)
        print(f"{name} ({len(data):,} B) → {out}")
    if not files:
        print("이름이 맞는 첨부 없음")


if __name__ == "__main__":
    main()
