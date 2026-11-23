"""첨부 본문 재파싱: downloads/ 에 보관한 원본(HWP·HWPX·PDF)을 새 전처리 파서(doc_reader)로 다시 읽어
bid_document_content 의 본문·수식을 바꾼다. SRM·공식 API 에 다시 요청하지 않는다.

- 원본 파일의 sha256 이 bid_attachment.sha256 과 같을 때만 바꾼다 (다른 파일로 덮어쓰지 않게)
- parser_version 이 이미 최신이면 건너뛴다 (--force 로 다시)
- 기본은 미리보기(DB 변경 없음). --apply 를 붙이면 DB 파일을 백업하고 스키마(init_db)를 적용한 뒤 한 트랜잭션으로 반영

    python -m backend.db.reparse_documents             # 미리보기
    python -m backend.db.reparse_documents --apply     # 반영 (bidmate.sqlite3.bak-YYYYmmddHHMMSS 생성)
"""
import argparse
import hashlib
import json
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from backend.collector.doc_reader import BAR, PARSER_VERSION, read_document
from backend.db.init_db import DEFAULT_DB_PATH, ROOT, init_db

PRIVATE_USE = re.compile(r"[-\U000F0000-\U0010FFFF]")


def plan(db, force=False):
    columns = {r[1] for r in db.execute("PRAGMA table_info(bid_document_content)")}
    version = "c.parser_version" if "parser_version" in columns else "NULL"
    rows = db.execute(f"""
        SELECT a.id, a.notice_no, a.filename, a.local_path, a.sha256, c.text, c.paragraph_count, c.equation_count,
               {version}
        FROM bid_attachment a JOIN bid_document_content c ON c.attachment_id = a.id
        WHERE a.download_status = 'SUCCESS' ORDER BY a.id""").fetchall()
    updates, skipped, stats = [], [], {"문서": len(rows)}
    for aid, notice_no, filename, local_path, sha, old_text, old_lines, old_eqs, current in rows:
        if current == PARSER_VERSION and not force:
            skipped.append((aid, "이미 최신 파서"))
            continue
        path = ROOT / (local_path or "")
        if not local_path or not path.exists():
            skipped.append((aid, "원본 파일 없음"))
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            skipped.append((aid, "원본 파일 지문이 DB와 다름"))
            continue
        try:
            lines, equations = read_document(filename, data)
        except Exception as exc:  # 기존 본문은 그대로 둔다
            skipped.append((aid, f"읽기 실패: {type(exc).__name__}: {exc}"[:200]))
            continue
        text = "\n".join(lines)
        if len(equations) != (old_eqs or 0):
            skipped.append((aid, f"수식 수가 달라짐 {old_eqs} → {len(equations)} (확인 필요)"))
            continue
        updates.append({"id": aid, "notice_no": notice_no, "text": text,
                        "equations": json.dumps(equations, ensure_ascii=False),
                        "lines": len(lines), "eqs": len(equations)})
        for key, value in (("기존 줄", old_lines or 0), ("새 줄", len(lines)),
                           ("기존 사설문자", len(PRIVATE_USE.findall(old_text or ""))),
                           ("새 사설문자", len(PRIVATE_USE.findall(text))),
                           ("남은 막대 문자", len(re.findall(BAR, text))),
                           ("표", text.count("[표]")), ("본문 안 수식 표시", text.count("[수식:")),
                           ("그림 표시", text.count("[이미지"))):
            stats[key] = stats.get(key, 0) + value
    return updates, skipped, stats


def show(updates, skipped, stats):
    print(f"바꿀 문서: {len(updates)}건 / 건너뜀: {len(skipped)}건 (전체 {stats['문서']}건, 파서 v{PARSER_VERSION})")
    for key, value in stats.items():
        if key != "문서":
            print(f"  {key}: {value:,}")
    for aid, why in skipped[:20]:
        print(f"  건너뜀 첨부 {aid}: {why}")


def apply(db_path, updates):
    backup = Path(f"{db_path}.bak-{datetime.now():%Y%m%d%H%M%S}")
    shutil.copy2(db_path, backup)
    db = init_db(db_path)  # parser_version 칸 등 스키마 적용
    try:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with db:
            changed = sum(db.execute("""UPDATE bid_document_content SET read_status='SUCCESS', text=?, equations=?,
                                        paragraph_count=?, equation_count=?, error=NULL, read_at=?, parser_version=?
                                        WHERE attachment_id=?""",
                                     (u["text"], u["equations"], u["lines"], u["eqs"], now, PARSER_VERSION, u["id"])).rowcount
                          for u in updates)
        left = db.execute("SELECT count(*) FROM bid_document_content WHERE parser_version IS NOT ?",
                          (PARSER_VERSION,)).fetchone()[0]
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        db.close()
    print(f"\n반영: {changed}건 (예상 {len(updates)}건), 옛 파서로 남은 본문 {left}건, integrity={integrity}, 백업 {backup}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--apply", action="store_true", help="백업 후 DB 반영 (없으면 미리보기만)")
    parser.add_argument("--force", action="store_true", help="이미 최신 파서로 읽은 본문도 다시 읽기")
    args = parser.parse_args()
    db = sqlite3.connect(f"file:{Path(args.db).resolve().as_posix()}?mode=ro", uri=True)
    updates, skipped, stats = plan(db, args.force)
    db.close()
    show(updates, skipped, stats)
    if args.apply and updates:
        apply(args.db, updates)
    elif not args.apply:
        print("\n미리보기만 했다. 반영하려면 --apply")


if __name__ == "__main__":
    main()
