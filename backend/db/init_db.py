"""DB 초기화 단일 진입점.

backend/db/schema/ 의 SQL 파일을 파일명 순서(01 → 07)대로 적용한다.
모든 테이블은 CREATE TABLE IF NOT EXISTS 라 기존 DB에 다시 실행해도 데이터가 유지된다.
뷰(07)만 DROP 후 재생성한다(데이터 없음).

다른 모듈은 sqlite3.connect 대신 init_db() 를 사용한다:
    from backend.db.init_db import DEFAULT_DB_PATH, init_db
    db = init_db(DEFAULT_DB_PATH)

실행 (저장소 루트):
    python -m backend.db.init_db                 # 기본 bidmate.sqlite3
    python -m backend.db.init_db --db 경로.sqlite3
"""
import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = Path(__file__).with_name("schema")
DEFAULT_DB_PATH = ROOT / "bidmate.sqlite3"


def schema_files():
    files = sorted(SCHEMA_DIR.glob("[0-9][0-9]_*.sql"))
    if not files:
        raise FileNotFoundError(f"스키마 파일이 없습니다: {SCHEMA_DIR}")
    return files


def init_db(db_path=DEFAULT_DB_PATH):
    """스키마를 적용한 연결을 반환한다. 외래키 검사를 켠다."""
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys = ON")
    for path in schema_files():
        db.executescript(path.read_text(encoding="utf-8"))
    return db


def main():
    ap = argparse.ArgumentParser(description="입찰메이트 DB 초기화 (backend/db/schema/*.sql)")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = ap.parse_args()
    db = init_db(args.db)
    tables = db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    views = db.execute("SELECT count(*) FROM sqlite_master WHERE type='view'").fetchone()[0]
    print(f"{args.db}: 적용 {', '.join(p.name for p in schema_files())}")
    print(f"테이블 {tables}개, 뷰 {views}개, integrity={db.execute('PRAGMA integrity_check').fetchone()[0]}")
    db.close()


if __name__ == "__main__":
    main()
