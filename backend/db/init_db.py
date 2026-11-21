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
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = Path(__file__).with_name("schema")


def resolve_db_path():
    """수집기·웹 서버·예약 실행이 같은 DB 파일을 쓰도록 경로를 한 곳에서 정한다.
    우선순위: 환경 변수 BIDMATE_DB → 루트 .env 의 BIDMATE_DB= → 저장소 루트 bidmate.sqlite3.
    상대 경로는 실행 위치(cwd)가 아니라 저장소 루트 기준으로 해석한다."""
    value = os.environ.get("BIDMATE_DB")
    env = ROOT / ".env"
    if not value and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("BIDMATE_DB="):
                value = line.split("=", 1)[1].strip()
    path = Path(value) if value else Path("bidmate.sqlite3")
    return path if path.is_absolute() else ROOT / path


DEFAULT_DB_PATH = resolve_db_path()


def schema_files():
    files = sorted(SCHEMA_DIR.glob("[0-9][0-9]_*.sql"))
    if not files:
        raise FileNotFoundError(f"스키마 파일이 없습니다: {SCHEMA_DIR}")
    return files


# 기존 DB에 컬럼 추가 (CREATE TABLE IF NOT EXISTS 는 이미 있는 테이블에 컬럼을 더하지 않으므로)
# (테이블, 컬럼, ADD COLUMN 정의) — 새 DB에는 schema/*.sql 정의로 이미 들어가 있어 건너뛴다
COLUMN_MIGRATIONS = [
    ("bid_notice", "api_created_at", "TEXT"),
    ("bid_notice", "srm_revision", "TEXT"),
    ("bid_notice", "needs_recollect", "INTEGER NOT NULL DEFAULT 0 CHECK (needs_recollect IN (0, 1))"),
    ("bid_notice", "recollect_reason", "TEXT"),
    ("bid_notice", "last_collected_at", "TEXT"),
    # SRM overHeadCostAmount 는 경비. 예전 수집기는 net_construction_cost(순공사원가)에 잘못 저장했음
    # → 기존 행 정정은 backend/db/fix_overhead_cost.py (검증 후 --apply)
    ("bid_notice", "overhead_cost_amount", "INTEGER"),
    ("collect_run", "new_notice_count", "INTEGER"),
    ("collect_run", "completed_count", "INTEGER"),
    ("collect_run", "deferred_count", "INTEGER"),
    ("collect_run", "failed_count", "INTEGER"),
    ("collect_run", "mode", "TEXT"),
    ("collect_run", "window_begin", "TEXT"),
    ("collect_run", "window_end", "TEXT"),
    ("collect_run", "list_synced", "INTEGER"),
    ("collect_run", "updated_count", "INTEGER"),
    ("collect_run", "corrected_count", "INTEGER"),
    # 2026-11-21 범위 정리
    ("bid_document_content", "parser_version", "TEXT"),
    ("company_document_check", "issued_at", "TEXT"),
    ("company_evaluation_draft", "updated_by", "TEXT REFERENCES app_user(user_id) ON DELETE SET NULL"),
    ("srm_region_district_master", "si_name", "TEXT"),
    ("srm_region_district_master", "gun_name", "TEXT"),
    ("srm_region_district_master", "gu_name", "TEXT"),
]

# 시군구 이름을 시/군/구 칸으로 나눈다. region_name 은 'X시', 'X군', 'X구', 'X시 Y구' 네 꼴뿐 (253건 확인)
FILL_DISTRICT_PARTS = """
UPDATE srm_region_district_master SET
  si_name  = CASE WHEN instr(region_name, ' ') > 0 THEN substr(region_name, 1, instr(region_name, ' ') - 1)
                  WHEN region_name LIKE '%시' THEN region_name END,
  gun_name = CASE WHEN instr(region_name, ' ') = 0 AND region_name LIKE '%군' THEN region_name END,
  gu_name  = CASE WHEN instr(region_name, ' ') > 0 THEN substr(region_name, instr(region_name, ' ') + 1)
                  WHEN region_name LIKE '%구' THEN region_name END"""

# 2026-11-21 범위 정리로 없앤 표, 정의를 바꾼 표. 데이터가 있으면 지우지 않고 멈춘다
RETIRED_TABLES = ("document_extraction", "license_equivalence", "company_notification_setting", "document_template_item")
REDEFINED_TABLES = (("evaluation_rule", "rule_type"),        # (표, 새 정의에만 있는 칸)
                    ("favorite_notice", "user_id"),
                    ("notice_notification", "user_id"))


def drop_retired(db):
    """없앤 표와 옛 정의 표를 지운다(비어 있을 때만). 옛 정의 표는 뒤이어 스키마 파일이 새 정의로 만든다."""
    existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    targets = [t for t in RETIRED_TABLES if t in existing]
    targets += [t for t, column in REDEFINED_TABLES
                if t in existing and column not in {r[1] for r in db.execute(f"PRAGMA table_info({t})")}]
    for table in targets:
        rows = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if rows:
            raise RuntimeError(f"{table} 에 데이터 {rows}건이 있어 지우지 않았습니다. 백업 후 옮길 방법을 정하세요.")
    if targets:
        db.execute("DROP VIEW IF EXISTS v_recommendation")  # 옛 favorite_notice 를 참조 → 07 이 다시 만든다
        for table in targets:
            db.execute(f"DROP TABLE {table}")
        db.commit()
    return targets


def migrate(db):
    """누락 컬럼 추가 + 값 채우기. 여러 번 실행해도 결과가 같다."""
    added = []
    for table, column, ddl in COLUMN_MIGRATIONS:
        columns = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            added.append(f"{table}.{column}")
    # 정정 감지 기준값: 기존 행은 저장해 둔 API 원본의 createDatetime 으로 채운다
    db.execute("UPDATE bid_notice SET api_created_at = json_extract(api_raw, '$.createDatetime') "
               "WHERE api_created_at IS NULL")
    db.execute("UPDATE bid_notice SET last_collected_at = (SELECT fetched_at FROM bid_restriction_snapshot s "
               "WHERE s.notice_no = bid_notice.notice_no) WHERE last_collected_at IS NULL")
    db.execute(FILL_DISTRICT_PARTS)
    db.commit()
    return added


def init_db(db_path=DEFAULT_DB_PATH):
    """스키마를 적용한 연결을 반환한다. 외래키 검사를 켠다."""
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys = ON")
    drop_retired(db)
    for path in schema_files():
        db.executescript(path.read_text(encoding="utf-8"))
    migrate(db)
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
