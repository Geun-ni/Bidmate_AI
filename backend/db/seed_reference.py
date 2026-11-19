"""기준정보(master) 시드: docs/planning/reference/*.xlsx → SQLite.

  - 면허종류.xlsx                → license_master
  - 제한경쟁입찰자격 종류.xlsx     → restricted_qualification_master
  - 지역제한 목록.xlsx            → srm_region_district_master
  - 지역그룹(18개)·자격 상하위 인정 규칙 → 업체정보 데이터 정의서 v0.2 §5.3, §6.2

외부 패키지 없이 xlsx(zip+XML)를 직접 읽는다. 코드는 앞자리 0 보존을 위해 문자열로 저장.
INSERT OR REPLACE 라 여러 번 실행해도 같은 결과(업체·공고 데이터는 건드리지 않음).
실행 (저장소 루트):
  python -m backend.db.seed_reference            # 기본 bidmate.sqlite3
"""
import argparse
import html
import re
import zipfile
from pathlib import Path

from backend.db.init_db import DEFAULT_DB_PATH, init_db

ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "docs" / "planning" / "reference"

# 업체정보 데이터 정의서 §6.2 (U·W는 과거 공고 판독용, 업체 선택 불가)
REGION_GROUPS = [("B", "부산"), ("C", "충북"), ("D", "대전"), ("E", "제주"), ("F", "강원"), ("G", "경기"),
                 ("H", "인천"), ("I", "전남광주"), ("J", "전북"), ("K", "경북"), ("L", "세종"), ("M", "경남"),
                 ("R", "충남"), ("S", "서울"), ("T", "대구"), ("U", "전남"), ("W", "광주"), ("Z", "울산")]
INACTIVE_GROUPS = {"U", "W"}

# 정의서 §5.3: 하위 < 상위 체인. 상위 보유 시 하위 요구 충족 (자기 자신 포함)
QUALIFICATION_CHAINS = [["1", "B", "C"], ["D", "E"], ["M", "O"], ["U", "V"], ["W", "X", "Y"]]


def read_xlsx(path):
    """첫 시트의 행 목록 (열 문자 → 값 dict)."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        xml = z.read("xl/sharedStrings.xml").decode("utf-8")
        shared = [html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))
                  for si in re.findall(r"<si>(.*?)</si>", xml, re.S)]
    sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
    rows = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", z.read(sheet).decode("utf-8"), re.S):
        cells = {}
        for c in re.finditer(r'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(.*?)</c>)', row, re.S):
            body = c.group(3) or ""
            v = re.search(r"<v>(.*?)</v>", body, re.S)
            if 't="s"' in c.group(2) and v:
                value = shared[int(v.group(1))]
            elif v:
                value = html.unescape(v.group(1))
            else:
                value = html.unescape(re.sub(r"<[^>]+>", "", body))
            cells[c.group(1)] = value.strip()
        rows.append(cells)
    return rows


def seed(db):
    # 지역그룹
    # INSERT OR REPLACE 는 행을 지웠다 다시 넣어 업체가 참조 중이면 외래키 오류가 나므로 UPSERT 사용
    db.executemany("INSERT INTO srm_region_group_master VALUES (?,?,?) ON CONFLICT(area_limit) DO UPDATE SET "
                   "area_limit_name=excluded.area_limit_name, is_active=excluded.is_active",
                   [(code, name, 0 if code in INACTIVE_GROUPS else 1) for code, name in REGION_GROUPS])
    group_by_name = {name: code for code, name in REGION_GROUPS}

    # 면허 (헤더: 면허명, 면허코드)
    rows = read_xlsx(REF / "면허종류.xlsx")[1:]
    licenses = [(r["B"], r["A"]) for r in rows if r.get("A") and r.get("B")]
    db.executemany("INSERT INTO license_master (license_code, license_name) VALUES (?,?) "
                   "ON CONFLICT(license_code) DO UPDATE SET license_name=excluded.license_name", licenses)

    # 제한경쟁입찰자격 (헤더: 회사명, 코드, 지역, 코드명)
    rows = read_xlsx(REF / "제한경쟁입찰자격 종류.xlsx")[1:]
    quals = [(r["B"], r["D"]) for r in rows if r.get("B") and r.get("D")]
    db.executemany("INSERT INTO restricted_qualification_master (qualification_code, qualification_name) "
                   "VALUES (?,?) ON CONFLICT(qualification_code) DO UPDATE SET "
                   "qualification_name=excluded.qualification_name", quals)
    known = {code for code, _ in quals}
    equivalences = []
    for chain in QUALIFICATION_CHAINS:
        for i, held in enumerate(chain):
            for accepted in chain[:i + 1]:
                if held in known and accepted in known:
                    equivalences.append((held, accepted, "업체정보 데이터 정의서 v0.2 §5.3"))
    db.executemany("INSERT INTO restricted_qualification_equivalence "
                   "(held_qualification_code, accepted_requirement_code, source_note) VALUES (?,?,?) "
                   "ON CONFLICT DO UPDATE SET source_note=excluded.source_note", equivalences)

    # 시군구 (헤더: 회사명, 코드, 지역, 코드명)
    rows = read_xlsx(REF / "지역제한 목록.xlsx")[1:]
    districts, unknown = [], set()
    for r in rows:
        group = group_by_name.get(r.get("C"))
        if not group:
            unknown.add(r.get("C"))
            continue
        # 엑셀이 코드를 숫자로 저장해 앞자리 0이 빠짐(예: 011013 → 11013) → SRM 형식 6자리로 복원
        code = r["B"].zfill(6) if r["B"].isdigit() else r["B"]
        districts.append((f"{group}_{code}", group, code, r["D"]))
    db.executemany("INSERT INTO srm_region_district_master (code_id, area_limit, region_code, region_name) "
                   "VALUES (?,?,?,?) ON CONFLICT(code_id) DO UPDATE SET region_name=excluded.region_name",
                   districts)

    return {"license_master": len(licenses), "restricted_qualification_master": len(quals),
            "restricted_qualification_equivalence": len(equivalences),
            "srm_region_group_master": len(REGION_GROUPS), "srm_region_district_master": len(districts),
            "unmapped_region_groups": sorted(x for x in unknown if x)}


def main():
    ap = argparse.ArgumentParser(description="기준정보 시드")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = ap.parse_args()
    db = init_db(args.db)
    with db:
        result = seed(db)
    for k, v in result.items():
        print(f"{k}: {v}")
    db.close()


if __name__ == "__main__":
    main()
