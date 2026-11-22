"""적격심사기준 본문(bid_document_content) → evaluation_rule 추출 (규칙 파서, LLM 안 씀)

1단계: 공고 추정가격에 맞는 [별표 1] 배점표를 골라
  - 통과점수 (PASS_SCORE / THRESHOLD)
  - 입찰가격 배점과 평가산식 (PRICE / FORMULA): 기준율·계수·A값 차감·반올림·최저평점·상한 구간
  - 수행능력 묶음과 그 아래 분야별 배점 (GROUP), 가감점 범위, 기술자 미달 등 결격 감점 (FIXED)
2단계: 1단계 행 아래 세부 표 — 경영상태 구간표·기준치·신용등급표, 시공경험 산식·사정액, 사업소 숙지도,
  신인도·가감점 등급표, 감리 PQ 세부평가기준(참여감리원·유사용역·신용도·업무중첩도), 기술용역 사업수행능력 환산 산식. 세부 표를 못 읽으면 그 행만 REVIEW (detail_issues).

- 금액 구간이 여러 개면 bid_notice.presumed_price(추정가격) 가 들어가는 구간의 표를 쓴다.
  '전기·정보통신·소방공사 등은 …' 처럼 괄호 안 별도 구간이 있으면 전기공사 공고는 그 구간을 쓴다.
  고시금액은 문서에 적힌 값 → 공고일에 맞는 공식 고시금액 → 둘 다 없으면 2억~2.6억 사이를 판정 보류.
- 확인이 덜 된 공고(배점 합계 ≠ 100, 산식 못 찾음 등)는 verification_status='REVIEW' 로 넣고 사유를 남긴다.
- 기본은 미리보기(DB 변경 없음, 결과 JSON 저장). --apply 면 DB 백업 → 스키마 적용 → 파서가 쓴 행(updated_by='PARSER')만 지우고 다시 넣는다.

    python -m backend.db.extract_evaluation_rules                 # 미리보기 → dist/evaluation_rule_preview.json
    python -m backend.db.extract_evaluation_rules --notice E012608220
    python -m backend.db.extract_evaluation_rules --apply
"""
import argparse
import json
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from backend.db.init_db import DEFAULT_DB_PATH, ROOT, init_db

PREVIEW_PATH = ROOT / "dist" / "evaluation_rule_preview.json"
NOTICE_AMOUNT_RANGE = (200_000_000, 260_000_000)  # 고시금액을 모를 때 허용 범위 (이 안이면 판정 보류)
# 국가계약법 제4조 고시금액(물품·용역, 기획재정부 고시). 2년마다 바뀌므로 공고일이 기간 밖이면 판정 보류.
# 2024-12-24 고시: 2025-01-01 ~ 2026-12-31 2.3억원 (한전 문서 E012607070 '고시금액 이상(2.3억)'과 같음)
OFFICIAL_NOTICE_AMOUNTS = (("20250101", "20261231", 230_000_000),)
SPECIAL_TRADES = ("전기", "정보통신", "소방")      # 괄호 별도 구간을 쓰는 공사 종류


# ------------------------------------------------------------------ 금액 구간
def won(num, unit):
    n = float(num.replace(",", ""))
    return int(round(n * {"억": 1e8, "천만": 1e7, "백만": 1e6, "만": 1e4}[unit]))


AMOUNT = r"(?:(\d[\d,.]*)\s*(억|천만|백만|만)\s*원?|(고시\s*금액))"
BOUND = re.compile(AMOUNT + r"\s*(?:원)?\s*(이상|초과|미만|이하)")


def notice_amount(text, notice_date=None):
    """문서에 적힌 고시금액(예: '고시금액 이상(2.3억)') 우선, 없으면 공고일에 맞는 공식 고시금액"""
    m = re.search(r"고시\s*금액\s*(?:이상|미만)?\s*\(\s*(\d[\d.]*)\s*억\s*원?\s*\)", text)
    if m:
        return won(m.group(1), "억")
    return next((v for start, end, v in OFFICIAL_NOTICE_AMOUNTS if notice_date and start <= notice_date <= end), None)


def parse_band(line, gosi):
    """'추정가격 50억원 미만 10억원 이상' → (하한, 하한포함, 상한, 상한포함). 고시금액 미상이면 (값, 'GOSI')."""
    lo = hi = None
    lo_inc = hi_inc = True
    for m in BOUND.finditer(line):
        value = gosi if m.group(3) else won(m.group(1), m.group(2))
        if m.group(3) and value is None:
            value = "GOSI"
        kind = m.group(4)
        if kind in ("이상", "초과"):
            lo, lo_inc = value, kind == "이상"
        else:
            hi, hi_inc = value, kind == "이하"
    if lo is None and hi is None:
        return None
    return lo, lo_inc, hi, hi_inc


def in_band(price, band):
    """True / False / None(고시금액 미상으로 판정 불가)"""
    lo, lo_inc, hi, hi_inc = band
    results = []
    for value, is_lo, inc in ((lo, True, lo_inc), (hi, False, hi_inc)):
        if value is None:
            continue
        if value == "GOSI":
            a, b = NOTICE_AMOUNT_RANGE
            if is_lo:
                results.append(True if price >= b else False if price < a else None)
            else:
                results.append(True if price < a else False if price >= b else None)
            continue
        ok = (price >= value if inc else price > value) if is_lo else (price <= value if inc else price < value)
        results.append(ok)
    if False in results:
        return False
    return None if None in results else True


def band_for(line, gosi, special):
    """구간 제목 한 줄 → 구간. 괄호 안 '전기·정보통신·소방공사 등은 …' 구간은 special 공사일 때 쓴다."""
    line = re.sub(r"\s*/\s*", " ", line)  # 표 칸 안 줄바꿈: '고시금액 / 미만인 / 용역①'
    m = re.search(r"\(([^()]*(?:전기|정보통신|소방)[^()]*)\)", line)
    if m and special:
        alt = parse_band(m.group(1), gosi)
        if alt:
            return alt
    head = line[:m.start()] if m else line
    return parse_band(head, gosi)


# ------------------------------------------------------------------ 배점표 찾기
SCORE_HEADER = re.compile(r"배\s*점")
NOT_TOP_TABLE = re.compile(r"평가등급|등\s*급|제출서류|집계표|종합평가표|종합득점|세부평가|대표사|자기\s*평점|심사\s*평점")


def tables(lines):
    """[(시작 줄, 끝 줄)] — '[표]' ~ '[표 끝]'"""
    out, start = [], None
    for i, line in enumerate(lines):
        if line == "[표]":
            start = i
        elif line == "[표 끝]" and start is not None:
            out.append((start, i))
            start = None
    return out


def top_tables(lines, gosi, special):
    """입찰가격 행이 있는 배점표 → [{start, end, band, heading}]"""
    found = []
    for start, end in tables(lines):
        header = lines[start + 1] if start + 1 < end else ""
        body = "\n".join(lines[start + 1:end])
        if not re.search(r"입\s*찰\s*가\s*격", body) or NOT_TOP_TABLE.search(header):
            continue
        # 표 제목 줄(앞 표 끝 또는 금액 구간 제목까지)에 '종합평가표' 등이 있으면 PQ 세부 평가표라 뺀다
        title_lines = []
        for j in range(start - 1, max(-1, start - 4), -1):
            if lines[j] == "[표 끝]":
                break
            title_lines.append(lines[j])
            if band_for(lines[j], gosi, special):
                break
        if any(NOT_TOP_TABLE.search(l) and not re.match(r"\s*[※*\-【]", l) for l in title_lines):
            continue
        # 금액 구간이 칸으로 나뉜 표: '구분 | 고시금액 미만인 용역① | 고시금액이상 10억원미만인 용역② | …'
        # 머리줄이 2줄이면 둘째 줄에 구간: '구 분 | … | 배점한도' / '추정가격 5억원이상 | 추정가격 5억원미만'
        column_table = None
        for k in (1, 2):
            if start + k >= end:
                break
            cols = [band_for(c, gosi, special) for c in split_cells(lines[start + k])[(1 if k == 1 else 0):]]
            if sum(1 for c in cols if c) >= 2:
                column_table = {"start": start, "end": end, "band": None, "heading": lines[start + k],
                                "cols": cols, "data_start": start + k + 1}
                break
        if column_table:
            found.append(column_table)
            continue
        # 머리줄에 '배점'이 있거나, '심사항목 | 기준 | 심사방법' 양식
        if not (SCORE_HEADER.search(header) or re.match(r"심사\s*항목 \| 기\s*준", header)):
            continue
        heading, band = None, None
        for j in range(start - 1, max(-1, start - 8), -1):
            line = lines[j]
            if line == "[표 끝]":
                break
            b = band_for(line, gosi, special)
            if b:
                heading, band = line, b
                break
        found.append({"start": start, "end": end, "band": band, "heading": heading})
    return found


def choose_table(candidates, price):
    """→ (표, 사유). 표가 여러 개면 추정가격이 들어가는 구간."""
    if not candidates:
        return None, "배점표(입찰가격 행 포함)를 못 찾음"
    resolved = []
    for c in candidates:
        if c.get("cols") is None:
            resolved.append(c)
            continue
        if price is None:
            return None, "추정가격 없음 → 구간 칸 선택 불가"
        fits = [i for i, b in enumerate(c["cols"]) if b and in_band(price, b) is True]
        if any(b and in_band(price, b) is None for b in c["cols"]):
            return None, "고시금액이 문서에 없어 구간 판정 보류"
        if len(fits) == 1:
            resolved.append({**c, "col": fits[0], "band": c["cols"][fits[0]]})
    candidates = resolved
    if not candidates:
        return None, f"구간 칸 표에서 추정가격 {price:,}원에 맞는 칸 없음"
    if len(candidates) == 1:
        c = candidates[0]
        if c["band"] and price is not None and in_band(price, c["band"]) is False:
            return None, f"수집기 확인 불가: 배점표 구간이 추정가격 {price:,}원과 안 맞음"
        return c, None
    if price is None:
        return None, "추정가격 없음 → 구간 선택 불가"
    hits = [c for c in candidates if c["band"] and in_band(price, c["band"]) is True]
    unsure = [c for c in candidates if c["band"] and in_band(price, c["band"]) is None]
    if len(hits) == 1 and not unsure:
        return hits[0], None
    if len(hits) > 1:
        return None, f"구간이 겹치는 배점표 {len(hits)}개"
    if unsure:
        return None, "고시금액이 문서에 없어 구간 판정 보류"
    return None, f"수집기 확인 불가: 배점표 {len(candidates)}개 중 추정가격 {price:,}원에 맞는 구간 없음"


# ------------------------------------------------------------------ 표 행 → 배점 항목
NUM = r"[+\-△]?\s*\d+(?:\.\d+)?"


def to_num(s):
    s = s.replace(" ", "").replace("△", "-").replace("점", "")
    return float(s)


def bracket_score(cell):
    m = re.search(r"[【\[]\s*(\d+(?:\.\d+)?)\s*[】\]]", cell)
    return float(m.group(1)) if m else None


def clean_name(cell):
    name = re.sub(r"【[^】]*】|\[\s*[+\-△]?\s*\d[^\]]*\]", "", cell)  # 【30】 【△3】 [ 30 ]
    name = re.sub(r"주\s*\d\)", "", name)
    name = re.sub(r"^\s*\d\)\s*", "", name.strip())
    name = re.split(r"A\.", name)[0]  # '2)산업재해 벌점 마일리지(한전 발주공사) A. 누계벌점 …' → 앞부분만
    name = re.sub(r"^\s*(?:\d+\.|[가-하]\.|[ⅠⅡⅢⅣ]\.?)\s*", "", name)
    return re.sub(r"\s+", "", name.replace("/", "")).strip() or None


def range_of(cell):
    """'△1～3' / '-5∼+0.1' / '+1.0 ~ △4.0' / '+1.0 / -2.4' → (min, max)"""
    nums = [to_num(x) for x in re.findall(NUM, cell)]
    if not nums:
        return None
    if re.search(r"[～~∼]", cell) and len(nums) == 2:
        return (min(nums), max(nums))  # '△1～3' → (-1, 3)
    # 등급별 점수 목록('-2.0 / -3.5 / -5.0')이나 단일 값은 해당 없음(0)까지 포함한 범위
    return (min(nums + [0]), max(nums + [0]))  # '+1.0 / -2.4' → (-2.4, 1)


def last_score(cells):
    """행의 마지막 배점 칸 → (값 문자열, 숫자 목록)"""
    for cell in reversed(cells):
        c = cell.strip()
        if not c or c.startswith("[별표") or c.startswith("주") or "참조" in c:
            continue
        nums = re.findall(NUM + r"\s*점?", c)
        if nums and re.fullmatch(r"[\s/점～~∼+\-△\d.()]+", c):
            return c, [to_num(n) for n in nums]
    return None, []


def classify(name):
    n = name or ""
    if "입찰가격" in n:
        return "PRICE"
    if re.search(r"경영|신용도|재무", n):
        return "MANAGEMENT"
    if re.search(r"사업소|숙지", n):
        return "OFFICE"
    if re.search(r"결격|미달", n):
        return "PENALTY"
    if re.search(r"(?<!평)가점|감점|신인도", n):
        return "BONUS"
    return "PERFORMANCE"


VALUE_CELL = re.compile(r"[\s+＋\-△~～∼/\d.점]*\d[\s+＋\-△~～∼/\d.점]*")


def parse_column_table(lines, table):
    """구간이 칸으로 나뉜 표 → 고른 칸의 수행능력 묶음(+분야)·입찰가격·결격 감점·합계.
    행마다 값 칸(숫자·범위)을 오른쪽에서 구간 수만큼 읽는다."""
    real = [i for i, b in enumerate(table["cols"]) if b]
    pick = real.index(table["col"])
    items, group = [], None
    for raw in lines[table.get("data_start", table["start"] + 2):table["end"]]:
        cells = split_cells(raw)
        values = [c for c in cells[1:] if VALUE_CELL.fullmatch(c)]
        if len(values) < len(real):
            continue
        cell = values[-len(real):][pick]
        name = compact(cells[0])
        num = to_num(cell) if re.fullmatch(r"\s*[+\-△]?\d+(?:\.\d+)?\s*점?\s*", cell) else None
        if re.match(r"^(합계|합)$", name) or (name == "계" and (group is None or items[-1]["kind"] == "PRICE")):
            items.append({"kind": "TOTAL", "score": num})
        elif name == "계" and group is not None:
            group["score"] = num  # 수행능력 소계
        elif "입찰가격" in name:
            items.append({"kind": "PRICE", "name": "입찰가격", "score": num, "quote": raw})
            group = None
        elif "결격" in name or "결격" in raw[:40]:
            items.append({"kind": "PENALTY", "name": clean_name(cells[0]) or "결격사유", "score": num,
                          "detail": cells[2] if len(cells) > 2 else None, "quote": raw})
        else:
            if "수행능력" in name:
                group = {"kind": "GROUP", "name": clean_name(cells[0]), "score": num, "quote": raw}
                items.append(group)
                if len(cells) < 2 or compact(cells[1]) in ("배점범위", "배점한도", "배점") or VALUE_CELL.fullmatch(cells[1]):
                    continue  # '당해용역 수행능력 | 배점범위 | 30 | 50 | 70' → 하위 분야 없음
                cells = cells[1:]
            if group is None:
                continue
            sub = clean_name(re.sub(r"^\d\.\s*", "", cells[0]))
            if not sub:
                continue
            if "신인도" in sub or re.search(r"[+＋△~～∼]", cell):
                items.append({"kind": "BONUS", "name": sub, "parent": group["name"],
                              "range": range_of(cell.replace("＋", "+")), "sub_items": None,
                              "detail": cells[1] if len(cells) > 1 else None, "quote": raw})
            else:
                items.append({"kind": classify(sub), "name": sub, "parent": group["name"], "score": num,
                              "detail": cells[1] if len(cells) > 1 else None, "score_cell": cell, "quote": raw})
    return items


def compact(s):
    return re.sub(r"[\s/▷]", "", s)


def split_cells(row):
    """'a | b | 5 |' → ['a', 'b', '5', ''] (끝의 빈 칸도 센다)"""
    return [c.strip() for c in re.split(r" \|(?= |$)", row)]


def parse_table(lines, table):
    """배점표 → 항목 목록 (dict). 계층은 'parent' 이름으로 표시.
    병합 칸 때문에 칸 수가 머리줄보다 적은 행은 왼쪽 칸(구분)이 위 행과 합쳐진 것으로 보고 앞을 빈 칸으로 채운다."""
    items, group = [], None
    header = split_cells(lines[table["start"] + 1])
    ncols = len(header)
    score_first = len(header) > 1 and SCORE_HEADER.search(header[1])  # '심사분야 | 배점 | 심사방법' 양식
    for raw in lines[table["start"] + 2:table["end"]]:
        cells = split_cells(raw)
        if len(cells) < ncols and not re.search(r"입찰가격|합계|^계", compact(cells[0])):
            cells = [""] * (ncols - len(cells)) + cells
        first = cells[0]
        key = compact(first)  # '입찰 / 가격' → '입찰가격'
        if re.match(r"^(합\s*계|계|소\s*계|합 계)", first.replace(" ", "")) or re.match(r"^합", first.replace(" ", "")):
            m = re.findall(r"\d+(?:\.\d+)?", " ".join(cells[1:]))
            if m and not first.replace(" ", "").startswith("소계"):
                items.append({"kind": "TOTAL", "score": float(m[-1])})
            continue
        if "수행능력" in key and "입찰가격" in key and len(cells) > 1:
            # 한 행에 두 분야: '▷당해용역 / 수행능력 / ▷입찰가격 | 30점 / 70점 | …'
            nums = [to_num(n) for n in re.findall(NUM, cells[1])]
            if len(nums) == 2:
                group = {"kind": "GROUP", "name": clean_name(first.split("입찰")[0].replace("▷", "")),
                         "score": nums[0], "quote": raw}
                items += [group, {"kind": "PRICE", "name": "입찰가격", "score": nums[1], "quote": raw}]
                continue
        if "입찰가격" in key:
            score = bracket_score(first)
            if score is None:
                _, nums = last_score(cells)
                score = nums[0] if nums else None
            items.append({"kind": "PRICE", "name": "입찰가격", "score": score, "quote": raw})
            group = None
            continue
        if "결격" in key and re.search(r"미달|결격", raw):
            val, nums = last_score(cells)
            items.append({"kind": "PENALTY", "name": clean_name(cells[1]) or "결격여부",
                          "detail": cells[2] if len(cells) > 2 else None,
                          "score": nums[0] if nums else None, "quote": raw})
            continue
        # 묶음 시작: 첫 칸에 【N】 (예: 당해공사 수행능력 【30】 | 시공경험 【18】 | …)
        top = bracket_score(first)
        if top is not None and len(cells) >= 2:
            group = {"kind": "GROUP", "name": clean_name(first), "score": top, "quote": raw}
            items.append(group)
            cells = cells[1:]
        elif "수행능력" in key and "결격" not in raw:
            # 용역 양식: '1. 수행능력 | 사업수행능력 | 심사방법 | 50점' / '설계용역 / 수행능력 | 20점 | …'
            score = None
            if score_first:
                nums = re.findall(NUM, cells[1])
                score = to_num(nums[0]) if len(nums) == 1 else None
            group = {"kind": "GROUP", "name": clean_name(first), "score": score, "quote": raw}
            items.append(group)
            cells = [] if score_first else cells[1:]  # score_first 양식의 나머지 칸은 심사방법 설명
        while cells and cells[0] == "":
            cells = cells[1:]  # 위 행과 병합된 칸
        if not cells or group is None:
            continue
        name_cell = cells[0]
        sub = bracket_score(name_cell)
        name = clean_name(name_cell)
        val, nums = last_score(cells[1:]) if len(cells) > 1 else (None, [])
        if not name or re.match(r"^(소계|합계|계)$", name):
            continue
        if not re.search(r"[가-힣]", name.replace("점", "")):
            # score_first 양식의 신인도 행: '+1.5∼△4점 | ○ 신인도 평가기준은 …'
            if len(cells) > 1 and "신인도" in cells[1]:
                rng = range_of(name_cell)
                items.append({"kind": "BONUS", "name": "신인도", "parent": group["name"], "range": rng,
                              "sub_items": None, "detail": cells[1], "quote": raw})
            continue
        if name_cell.count("○") > 1 and items and items[-1] is group:
            # 한 칸에 여러 분야: '○ 시공경험 / ○ 경영상태 / ○ 신 인 도' → 합친 배점 1개
            parts = [compact(p).strip("○") for p in name_cell.split("○") if compact(p).strip("○")]
            limit = re.search(r"(\d+(?:\.\d+)?)\s*점을?\s*한도", raw)  # 비고 '30점을 한도로 함'
            score = float(limit.group(1)) if limit else (nums[0] if len(nums) == 1 else None)
            items.append({"kind": "PERFORMANCE", "name": "·".join(parts), "parent": group["name"],
                          "score": score, "detail": cells[1] if len(cells) > 1 else None,
                          "score_cell": val, "quote": raw})
            continue
        if re.match(r"[○‘'']|[A-E]\.\s", name_cell) and items and items[-1].get("parent"):
            # 위 분야의 심사항목이 다음 줄로 이어진 행 (예: 시공경험 아래 '○ 지중송전 전기원 교육실적')
            items[-1]["detail"] = f"{items[-1].get('detail') or ''} / {name_cell}".strip(" /")
            continue
        kind = classify(name)
        signed = bool(val) and sub is None and re.search(r"[+△]|^\s*-", val)  # '+0.1' / '△1～3' / '-0.5 / -1.0'
        if kind == "BONUS" or signed or re.search(r"(?<!평)가점|감점|신인도", name_cell):
            # 이름 칸에 범위가 있으면 그 값 우선: '일반 신인도 / 【-1~+3 】'
            m = re.search(r"[【\[]\s*(" + NUM + r")\s*[～~∼]\s*(" + NUM + r")\s*[】\]]", name_cell)
            if m:
                name = clean_name(name_cell[:m.start()] + name_cell[m.end():])
                val = f"{m.group(1)}~{m.group(2)}"
            detail = cells[1] if len(cells) > 1 else ""
            # '신 / 인 / 도 | 건설 / 안전 | …' 다음 행 '일반 | -1 ∼ +3' → 신인도(건설안전), 신인도(일반)
            if detail and len(compact(detail)) <= 6 and not detail.startswith("○") and not re.search(r"\d", detail):
                name = f"{name}({compact(detail)})"
            elif len(name) <= 3 and items and items[-1]["kind"] == "BONUS":
                name = f"{items[-1]['name'].split('(')[0]}({name})"
            names = [x.strip() for x in detail.split(" / ") if x.strip()]
            scores = [x.strip() for x in (val or "").split(" / ") if x.strip()]
            sub_items = None
            # '○ 일반신인도 / ○ 건설안전신인도 …' ↔ '△1～3 / △5～+0.1 …' 처럼 항목마다 범위가 있을 때만 짝짓는다
            if len(names) > 1 and len(names) == len(scores) and \
                    all(re.match(r"[○◦ㅇ\-]", n) and re.search(r"[가-힣]", n) for n in names):
                sub_items = [{"name": re.sub(r"^[○◦ㅇ\-]\s*", "", n), "min": range_of(s)[0], "max": range_of(s)[1]}
                             for n, s in zip(names, scores) if range_of(s)]
            items.append({"kind": "BONUS", "name": name, "parent": group["name"],
                          "range": None if sub_items else (range_of(val) if val else None),
                          "sub_items": sub_items, "detail": detail or None, "quote": raw})
            continue
        # 묶음 한도 【N】 우선. 없으면 배점 칸 숫자 1개. '2 / 2 / 4' 처럼 여러 개면 선택형일 수 있어 비워 둔다
        score = sub if sub is not None else (nums[0] if len(nums) == 1 else None)
        detail = cells[1] if len(cells) > 1 else ""
        if score is None and len(nums) > 1 and detail.count("○") == len(nums) and not re.search(r"\d\.\s", detail):
            score = sum(nums)  # '○ 자기자본비율 / ○ 유동비율 | 3 / 2' → 둘 다 평가하므로 합 5 ('1. … 2. …' 선택형은 제외)
        items.append({"kind": kind, "name": name, "parent": group["name"], "score": score,
                      "detail": cells[1] if len(cells) > 1 else None, "score_cell": val, "quote": raw})
    return items


# ------------------------------------------------------------------ 입찰가격 산식
def normalize_formula(s):
    s = s.replace("–", "-").replace("−", "-").replace("TIMES", "×").replace("vert", "|").replace("｜", "|").replace("│", "|").replace("ㅣ", "|")
    s = re.sub(r"\[수식:\s*", "", s).replace("]", " ")
    s = re.sub(r"\{([^{}]*)\}\s*over\s*\{([^{}]*)\}", r"(\1)/(\2)", s)
    s = re.sub(r"\b(rm|LEFT|RIGHT|it)\b", " ", s)
    s = s.replace("`", " ").replace("~", " ").replace("{", "(").replace("}", ")").replace("*", "×")
    return re.sub(r"\s+", "", s)


PRICE_RE = re.compile(
    r"^(?:(?:평점(?:\(점\))?|평가산식|산식)=)?(\d+(?:\.\d+)?|배점한도)-(?:(\d+(?:\.\d+)?)×)?\|+\(*\(?(\d+(?:\.\d+)?)\)?/\(?100\)?\)?-\(*"
    r"(입찰가격(?:-A)?)\)?/\(*(예정가격(?:-A)?)(?:\)*×100\)*\|+|\)*\|+\)*×100)")
CAP_RE = re.compile(r"(?:100분의\s*|/100\s*|예정가격의\s*)(\d+(?:\.\d+)?)\s*(?:%\s*)?이상인?\s*경우(?:의|에는|에|는)?\s*평점은?\s*(\d+(?:\.\d+)?)\s*점")
# '예정가격의 96%이상인 경우에는 입찰가격이 예정가격의 96%인 경우의 점수로 평가' → 점수는 산식으로 계산
CAP_AT_RE = re.compile(r"예정가격의\s*(\d+(?:\.\d+)?)\s*%\s*이상인?\s*경우에는\s*입찰가격이\s*예정가격의\s*\1\s*%인\s*경우의\s*점수")
MIN_RE = re.compile(r"최저\s*평점은\s*(\d+(?:\.\d+)?)\s*점")
# PDF 줄바꿈 '소수점이 하의 … 다섯째자 리에서'도 허용
ROUND_RE = re.compile(r"소[수숫]점\s*(?:이\s*하\s*)?(첫|둘|셋|넷|다섯|여섯)\s*째\s*자\s*리\s*에\s*서\s*반\s*올\s*림")
PRICE_ROUND_RE = re.compile(r"예정가격(?:\s*-\s*A)?\s*\)?\s*으로\s*나눈[^.]{0,80}?" + ROUND_RE.pattern)  # 입찰가격 비율의 반올림만
ORDINAL = {"첫": 1, "둘": 2, "셋": 3, "넷": 4, "다섯": 5, "여섯": 6}
# '평점 =' / '평점(점) =' / '평가산식 =' / '○ 산식 =' / 수식 안의 '평점`=30'
FORMULA_START = re.compile(r"(?:평[\s`~]*점[\s`~]*(?:\(\s*점\s*\))?|평가\s*산식|산\s*식)[\s`~]*=")
TABLE_FORMULA_START = re.compile(r"(?:평가\s*산식\s*:?|평\s*점\s*(?:\(\s*점\s*\))?\s*=)\s*\n\[표\]")


TABLE_FORMULA_RE = re.compile(
    r"^(\d+(?:\.\d+)?) (?:(\d+(?:\.\d+)?) × )?(\d+(?:\.\d+)?) - (입찰가격(?:-A)?) × 100 100 (예정가격(?:-A)?)$")


def table_formula(text, s):
    """'평가산식 :' / '평점(점) =' 바로 아래 표로 그린 산식 (50 | | 2 × | ( | 90 | - | 입찰가격-A | )×100 / | 100 | 예정가격-A)"""
    m = TABLE_FORMULA_START.match(text, s)
    if not m:
        return None, None
    m = re.match(r"\[표\]\n(.*?)\n\[표 끝\]", text[m.end() - 3:m.end() + 400], re.S)
    if not m:
        return None, None
    flat = re.sub(r"\s+", " ", re.sub(r"[|\n()]", " ", m.group(1))).strip()
    flat = re.sub(r"(\d)\s*×", r"\1 ×", flat).replace("×100", "× 100")
    return TABLE_FORMULA_RE.match(flat), m.group(0)


def price_formulas(text):
    """문서 안 입찰가격 산식 전부 → [{pos, base, coef, ratio, subtract_a, cap, min_score, round_digits, quote}]
    '평점 =' / '평가산식 =' 마다 끊어서, 그 뒤 설명(최저평점·상한·반올림)을 다음 산식 전까지 본다 (최대 600자)."""
    out = []
    labeled = {m.start() for m in FORMULA_START.finditer(text)}
    bare = {m.start() for m in re.finditer(r"\[수식:[^\]]*입찰가격", text)}  # '* [수식: 80-20×…]'
    starts = sorted(labeled | bare | {m.start() for m in TABLE_FORMULA_START.finditer(text)})
    for i, s in enumerate(starts):
        # 산식 자체는 그 줄 끝까지, 설명(최저평점·상한·반올림)은 다음 산식 전까지(최대 800자) 본다
        line_end = text.find("\n", s)
        head = text[s:line_end if line_end >= 0 else len(text)]
        nxt = next((x for x in starts[i + 1:] if x >= s + len(head)), len(text))
        seg = text[s:min(nxt, s + 800)]
        m = PRICE_RE.match(normalize_formula(head))
        if not m:
            m, head = table_formula(text, s)
            if not m:
                continue
        out.append(formula_entry(text, s, seg, head, m.group(1), m.group(2), m.group(3), m.group(4).endswith("-A")))
    return out


def formula_entry(text, s, seg, head, base, coef, ratio, subtract_a):
    flat = re.sub(r"\s*(?:/|\n)\s*", " ", seg)  # 표 칸 안 줄바꿈 '100분의 / 92.5이상'
    cap, cap_at, mn = CAP_RE.search(flat), CAP_AT_RE.search(flat), MIN_RE.search(flat)
    # 반올림은 '예정가격으로 나눈 결과 … 반올림' 문장만 본다 (사업수행능력 '셋째자리 반올림'과 섞이지 않게).
    # 산식 바로 앞 설명에 있는 양식('※ 입찰가격 평점산식(…) … 다섯째자리에서 반올림')도 본다
    rd = PRICE_ROUND_RE.search(flat) or \
        PRICE_ROUND_RE.search(re.sub(r"\s*(?:/|\n)\s*", " ", text[max(0, s - 300):s]))
    if cap:
        cap = {"ratio": float(cap.group(1)), "score": float(cap.group(2))}
    elif cap_at:
        cap = {"ratio": float(cap_at.group(1)), "score": None}  # 그 비율일 때 산식 점수 (배점 확정 후 계산)
    return {
        "pos": s,
        "base": None if base == "배점한도" else float(base),  # None = 배점표의 입찰가격 배점
        "coef": float(coef or 1), "ratio": float(ratio), "subtract_a": subtract_a,
        "cap": cap,
        "min_score": float(mn.group(1)) if mn else None,
        "round_digits": ORDINAL[rd.group(1)] - 1 if rd else None,
        "quote": head.strip()[:300],
    }


def formula_text(f):
    bid, plan = ("(입찰가격-A)", "(예정가격-A)") if f["subtract_a"] else ("입찰가격", "예정가격")
    coef = "" if f["coef"] == 1 else f"{f['coef']:g}×"
    return f"{f['base']:g}-{coef}|({f['ratio']:g}/100-{bid}/{plan})×100|"


# ------------------------------------------------------------------ 통과점수
PASS_RES = (
    re.compile(r"종합\s*평점이?\s*(\d{2})\s*점\s*이상"),
    re.compile(r"(\d{2})\s*점\s*이상인?\s*(?:경우|자)[^\n]{0,30}낙찰"),
    re.compile(r"낙찰자\s*결정[^\n]{0,40}?(\d{2})\s*점\s*이상"),
    re.compile(r"평점\s*합계가?\s*(\d{2})\s*점\s*이상"),
)


# '10억원 미만인 용역은 95점' / '2. 100억원 미만 30억원 이상 용역의 경우 90점'
BAND_PASS = re.compile(r"([^,()\n]*?(?:이상|미만|이하|초과)[^,()\n]*?)\s*(?:인|의)?\s*(?:용역|공사)(?:은|는|의\s*경우)\s*(\d{2})\s*점")


def pass_score(text, price=None, gosi=None):
    """→ (점수, 근거 문구). '10억원 이상인 용역은 92점, 10억원 미만인 용역은 95점' 처럼 구간별이면 추정가격으로 고른다.
    구간 문구는 '낙찰자로 결정' 앞 또는 뒤(각호 목록) 300자 안에서 찾는다."""
    for m in re.finditer(r"낙찰자[로를]\s*결정", text):
        window = text[max(0, m.start() - 300):m.end() + 300]
        clauses = list(BAND_PASS.finditer(window))
        if price is not None and clauses:
            for c in clauses:
                band = parse_band(c.group(1), gosi)
                if band and in_band(price, band) is True:
                    return float(c.group(2)), c.group(0).strip()
    # '종합평점이 85점(다만, 소프트웨어용역의 …, 여객 육상운송용역은 88점)이상' → 문서가 그 용역의 기준이면 예외 점수
    m = re.search(r"종합\s*평점이?\s*(\d{2})\s*점\s*\(\s*다만\s*,([^)]*)\)", text)
    if m:
        whole = compact(text)
        for names, score in re.findall(r"([^()]*?)(?:은|는)\s*(\d{2})\s*점", m.group(2)):
            for name in names.split(","):
                key = compact(name)
                if key.endswith("용역") and key + "적격심사" in whole:
                    return float(score), m.group(0)
        return float(m.group(1)), m.group(0)
    for r in PASS_RES:
        m = r.search(text)
        if m:
            return float(m.group(1)), m.group(0)
    return None, None


# ------------------------------------------------------------------ 2단계: 경영상태 세부 표
# 재무비율 구간표: '① 최근년도 부채비율 / = [수식] / 【 7 】 | ◦ 기준치×0.5 미만 / ◦ 기준치×0.5이상～ 기준치×0.75미만 … | 7.0 / 6.2 …'
RATIO_NAMES = {"부채비율": ("debt_ratio", "부채총계/자기자본×100"), "유동비율": ("current_ratio", "유동자산/유동부채×100"),
               "자기자본비율": ("equity_ratio", "자기자본/총자산×100"), "영업기간": ("business_years", None)}
BASIS_BOUND = re.compile(r"기준치\s*×\s*(\d+(?:\.\d+)?)\s*(이상|이하|초과|미만)")
YEAR_BOUND = re.compile(r"(\d+(?:\.\d+)?)\s*년\s*(이상|이하|초과|미만)")


def grade_rows(grades, scores, bound_re):
    """'기준치×1.0이상～기준치×1.3미만' 목록 + 점수 목록 → [{min, min_inc, max, max_inc, score}]"""
    if len(grades) != len(scores):
        return None
    out = []
    for g, s in zip(grades, scores):
        row = {"min": None, "min_inc": None, "max": None, "max_inc": None, "score": s}
        bounds = bound_re.findall(g)
        if not bounds:
            return None
        for value, kind in bounds:
            if kind in ("이상", "초과"):
                row["min"], row["min_inc"] = float(value), kind == "이상"
            else:
                row["max"], row["max_inc"] = float(value), kind == "이하"
        out.append(row)
    return out


def ratio_table(lines, start, end, grade_col=0, grade_cols=1):
    """재무비율 구간표 1개 → [{name, max, rows, uses_basis, quote}] (못 읽는 표면 None).
    'A공사 | B공사'처럼 등급 칸이 여러 개면 grade_cols 개 중 grade_col 번째 칸을 읽는다."""
    items = []
    body = "\n".join(lines[start + 2:end])
    body = re.sub(r"\[수식:[^\]]*\]", lambda m: m.group(0).replace("\n", " "), body)  # 수식 안 줄바꿈으로 행이 끊김
    for raw in body.split("\n"):
        cells = split_cells(raw)
        if len(cells) < 3:
            continue
        name = next((n for n in RATIO_NAMES if n in compact(cells[0])), None)
        if not name:
            continue
        if len(cells) < 2 + grade_cols:
            continue
        grades = [g for g in cells[1 + grade_col].split(" / ") if g.strip()]
        scores = [to_num(x) for x in re.findall(NUM, cells[1 + grade_cols])]
        limit = bracket_score(cells[0])
        if limit is None and scores:
            limit = max(scores)  # '③ 영업기간 | … | 1.0 / 0.9 / 0.8' 처럼 한도 표시가 없으면 최고점
        uses_basis = "기준치" in cells[1 + grade_col]
        rows = grade_rows(grades, scores, BASIS_BOUND if uses_basis else YEAR_BOUND)
        if not rows:
            return None
        items.append({"name": name, "max": limit, "rows": rows, "uses_basis": uses_basis, "quote": raw[:300]})
    # '득점 = (심사항목 ①항+②항+③항) × 15/45' → 합계를 한도에 맞게 환산
    scale = re.search(r"득점\s*=.*?×\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", body)
    if items and scale:
        items[0]["scale"] = (float(scale.group(1)), float(scale.group(2)))
    return items or None


PCT_BOUND = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(이상|이하|초과|미만)")


def embedded_ratio(lines):
    """배점표 안에 들어간 기준비율 등급 (진단·기술용역 고시금액 미만):
    '‘최근년도자기자본비율 ’ / (자기자본/총자산)/기준비율 | 5' 다음 줄 'A. 100%이상 / … | (5) / (4.5) / …'
    → (항목들, 줄 위치, 문서에 적힌 기준비율)"""
    items, inline, at = [], {}, None
    for i, line in enumerate(lines[:-1]):
        cells = split_cells(line)
        k = next((j for j, c in enumerate(cells) if "기준비율" in c and any(n in compact(c) for n in RATIO_NAMES)), None)
        if k is None or not re.match(r"\s*A\.\s*\d", lines[i + 1]):
            continue
        name = next(n for n in RATIO_NAMES if n in compact(cells[k]))
        limit = next((to_num(c) for c in cells[k + 1:] if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", c)), None)
        grade_cells = split_cells(lines[i + 1])
        grades = [g for g in grade_cells[0].split(" / ") if g.strip()]
        scores = [to_num(x) for x in re.findall(r"\((\d+(?:\.\d+)?)\)", grade_cells[1] if len(grade_cells) > 1 else "")]
        rows = grade_rows(grades, scores, PCT_BOUND)
        if limit is None or not rows:
            return None, None, None
        for r in rows:  # 100% → 기준비율의 1.0배
            r["min"] = None if r["min"] is None else r["min"] / 100
            r["max"] = None if r["max"] is None else r["max"] / 100
        m = re.search(r"기준비율\s*:\s*(\d+(?:\.\d+)?)\s*%", cells[k])
        if m:
            inline[name] = float(m.group(1))
        items.append({"name": name, "max": limit, "rows": rows, "uses_basis": True, "quote": line[:300]})
        at = at if at is not None else i
    return items or None, at, inline


def credit_list_cell(lines):
    """한 칸에 이어 적힌 등급표 (폐기물 처리용역):
    '회사채ㆍ기업신용평가 / 기업어음 / 평 점 / AAA / - / 10.0 / AA+, AA0, AA- / A1 / 10.0 / …'"""
    for line in lines:
        for cell in split_cells(line):
            parts = [p.strip() for p in cell.split(" / ")]
            if len(parts) < 9 or "회사채" not in parts[0] or "기업어음" not in parts[1]:
                continue
            rest = parts[3:]
            if len(rest) % 3:
                continue
            rows = []
            for a, b, s in zip(rest[0::3], rest[1::3], rest[2::3]):
                if not re.fullmatch(r"\d+(?:\.\d+)?", s):
                    return None
                rows.append({"회사채": a, "기업어음": b, "기업신용평가": a, "score": float(s)})
            return rows
    return None


def credit_table(lines, start, end, price=None, gosi=None, special=False, limit=None):
    """신용평가등급표 → [{회사채, 기업어음, 기업신용평가, score}].
    점수 칸이 금액 구간별('추정가격 5억원이상 | 5억원미만') 또는 배점한도별('배점한도별' / '20점 | 10점')로
    나뉜 표는 맞는 칸을 읽는다. 한 칸에 여러 등급('AAA / AA+, AA0, AA- / A+')이 묶인 행은 등급마다 나눈다."""
    data_start, pick = start + 2, 0
    if start + 3 < end and "배점한도별" in compact(lines[start + 2]):
        heads = [to_num(c) for c in split_cells(lines[start + 3]) if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*점\s*", c)]
        if limit not in heads:
            return None
        data_start, pick = start + 4, heads.index(limit)
    elif start + 2 < end:
        bands = [b for b in (band_for(c, gosi, special) for c in split_cells(lines[start + 2])) if b]
        if len(bands) >= 2:
            fits = [i for i, b in enumerate(bands) if price is not None and in_band(price, b) is True]
            if len(fits) != 1:
                return None
            data_start, pick = start + 3, fits[0]
    out = []
    for raw in lines[data_start:end]:
        cells = split_cells(raw)
        nums = [c for c in cells[3:] if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", c)]
        if len(cells) < 4 or pick >= len(nums):
            continue
        score = float(nums[pick])
        a_list, b_list = cells[0].split(" / "), cells[1].split(" / ")
        if len(a_list) > 1 and len(a_list) == len(b_list):
            out += [{"회사채": a.strip(), "기업어음": b.strip(), "기업신용평가": a.strip(), "score": score}
                    for a, b in zip(a_list, b_list)]
        else:
            out.append({"회사채": cells[0], "기업어음": cells[1], "기업신용평가": cells[2], "score": score})
    return out or None


def basis_values(lines, start, limit=80):
    """구간표 뒤 가까운 기준치 표 → {'부채비율': 120.77, '유동비율': 142.57}"""
    for s, e in tables(lines):
        if s < start or s > start + limit:
            continue
        body = [split_cells(l) for l in lines[s + 1:e]]
        if not body:
            continue
        header = body[0]
        names = [next((n for n in RATIO_NAMES if n in compact(c)), None) for c in header]
        if not any(names):
            continue
        for row in body[1:]:
            values = [re.search(r"(\d+(?:\.\d+)?)\s*\)?\s*%", c) for c in row]
            if sum(1 for v in values if v) >= 1:
                found = {n: float(v.group(1)) for n, v in zip(names, values) if n and v}
                if found:
                    return found, "\n".join(lines[s:e + 1])[:300]
    return None, None


def section_band(lines, start, gosi, special):
    """표 위쪽에서 가장 가까운 금액 구간 제목 ('가. 추정가격 50억원 미만 10억원 이상인 공사')"""
    for j in range(start - 1, max(-1, start - 15), -1):
        if "추정가격" in lines[j]:
            b = band_for(lines[j], gosi, special)
            if b:
                return b
    return None


def pick_by_band(cands, lines, price, gosi, special):
    """같은 점수의 후보 표가 여러 개면 위쪽 금액 구간 제목으로 고른다. 내용이 같으면 첫 표."""
    if len({json.dumps(c[2], ensure_ascii=False, sort_keys=True) for c in cands}) <= 1:
        return cands[0] if cands else None
    fit = [c for c in cands if (b := section_band(lines, c[0], gosi, special)) and in_band(price, b) is True]
    return fit[0] if len(fit) == 1 or (fit and len({json.dumps(c[2], sort_keys=True) for c in fit}) == 1) else None


def management_details(lines, limit, price, gosi, special, wants_credit=True, wants_ratio=True):
    """경영상태 한도(예: 15) → (재무비율 항목들, 기준치, 신용등급표, 근거, 문제 목록)"""
    issues, ratio, credit, basis, quotes = [], None, None, None, {}
    ratio_cands, credit_cands, ab_table = [], [], False
    for s, e in tables(lines):
        body = "\n".join(lines[s + 1:e])
        if s + 2 < e and re.search(r"A공사.*B공사", compact(lines[s + 2])) and "기준치" in body:
            # 'A공사 | B공사' 칸으로 나뉜 구간표: 표 아래 주석 "'A'공사는 추정가격 50억원미만 10억원 이상인 공사에 적용"
            notes = re.sub(r"[‘’'\"]", "", "\n".join(lines[e:e + 25]))
            col = None
            for k, letter in enumerate("AB"):
                m = re.search(letter + r"\s*공사는\s*([^\n]+?)에\s*적용", notes)
                b = band_for(m.group(1), gosi, special) if m else None
                if b and price is not None and in_band(price, b) is True:
                    col = k
            items = ratio_table(lines, s, e, col, 2) if col is not None else None
            if items and abs(sum(i["max"] for i in items) - limit) < 1e-6:
                ratio_cands.append((s, e, items))
            else:
                ab_table = True
            continue
        if "기준치" in body and re.search(r"부채비율|유동비율|자기자본비율", compact(body)):
            items = ratio_table(lines, s, e)
            total = sum(i["max"] for i in items) if items else None
            scale = items[0].get("scale") if items else None
            if items and (abs(total - limit) < 1e-6 or (scale and abs(scale[1] - total) < 1e-6
                                                       and abs(scale[0] - limit) < 1e-6)):
                ratio_cands.append((s, e, items))
        if "신용평가등급" in compact(lines[s + 1]):
            rows = credit_table(lines, s, e, price, gosi, special, limit)
            if rows and abs(max(r["score"] for r in rows) - limit) < 1e-6:
                credit_cands.append((s, e, rows))
    picked = pick_by_band(ratio_cands, lines, price, gosi, special)
    if picked:
        s, e, ratio = picked
        if any(i["uses_basis"] for i in ratio):
            basis, quotes["basis"] = basis_values(lines, e)
            missing = [i["name"] for i in ratio if i["uses_basis"] and (not basis or i["name"] not in basis)]
            if missing:
                issues.append(f"경영상태 기준치 못 찾음({', '.join(missing)})")
        credit_after = [c for c in credit_cands if c[0] > s]
        credit_cands = credit_after[:1] or credit_cands
    elif ratio_cands:
        issues.append("경영상태 재무비율 구간표가 금액 구간별로 여러 개라 못 고름")
    else:
        items, at, inline = embedded_ratio(lines)
        if items and abs(sum(i["max"] for i in items) - limit) < 1e-6:
            ratio = items
            basis, quotes["basis"] = (inline, None) if len(inline) == len(items) else basis_values(lines, at)
            missing = [i["name"] for i in ratio if not basis or i["name"] not in basis]
            if missing:  # 예: '기준비율은 … 입찰공고 시 별도 명시한다'인데 공고문에도 값이 없음
                issues.append(f"경영상태 기준비율 값이 문서에 없음({', '.join(missing)})")
        elif ab_table:
            issues.append("경영상태 구간표가 A공사/B공사로 나뉘는데 구분 기준이 문서에 없음")
        elif wants_ratio:
            issues.append("경영상태 재무비율 구간표 못 찾음")
    c = pick_by_band(credit_cands, lines, price, gosi, special)
    if c:
        credit = c[2]
        quotes["credit"] = "\n".join(lines[c[0]:c[0] + 4])[:300]
    else:
        listed = credit_list_cell(lines)
        if listed and abs(max(r["score"] for r in listed) - limit) < 1e-6:
            credit = listed
        elif wants_credit:
            issues.append("경영상태 신용평가등급표 못 찾음")
    return ratio, basis, credit, quotes, issues


# ------------------------------------------------------------------ 2단계: 시공경험 산식
EXP_HEAD = re.compile(r"시공\s*경험\s*\(\s*(?:최대\s*)?(\d+(?:\.\d+)?)\s*점")
SUB_HEAD = re.compile(r"^\s*(?:[①-⑩]|[가-하]\.|\d\))\s*(.+?)\s*\(\s*(?:평점\s*상한은?\s*|가산점\s*상한은?\s*)?(\d+(?:\.\d+)?)\s*점")
FRACTION = re.compile(r"\{([^{}]*실적\s*(?:누계액|합계|누계점수)[^{}]*)\}\s*over\s*\{([^{}]*)\}")  # 금액 또는 규모(C-km)
AMOUNT_TEXT = re.compile(r"(사정액|예비가격\s*기초금액)[^\n:：]{0,30}[:：]\s*￦?\s*([\d,]{6,})")


def clean_label(s):
    return re.sub(r"\s+", " ", re.sub(r"[~`]|\brm\b|LEFT|RIGHT|left|right|\(|\)", " ", s)).strip()


def experience_section(lines, limit, price, gosi, special, table_start=0):
    """[별표 2]의 '시공경험(18점)' 절 (다음 '경영상태' 절 전까지) → (시작, 끝) 또는 None.
    송전 양식처럼 배점표 바로 아래 '주1) 시공경험 평가방법'에 있으면 그 절."""
    cands = []
    for i, line in enumerate(lines):
        m = EXP_HEAD.search(line)
        if not m or abs(float(m.group(1)) - limit) > 1e-6:
            continue
        end = next((j for j in range(i + 1, min(len(lines), i + 200))
                    if re.search(r"경영\s*상태\s*\(|^\s*(?:\d+|[가-하])\.\s*경영\s*상태", lines[j])), min(len(lines), i + 120))
        cands.append((i, end, "\n".join(lines[i:end])))
    picked = pick_by_band(cands, lines, price, gosi, special)
    if picked:
        return picked[:2]
    for i in range(table_start, min(len(lines), table_start + 80)):
        if re.search(r"시공\s*경험\s*평가\s*방법", lines[i]):
            end = next((j for j in range(i + 1, min(len(lines), i + 120))
                        if re.match(r"\s*주\s*\d\)", lines[j]) or "경영상태" in lines[j][:20]), min(len(lines), i + 80))
            return i, end
    return None


def experience_items(lines, start, end, limit):
    """시공경험 절 → [{name, cap, formula, params, quote, bonus}]"""
    items, heading = [], None
    sec = lines[start:end]
    text = "\n".join(sec)
    amounts = {re.sub(r"\s+", "", m.group(1)): int(m.group(2).replace(",", "")) for m in AMOUNT_TEXT.finditer(text)}
    rd = ROUND_RE.search(text)
    for k, line in enumerate(sec):
        h = SUB_HEAD.match(line)
        if h:
            name = clean_label(h.group(1))
            name = name.split("대비 ")[-1] if "대비 " in name else name  # '…사정액 대비 최근 5년간 해당업종 실적누계액 비율'
            heading = {"name": name, "cap": float(h.group(2)), "bonus": "가산점" in line}
        f = FRACTION.search(line.replace("~", " ").replace("`", " "))
        if f:
            num_raw, den = f.group(1), f.group(2)
            mult = re.search(r"(?:×|TIMES)\s*(\d+(?:\.\d+)?)\s*(?:/\s*(\d+))?", den)
            multiplier = (float(mult.group(1)) / float(mult.group(2) or 1)) if mult else 1.0
            num_mult = re.search(r"(?:×|TIMES)\s*(\d+(?:\.\d+)?)", num_raw)  # '실적누계액 TIMES 2' = 분모 × 0.5
            if num_mult:
                multiplier /= float(num_mult.group(1))
            num = clean_label(re.sub(r"(?:×|TIMES).*$", "", num_raw))
            den_label = clean_label(re.sub(r"(?:×|TIMES).*$", "", den))
            after = line.replace("~", " ").replace("`", " ")[f.end():]
            pts = re.search(r"(?:×|TIMES)\s*(\d+(?:\.\d+)?)\s*(?:\(?\s*점)?", after)
            points = float(pts.group(1)) if pts else None  # '× 배점' 이면 제목의 점수
            look = "\n".join(sec[max(0, k - 4):k + 1])
            caps = list(re.finditer(r"(?:평점\s*)?상한은?\s*(\d+(?:\.\d+)?)\s*점", look))
            cap = caps[-1] if caps else None  # 산식에 가장 가까운 상한 문구
            name = heading["name"] if heading else num
            cap_value = float(cap.group(1)) if cap else (heading["cap"] if heading else points)
            points = points if points is not None else cap_value
            basis = "예비가격기초금액" if "예비가격" in den_label else "사정액" if "사정액" in den_label else den_label
            items.append({"name": name, "cap": cap_value, "bonus": bool(heading and heading["bonus"]),
                          "formula": f"min({num} / ({basis} × {multiplier:g}) × {points:g}, {cap_value:g})",
                          "params": {"numerator": num, "denominator": basis, "multiplier": multiplier,
                                     "points": points, "cap": cap_value,
                                     "base_amount": amounts.get(re.sub(r"\s+", "", basis))},
                          "quote": line.strip()[:300]})
            heading = None
            continue
        per = re.search(r"1\s*인당\s*(\d+(?:\.\d+)?)\s*점", line)
        if per and heading and not any(i["name"] == heading["name"] for i in items):
            items.append({"name": heading["name"], "cap": heading["cap"], "bonus": False,
                          "formula": f"min(인원 × {float(per.group(1)):g}, {heading['cap']:g})",
                          "params": {"per_unit": float(per.group(1)), "unit": "인", "cap": heading["cap"]},
                          "quote": line.strip()[:300]})
    items += other_experience_items(sec, items)
    return items, (ORDINAL[rd.group(1)] - 1 if rd else None), amounts


OTHER_HEAD = re.compile(r"^\s*(\d\.|[가-하]\.|[①-⑩])\s*(.+?)\s*\(\s*(\+)?\s*(\d+(?:\.\d+)?)\s*점\s*\)")


def other_experience_items(sec, found):
    """실적 비율 산식이 아닌 시공경험 항목 (송전 필수장비·배치기술자·기능인력, 표 없는 사업소 숙지도 등).
    계산 방법을 못 읽은 항목도 배점은 남기고 rule_type='GROUP', review=True 로 둔다."""
    out, skip_style = [], None
    known = [compact(i["name"]) for i in found]
    for k, line in enumerate(sec[1:], 1):
        h = OTHER_HEAD.match(line)
        if not h:
            continue
        style = re.sub(r"[0-9]", "0", re.sub(r"[가-하]", "가", re.sub(r"[①-⑩]", "①", h.group(1))))
        if skip_style and style != skip_style:
            continue  # 가점 항목의 하위 세목 ('4. 기능인력 (+1.0점)' 아래 '가. … (+0.2점)')
        skip_style = None
        name, plus, cap = clean_label(h.group(2)), h.group(3), float(h.group(4))
        key = compact(name)
        if any(key in n or n in key for n in known) or "실적누계액" in key:
            continue
        near = "\n".join(sec[k:k + 5])
        base = {"name": name, "cap": cap, "bonus": bool(plus), "quote": line.strip()[:300], "formula": None}
        pairs = re.findall(r"([가-힣A-Za-z]+)\s*\(\s*(\d+(?:\.\d+)?)\s*점\s*\)", line[h.end():])
        if plus:
            out.append({**base, "rule_type": "GROUP", "params": {"min": 0, "max": cap, "설명": sec[k + 1].strip()[:200]
                                                                   if k + 1 < len(sec) else None}, "review": True})
            skip_style = style
        elif len(pairs) >= 2:  # '필수장비 보유여부 (3점) : 엔진풀러 (1.5점), 텐셔너 (1.5점)'
            out.append({**base, "rule_type": "FIXED", "input_key": "equipment_owned",
                        "params": {"items": [{"name": n, "score": float(v)} for n, v in pairs]}})
        elif re.search(r"365\s*(?:TIMES|×)\s*(\d+)\s*년", near):  # '(배점) × A / (365 × 3년)'
            years = int(re.search(r"365\s*(?:TIMES|×)\s*(\d+)\s*년", near).group(1))
            out.append({**base, "rule_type": "FORMULA", "input_key": "engineer_days",
                        "formula": f"min({cap:g} × 경력일수 / (365 × {years}), {cap:g})",
                        "params": {"A": "동종공사 시공참여 경력(근무일수)", "days": 365 * years, "cap": cap}})
        elif "숙지도" in name:  # 표 없이 '광역시·도에 본점이 있으면 배점'
            out.append({**base, "rule_type": "FIXED", "input_key": "office_location", "category": "OFFICE",
                        "params": {"score": cap, "condition": "공사현장이 속한 광역시·특별시·특별자치도·도에 본점 소재"}})
        else:
            out.append({**base, "rule_type": "GROUP", "params": {"설명": sec[k + 1].strip()[:200] if k + 1 < len(sec)
                                                                  else None}, "review": True})
        known.append(key)
    return out


def office_table(lines, start, end):
    """사업소 숙지도 표 ('본점 소재지 | 해당 사업소 | 인접 사업소 | 비인접 사업소' / '평점 | 2 | 1 | 0')
    + 해당·인접 사업소 이름 → {name, cap, rows, office, adjacent, quote}"""
    for s, e in tables(lines):
        if s < start or s > end or e - s < 3:
            continue
        head, vals = split_cells(lines[s + 1]), split_cells(lines[s + 2])
        if not any("해당" in compact(c) and "사업소" in c for c in head):
            continue
        levels = [compact(c) for c in head[1:]]
        scores = [to_num(v) for v in vals[1:] if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", v)]
        if len(levels) != len(scores):
            continue
        after = "\n".join(lines[e:min(end, e + 12)])
        office = (re.search(r"해당\s*공사현장이\s*속한\s*한전\s*사업소\s*\(([^)]+)\)", after)
                  or re.search(r"해당\s*공사현장이\s*속한\s*한전\s*([가-힣]+(?:지사|본부|사업소))", after))
        adjacent = re.search(r"인접한\s*사업소\s*\(([^)]+)\)", after)
        heading = next((l for l in reversed(lines[max(start, s - 25):s]) if re.search(r"숙지도.*\d+\s*점", l)), "")
        cap = re.search(r"(\d+(?:\.\d+)?)\s*점", heading)
        return {"name": "사업소 숙지도", "cap": float(cap.group(1)) if cap else max(scores),
                "rows": [{"level": l, "score": sc} for l, sc in zip(levels, scores)],
                "office": office.group(1).strip() if office else None,
                "adjacent": [x.strip() for x in adjacent.group(1).split(",")] if adjacent else None,
                "outside_region_score": 0 if re.search(r"소재하지\s*않는\s*경우\s*0\s*점", after) else None,
                "quote": "\n".join(lines[s:e + 1])[:300]}
    return None


# ------------------------------------------------------------------ 2단계: 신인도·가감점 세부 표
SIGNED = r"[+\-△]\s*\d+(?:\.\d+)?|(?<![\d.])0(?:\.0+)?(?![\d.])"
BAND_HEAD = re.compile(r"^\s*(?:[가-하]\.|[ⅠⅡⅢⅣ]\.?|\d\.)\s*\[?\s*추정가격")


def bonus_table_fits(lines, s, price, gosi, special):
    """신인도 표가 이 공고에 해당하는지: 서식·별지 안의 표는 빼고,
    표 위쪽 가장 가까운 금액 구간 제목('가. 추정가격 50억원 미만 10억원 이상인 공사')이 추정가격에 맞아야 한다."""
    for j in range(s - 1, -1, -1):
        line = lines[j]
        if line.startswith("[서식") or line.startswith("[별지"):
            return False
        if line.startswith("[별표"):
            break
    # 금액 구간이 'Ⅰ. 추정가격 …'처럼 문서 최상위로 나뉜 양식(송전·변전)이 아니면, 다른 [별표]의 구간 제목은 보지 않는다
    # (용역·감리: [별표 1] 구간별 배점표 뒤의 [별표 2] 신인도·[별표 11] 평가표는 모든 구간에 공통)
    roman_doc = any(re.match(r"\s*[ⅠⅡⅢⅣ]\.?\s*추정가격", l) for l in lines)
    section_seen = False  # '2. 신인도'처럼 구간 하위('가.','나.')를 끝내는 상위 절 제목을 지났는지
    for j in range(s - 1, -1, -1):
        line = lines[j]
        if not roman_doc and line.startswith("[별표"):
            return True
        if re.match(r"\s*\d+\.\s*(?:신인도|가점|감점)", line):
            section_seen = True
        if BAND_HEAD.match(line):
            if section_seen and not re.match(r"\s*[ⅠⅡⅢⅣ]", line):
                continue  # 송전: 경영상태의 '가./나.' 금액 구간 뒤에 공통 '2. 신인도' → 상위 'Ⅰ.' 구간으로 판단
            b = band_for(line, gosi, special)
            return not b or price is None or in_band(price, b) is not False
    return True


def bonus_tables(lines, price, gosi, special):
    """신인도 표 → [{group, name, condition, rows:[{grade, score}], scale, quote}]
    표 안 소제목 행('1. 건설안전 신인도(배점 : -5∼+0.1)', '2. 일반신인도 [배점 : ±1, 아래 점수 × 1/3]')으로 묶음을 나눈다."""
    out = []
    for s, e in tables(lines):
        if not bonus_table_fits(lines, s, price, gosi, special):
            continue
        head = lines[s + 1]
        if re.search(r"세부\s*항목", head) and re.search(r"배\s*점", head):
            out += detail_item_table(lines, s, e)  # 용역 '심사항목 | 세부항목 | 배점 | 비고'
            continue
        if "세부평가방법" in compact(head):
            out += method_table(lines, s, e)  # 감리 '평가항목 | 평가요소 | 세부평가방법' 의 가점·감점 행
            continue
        if not (re.search(r"등\s*급", head) and re.search(r"평\s*점|배\s*점", head)) or "신용" in head:
            continue
        group = next((l.strip() for l in reversed(lines[max(0, s - 4):s]) if re.search(r"신인도|가점|감점", l)), "")
        name = None
        for raw in lines[s + 2:e]:
            cells = split_cells(raw)
            if len(cells) == 1:
                if re.search(r"신인도|가점|감점|배점", raw):
                    group = raw.strip()
                continue
            score_cell, grade_cell = cells[-1], cells[-2] if len(cells) >= 2 else ""
            scores = [to_num(x) for x in re.findall(SIGNED, score_cell)]
            if not scores:
                continue
            grades = [g.strip() for g in grade_cell.split(" / ") if g.strip()]
            if len(grades) != len(scores):
                grades = [grade_cell.strip()] if len(scores) == 1 else None
            if not grades:
                continue
            condition = cells[-3] if len(cells) >= 3 else ""
            if re.fullmatch(r"\s*[+\-△]?\s*\d+(?:\.\d+)?\s*", condition) and len(cells) >= 4:
                condition = cells[-4]  # '평가요소 | 배점 | 등급 | 평점' 양식의 배점 칸 건너뜀
            if len(cells) >= 4 or (len(cells) == 3 and not condition):
                name = clean_label(re.sub(r"\(.*?\)", "", cells[0].replace(" / ", " "))) or name
            sub = re.match(r"\s*\d\)\s*([^/]+)", condition)
            item = clean_label(sub.group(1))[:40] if sub and len(sub.group(1)) < 40 else (name or "")
            scale = re.search(r"×\s*(\d+)\s*/\s*(\d+)", group)
            out.append({"group": group, "name": item or name or "신인도", "condition": condition[:300],
                        "rows": [{"grade": g, "score": sc} for g, sc in zip(grades, scores)],
                        "scale": [int(scale.group(1)), int(scale.group(2))] if scale else None, "quote": raw[:300]})
    return out


def detail_item_table(lines, s, e):
    """'가. 유사실적 | A. … | 2.0 | 비고' / 'B. … | 0.2 |' (병합 행) → 심사항목별 [{name, rows:[{grade, score}]}]"""
    ncols = len(split_cells(lines[s + 1]))
    groups, name = {}, None
    for raw in lines[s + 2:e]:
        cells = split_cells(raw)
        if len(cells) >= ncols:
            name = clean_label(cells[0].replace(" / ", " ")) or name
            grade, score_cell = cells[1], cells[2]
        elif len(cells) >= 2:
            grade, score_cell = cells[0], cells[1]
        else:
            continue
        nums = re.findall(SIGNED + r"|\d+(?:\.\d+)?", score_cell)
        if not nums or not name:
            continue
        groups.setdefault(name, []).append({"grade": grade.split(" / ")[0].strip()[:200], "score": to_num(nums[0])})
    return [{"group": "신인도", "name": n, "condition": "", "rows": r, "scale": None,
             "quote": "\n".join(lines[s:s + 3])[:300]} for n, r in groups.items()]


def method_table(lines, s, e):
    """감리 PQ 표의 '마. 가점 감점' 행들 → 항목별 규칙 (건당·기간당 점수, 최대 점수).
    예: '(6)부정당 업체 | … 합산기간 1월마다 0.1점씩 감점하되 최대 0.8점까지 감점'"""
    out, inside = [], False
    for raw in lines[s + 2:e]:
        cells = split_cells(raw)
        if len(cells) >= 3:
            inside = bool(re.search(r"가점|감점", compact(cells[0])))
            name_cell, text = cells[1], cells[2]
        elif len(cells) == 2 and inside:
            name_cell, text = cells[0], cells[1]
        else:
            continue
        if not inside:
            continue
        name = clean_label(re.sub(r"^\(\d\)\s*", "", name_cell.replace(" / ", "")))
        minus = "감점" in text and "가점" not in text
        caps = re.findall(r"최대\s*(\d+(?:\.\d+)?)\s*점", text)
        per = re.search(r"(\d+(?:\.\d+)?)\s*점씩", text)
        cap = sum(float(c) for c in caps) if caps else None
        if len(caps) > 1:
            per = None  # 업체·감리원 등 규칙이 여러 개 섞인 행 → 원문 확인
        band_caps = None
        if cap is None:  # '최대 N점' 문장이 없고 점수가 표 안에만 있는 항목 (부실벌점·자격보유)
            cols = re.findall(r"(?:초과|이하|미만|이상)\s*/\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", text)
            if cols and re.search(r"4천만원\s*미만", text):  # 용역금액 구간 3열 (4천만원 미만 → 1억원~고시금액)
                band_caps = [max(float(c[i]) for c in cols) for i in range(3)]
            elif not re.search(r"\d천만원\s*미만", text):  # 다른 금액 구간표(예: 3천만원)는 구간을 추측하지 않고 REVIEW 로 둔다
                # 자격증별 점수 표 ('기술사 / 0.2점 / …'). '25점 초과'처럼 구간 경계에 붙은 숫자는 점수가 아님
                pts = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*점(?!\s*(?:초과|이하|미만|이상))", text)]
                cap = max(pts) if pts else None
        rule = {"per_unit": float(per.group(1)) if per else None, "sign": -1 if minus else 1,
                "설명": text[:300]}
        if band_caps:
            rule["band_caps"] = band_caps
        out.append({"group": "가점/감점", "name": name or "가점/감점", "condition": text[:300],
                    "rows": [{"grade": "최대", "score": (-cap if minus else cap) if cap is not None else 0.0}],
                    "rule": rule, "scale": None, "quote": raw[:300]})
    return out


GRADE_BOUND = re.compile(r"(\d+(?:\.\d+)?)\s*(회|명|건|점|년|개월)\s*(이상|초과|미만|이하)?")
GRADE_NOT_BOUND = re.compile(r"최근\s*\d+\s*(?:년|개월)|\d+\s*(?:년|개월)\s*(?:간|이내|평균|동안)|\d+\s*개월\s*평균")


def grade_bounds(grade):
    """등급 문구의 숫자 조건 → (min, min_inc, max, max_inc, unit) 또는 None.
    '2회 받은 자' → 2~2 / '3회 이상' → 3~ / '누계 벌점 0.5점 이상 1점 미만' → 0.5~1 / '사망자 1명' → 1~1.
    '최근 1년동안', '6개월 평균' 같은 기간 설명은 조건으로 보지 않는다."""
    text = GRADE_NOT_BOUND.sub(" ", grade)
    found = GRADE_BOUND.findall(text)
    if not found or len({u for _, u, _ in found}) > 1:
        return None
    unit = found[0][1]
    lo = hi = None
    lo_inc = hi_inc = True
    for value, _, kind in found:
        v = float(value)
        if kind in ("이상", "초과"):
            lo, lo_inc = v, kind == "이상"
        elif kind in ("미만", "이하"):
            hi, hi_inc = v, kind == "이하"
        elif len(found) == 1:
            lo = hi = v  # '2회 받은 자', '사망자 1명'
    if lo is None and hi is None:
        return None
    return lo, lo_inc, hi, hi_inc, unit


def add_grade_bounds(rows):
    """등급표 rows 에 숫자 범위(min·max·unit)를 덧붙인다 (원문 grade·score 는 그대로).
    '과징금 처분을 받은 사실이 있는 자 / 2회 / 3회 이상'처럼 첫 등급에만 숫자가 없고 다음 등급이 정확히 2회면
    첫 등급을 1회로 본다 (inferred=True)."""
    bounds = [grade_bounds(r["grade"]) for r in rows]
    nxt = bounds[1] if len(rows) >= 2 else None
    if bounds[0] is None and nxt and nxt[0] == 2 and nxt[4] in ("회", "명", "건"):
        # 다음 등급이 '2회' 또는 '2회 이상' → 첫 등급('처분을 받은 사실이 있는 자')은 1회 (팀 결정 2026-11-22)
        bounds[0] = (1.0, True, 1.0, True, nxt[4])
        rows[0]["inferred"] = True
    for r, b in zip(rows, bounds):
        if b:
            r.update(min=b[0], min_inc=b[1] if b[0] is not None else None, max=b[2],
                     max_inc=b[3] if b[2] is not None else None, unit=b[4])
    return rows


def grade_input(rows):
    """등급표를 화면에서 어떻게 받을지 → {input_type, unit}
    count : 모든 등급에 숫자 범위가 있음 → 숫자(횟수·인원·벌점 등) 입력, 점수는 범위로 고름
    check : 숫자 없는 등급 1개 → 해당/비해당
    select: 숫자 없는 등급 여러 개(또는 숫자 있는 등급과 섞임) → 등급 하나 고르기 (해당 없음 포함)"""
    units = {r.get("unit") for r in rows}
    if all(r.get("unit") for r in rows) and len(units) == 1:
        return {"input_type": "count", "unit": units.pop()}
    if len(rows) == 1:
        return {"input_type": "check", "unit": None}
    return {"input_type": "select", "unit": None}


def bonus_parent(rows, group):
    """신인도 세부 행을 붙일 1단계 가감점 묶음 행 번호 (건설안전/일반 이름으로 맞추고, 없으면 첫 가감점 묶음)"""
    cands = [i for i, r in enumerate(rows) if r["category"] == "BONUS" and r["rule_type"] == "GROUP"
             and r.get("source_location", "").startswith("[별표 1]")]
    key = "건설안전" if "건설안전" in compact(group) else "일반" if "일반" in compact(group) else None
    if key:
        hit = [i for i in cands if key in compact(rows[i]["item_name"])]
        if hit:
            return hit[0]
    return cands[0] if cands else None


# ------------------------------------------------------------------ 공고 1건
def is_special(db, notice_no, title):
    names = " ".join(r[0] or "" for r in db.execute(
        "SELECT name FROM bid_restriction_item WHERE notice_no=? AND category='LICENSE'", (notice_no,)))
    return any(t in names for t in SPECIAL_TRADES) or any(t in (title or "") for t in ("전기공사",))


# ------------------------------------------------------------------ 2단계: 감리 PQ 세부 표 ([별표 12] 고시금액 미만)
# 표 안 칸이 ' / ' 로 이어진 한 줄로 읽힌다: '용역금액 / 5년이상 / 5년미만 / 4년이상 / … / 1억원이상 / 고시금액미만 / 4.80점 / …'
# 용역금액 구간(4천만원 미만 / 4천만원~1억원 / 1억원~고시금액)마다 점수가 한 줄씩 있다.
PQ_BANDS = (("4천만원 미만", r"4천만원\s*미만"), ("4천만원 이상 1억원 미만", r"4천만원\s*이상\s*(?:/\s*)?1억원\s*미만"),
            ("1억원 이상 고시금액 미만", r"1억원\s*이상\s*(?:/\s*)?고시금액\s*미만"))
PQ_NUM = r"(\d+(?:\.\d+)?)"
PQ_SCORE_RE = re.compile(r"평점\s*=\s*\(\s*평가점수\s*/\s*100\s*\)\s*×\s*(\d+(?:\.\d+)?)\s*점")


def pq_band(price):
    """추정가격 → PQ_BANDS 번호 (없으면 None)"""
    if price is None:
        return None
    return 0 if price < 40_000_000 else 1 if price < 100_000_000 else 2


def _band_marks(seg):
    return sorted((m.start(), m.end(), i) for i, (_, pat) in enumerate(PQ_BANDS) for m in re.finditer(pat, seg))


def pq_band_scores(seg, band, k):
    """seg 안에서 band 구간 이름 뒤 숫자 k개 (구간 이름 순서가 표마다 달라 이름으로 찾는다)"""
    marks = _band_marks(seg)
    for n, (s, e, i) in enumerate(marks):
        if i == band:
            nxt = marks[n + 1][0] if n + 1 < len(marks) else len(seg)
            nums = [float(x) for x in re.findall(PQ_NUM, seg[e:nxt])][:k]
            return nums if len(nums) == k else None
    return None


def pq_bins(header, unit):
    """['5년이상','5년미만','4년이상',…,'2년미만'] → 구간 행 (단위가 % 면 배수로: 75% → 0.75)"""
    vals = []
    for t in header:
        m = re.fullmatch(rf"{PQ_NUM}{unit}(이상|미만)", t.replace(" ", ""))
        if not m:
            return None
        vals.append((float(m.group(1)) / (100 if unit == "%" else 1), m.group(2)))
    mid = vals[1:-1]
    if len(vals) < 2 or vals[0][1] != "이상" or vals[-1][1] != "미만" or len(mid) % 2:
        return None
    rows = [{"min": vals[0][0], "min_inc": True, "max": None, "max_inc": None, "label": header[0]}]
    for j in range(0, len(mid), 2):
        (hi, a), (lo, b) = mid[j], mid[j + 1]
        if (a, b) != ("미만", "이상"):
            return None
        rows.append({"min": lo, "min_inc": True, "max": hi, "max_inc": False,
                     "label": f"{header[j + 2]} {header[j + 1]}"})
    rows.append({"min": None, "min_inc": None, "max": vals[-1][0], "max_inc": False, "label": header[-1]})
    return rows


def pq_amount_table(seg, band, unit):
    """'용역금액 / 5년이상 / … / 1억원이상 / 고시금액미만 / 4.80점 / …' → 구간 행(점수 포함) 또는 None"""
    i = seg.find("용역금액 /")
    if i < 0:
        return None
    rest = seg[i + len("용역금액 /"):]
    marks = _band_marks(rest)
    if not marks:
        return None
    header = [t.strip() for t in rest[:marks[0][0]].split(" / ") if t.strip()]
    rows = pq_bins(header, unit)
    scores = pq_band_scores(rest, band, len(rows)) if rows else None
    if not scores:
        return None
    for r, sc in zip(rows, scores):
        r["score"] = sc
    return rows


def pq_details(text, price, srm_amount=None):
    """감리 PQ 세부평가기준 → ({'참여감리원': [항목…], '유사용역수행실적': […], '신용도': […], '업무중첩도': […]}, 문제 목록).
    항목 = dict(name, rule_type, max, input_key, parameters, quote). 점수는 추정가격이 들어가는 용역금액 구간 값."""
    band = pq_band(price)
    if band is None:
        return {}, ["감리 PQ: 추정가격 없음"]
    head = re.search(r"\[별표\s*\d+\]\s*\n[^\n]*사업수행능력\s*세부평가기준[^\n]*", text)
    if not head:
        return {}, ["감리 PQ 세부평가기준 표 못 찾음"]
    end = re.search(r"\n\[(?:부표|별표)\s*\d", text[head.end():])
    sec = text[head.end(): head.end() + end.start()] if end else text[head.end():]
    out, issues = {}, []
    bname = PQ_BANDS[band][0]

    def table_item(name, seg, unit, input_label, quote):
        rows = pq_amount_table(seg, band, unit)
        if not rows:
            issues.append(f"감리 PQ '{name}' 표 못 읽음")
            return None
        return {"name": name, "rule_type": "TABLE", "max": max(r["score"] for r in rows), "input_key": "pq_grade",
                "parameters": {"rows": rows, "input_type": "count", "unit": unit, "입력": input_label,
                               "용역금액 구간": bname}, "quote": quote[:300]}

    # 가. 참여감리원 — 상주(전기분야 경력, 참여분야 경력 2개 표), 비상주(등급, 참여분야 경력)
    s1, s2, s3 = sec.find("(1)상주"), sec.find("(2)비상주"), sec.find("나. / 유")
    people = []
    if s1 >= 0:
        resident = sec[s1: s2 if s2 > s1 else s3 if s3 > s1 else len(sec)]
        for m in re.finditer(r"용역금액 /", resident):
            p = m.start()
            line_start = resident.rfind("\n", 0, p) + 1
            before = resident[line_start:p]
            name = ("상주감리원 참여분야 경력" if "공사설계" in before else
                    "상주감리원 참여분야 경력(공사감독·공사감리)" if "공사감독" in before else
                    "상주감리원 전기분야 경력")
            it = table_item(name, resident[p:].split("\n")[0], "년", "경력 년수 (년 미만 절사)",
                            resident[line_start:p + 120])
            if it:
                it["parameters"]["참고"] = "상주감리원이 2인 이상이면 개인별 점수 평균 (소수점 셋째자리 반올림)"
                people.append(it)
    if s2 >= 0:
        nonres = sec[s2: s3 if s3 > s2 else len(sec)]
        m = re.search(rf"{PQ_BANDS[band][1]}\s*:\s*특급감리원\s*:?\s*{PQ_NUM}\s*점\s*,\s*고급감리원\s*:?\s*{PQ_NUM}\s*점",
                      nonres)
        grade = [float(m.group(1)), float(m.group(2))] if m else None
        t = nonres.find("용역금액 / 특급감리원 / 고급감리원")  # 같은 내용을 표로 쓴 양식
        if grade is None and t >= 0:
            grade = pq_band_scores(nonres[t:].split("\n")[0], band, 2)
        if grade:
            rows = [{"grade": "특급감리원", "score": grade[0]}, {"grade": "고급감리원", "score": grade[1]}]
            people.append({"name": "비상주감리원 등급", "rule_type": "TABLE", "max": rows[0]["score"],
                           "input_key": "pq_grade",
                           "parameters": {"rows": rows, "input_type": "select", "unit": None, "용역금액 구간": bname,
                                          "참고": "고급감리원 미만으로 배치하면 실격"},
                           "quote": nonres[:300]})
        else:
            issues.append("감리 PQ '비상주감리원 등급' 점수 못 읽음")
        p = nonres.find("용역금액 /", max(0, nonres.find("참여")))
        if p >= 0:
            it = table_item("비상주감리원 참여분야 경력", nonres[p:].split("\n")[0], "년",
                            "직무기간 년수 (비상주 기간의 20% 인정, 년 미만 절사)", nonres[max(0, p - 150):p + 150])
            if it:
                people.append(it)
    out["참여감리원"] = people

    # 나. 유사용역수행실적 — 최근 3년 실적 합계 ÷ 사정금액 구간 (열 순서: 4천만원 미만 → 1억원~고시금액)
    sim = sec[s3:] if s3 >= 0 else ""
    rows = []
    for m in re.finditer(rf"사정금액\s*대비\s*{PQ_NUM}%\s*(이상|미만)(?:\s*{PQ_NUM}%\s*미만)?"
                         rf"\s*/\s*{PQ_NUM}\s*점\s*/\s*{PQ_NUM}\s*점\s*/\s*{PQ_NUM}\s*점", sim):
        a, kind, b = float(m.group(1)) / 100, m.group(2), m.group(3)
        if kind == "이상":
            row = {"min": a, "min_inc": True, "max": float(b) / 100 if b else None, "max_inc": False if b else None}
        else:
            row = {"min": None, "min_inc": None, "max": a, "max_inc": False}
        rows.append({**row, "label": re.sub(r"\s+", " ", m.group(0).split(" / ")[0]),
                     "score": float(m.group(4 + band))})
    header = sim[:sim.find("사정금액 대비")] if "사정금액 대비" in sim else ""
    order = [i for _, _, i in _band_marks(header)]
    base = re.search(r"사정금액\s*\(VAT\s*포함\)\s*[：:]\s*￦?\s*([\d,]+)", sim)
    if rows and order == [0, 1, 2]:
        basis = int(base.group(1).replace(",", "")) if base else srm_amount  # 기준서 칸이 비어 있으면 SRM 사정액
        if base and srm_amount and basis != srm_amount:
            issues.append(f"감리 PQ 유사용역: 기준서 사정금액 {basis:,} ≠ SRM 사정액 {srm_amount:,}")
        out["유사용역수행실적"] = [{
            "name": "감리업체 실적", "rule_type": "TABLE", "max": max(r["score"] for r in rows),
            "input_key": "similar_amount",
            "parameters": {"rows": rows, "value_unit": "기준치 배수",
                           "basis": basis, "basis_name": "사정금액(VAT 포함)",
                           "basis_source": "기준서" if base else "SRM 사정액", "입력": "최근 3년 유사용역 실적 합계(원)", "용역금액 구간": bname},
            "quote": sim[sim.find("감리업체실적"):][:300]}]
        if not basis:
            issues.append("감리 PQ 유사용역: 사정금액 못 찾음")
    else:
        issues.append("감리 PQ '유사용역수행실적' 표 못 읽음")

    # 다. 신용도 — 업체 자본비율·유동비율 ÷ 업종 평균 비율 구간
    credit = []
    for name, key, start, avg_re in (("자본비율", "equity_ratio", "(1)자본비율", r"평균\s*자기자본비율\s*:\s*([\d.]+)\s*%"),
                                     ("유동비율", "current_ratio", "(2) 유동비율", r"평균\s*유동비율\s*:\s*([\d.]+)\s*%")):
        s = sec.find(start)
        seg = sec[s:].split("\n")[0] if s >= 0 else ""
        if name == "자본비율" and "(2) 유동비율" in seg:
            seg = seg[:seg.find("(2) 유동비율")]
        rows = pq_amount_table(seg, band, "%")
        if not rows:
            issues.append(f"감리 PQ '{name}' 표 못 읽음")
            continue
        avg = re.search(avg_re, seg)
        if not avg:
            issues.append(f"감리 PQ '{name}' 업종 평균 비율 못 찾음")
        credit.append({"name": name, "rule_type": "TABLE", "max": max(r["score"] for r in rows), "input_key": key,
                       "parameters": {"rows": rows, "value_unit": "기준치 배수",
                                      "basis": float(avg.group(1)) if avg else None, "입력": f"업체 {name}(%)",
                                      "용역금액 구간": bname, "참고": "관련서류를 제출하지 않으면 최저점수"},
                       "quote": seg[:300]})
    out["신용도"] = credit

    # 라. 업무중첩도 — 상주(중복 배치면 실격 → 아니면 책임+보조 합계), 비상주(다른 현장 비상주 중복 개소 수)
    over = []
    s = sec.find("라. / 업")
    seg = sec[s:] if s >= 0 else ""
    r_start = seg.find("책임감리원 / 보조감리원 / 합계")
    if r_start >= 0:
        nums = pq_band_scores(seg[r_start:].split("\n")[0], band, 3)
        if nums and abs(nums[0] + nums[1] - nums[2]) < 1e-6:
            over.append({"name": "상주감리원 중복배치 없음", "rule_type": "FIXED", "max": nums[2], "input_key": "pq_check",
                         "parameters": {"score": nums[2], "condition": "상주감리원이 다른 공사현장에 중복 배치되지 않음 "
                                        "(중복 배치면 실격)", "책임감리원": nums[0], "보조감리원": nums[1],
                                        "용역금액 구간": bname},
                         "quote": seg[max(0, r_start - 120): r_start + 180]})
        else:
            issues.append("감리 PQ '상주감리원 업무중첩도' 점수 못 읽음")
    n_start = seg.find("6개소미만")
    if n_start >= 0:
        line = seg[n_start:].split("\n")[0]
        marks = _band_marks(line)
        header = [t.strip() for t in line[:marks[0][0]].split(" / ") if t.strip()] if marks else []
        rows = []
        for t in header:
            m = re.fullmatch(rf"{PQ_NUM}개소(미만)?", t.replace(" ", ""))
            if not m:
                rows = []
                break
            v = float(m.group(1))
            rows.append({"min": None, "min_inc": None, "max": v, "max_inc": False, "label": t} if m.group(2)
                        else {"min": v, "min_inc": True, "max": v, "max_inc": True, "label": t})
        scores = pq_band_scores(line, band, len(rows)) if rows else None
        if scores:
            for r, sc in zip(rows, scores):
                r["score"] = sc
            over.append({"name": "비상주감리원 중복배치", "rule_type": "TABLE", "max": max(scores), "input_key": "pq_grade",
                         "parameters": {"rows": rows, "input_type": "count", "unit": "개소",
                                        "입력": "다른 공사현장에 비상주감리원으로 배치된 개소 수", "용역금액 구간": bname,
                                        "참고": "다른 공사현장의 상주감리원으로 중복 배치되면 실격"},
                         "quote": seg[max(0, n_start - 150):n_start + 150]})
        else:
            issues.append("감리 PQ '비상주감리원 업무중첩도' 표 못 읽음")
    out["업무중첩도"] = over
    return out, issues


# 항목 하나만 못 읽은 문제 (나머지 배점 행은 믿을 수 있음 → 행을 남기고 그 행만 REVIEW)
ITEM_ONLY_ISSUE = re.compile(r"계산 방법은 원문 확인 필요|기준비율 값이 문서에 없음")


def extract_notice(db, notice_no):
    notice = db.execute("SELECT title, presumed_price, item_type, notice_date FROM bid_notice WHERE notice_no=?",
                        (notice_no,)).fetchone()
    title, price, item_type, notice_date = notice
    docs = db.execute("""
        SELECT a.id, a.sha256, c.text, a.file_ext FROM bid_attachment a
        JOIN bid_document_content c ON c.attachment_id = a.id
        WHERE a.notice_no=? AND a.doc_kind='CRITERIA' AND c.read_status='SUCCESS' ORDER BY a.id""",
                      (notice_no,)).fetchall()
    special = item_type == "Construction" and is_special(db, notice_no, title)
    result = {"notice_no": notice_no, "title": title, "presumed_price": price, "issues": [], "rows": []}
    chosen = None
    for aid, sha, text, ext in docs:
        lines = text.split("\n")
        gosi = notice_amount(text, notice_date)
        if (ext or "").lower() == "pdf":
            # PDF는 표 구조·분수가 깨져 파서로 확인할 수 없다 → 행을 만들지 않고 AI 파트(LLM)가 원본을 읽는다
            result["issues"].append("수집기 확인 불가: PDF 기준서")
            continue
        table, why = choose_table(top_tables(lines, gosi, special), price)
        if table:
            chosen = (aid, sha, text, lines, table)
            break
        result["issues"].append(why)
    if not chosen:
        if not docs:
            result["issues"].append("읽은 적격심사기준 없음")
        result["rule_extracted"] = False if docs else None  # 기준서가 아예 없으면 판단 대상 아님
        return result
    aid, sha, text, lines, table = chosen
    result["issues"] = []
    result["attachment_id"] = aid
    result["band"] = table["heading"]
    items = parse_column_table(lines, table) if table.get("col") is not None else parse_table(lines, table)
    rows = result["rows"]
    src = {"attachment_id": aid, "source_sha256": sha}

    def add(**row):
        row.setdefault("verification_status", "UNVERIFIED")
        rows.append({**src, **row})
        return len(rows) - 1

    # 1) 통과점수
    gosi = notice_amount(text, notice_date)
    ps, quote = pass_score(text, price, gosi)
    if ps is None:
        result["issues"].append("통과점수 못 찾음")
    else:
        add(parent=None, category="PASS_SCORE", item_name="낙찰자 결정 기준점수", rule_type="THRESHOLD",
            max_score=None, parameters={"pass_score": ps}, source_location="본문 > 낙찰자 결정", source_quote=quote)

    # 2) 입찰가격
    price_item = next((i for i in items if i["kind"] == "PRICE"), None)
    price_max = price_item["score"] if price_item else None
    formulas = price_formulas(text)
    for x in formulas:
        if x["base"] is None and price_max is not None:  # '배점한도 - 4 × |…|'
            x["base"], x["from_limit"] = price_max, True
        if x["cap"] and x["cap"]["score"] is None and x["base"] is not None:
            x["cap"]["score"] = round(x["base"] - x["coef"] * abs(x["ratio"] - x["cap"]["ratio"]), 4)
    pick = [f for f in formulas if price_max is not None and f["base"] == price_max]
    # 산식 바로 위에 금액 구간 제목이 있으면('2. 추정가격이 3억원 이상 10억원 미만인 전기·…공사') 그 구간으로 거른다
    for x in pick:
        before = text[max(0, x["pos"] - 400):x["pos"]].split("\n")[-4:]
        bands = [band_for(l, gosi, special) for l in before if "추정가격" in l]
        x["band"] = next((b for b in reversed(bands) if b), None)
    if price is not None and any(x["band"] for x in pick):
        in_price = [x for x in pick if x["band"] and in_band(price, x["band"]) is True]
        pick = in_price or [x for x in pick if not x["band"]] or pick
    after = [f for f in pick if f["pos"] >= sum(len(l) + 1 for l in lines[:table["start"]])]
    f = (after or pick or [None])[0]
    variants = {(x["base"], x["coef"], x["ratio"], x["subtract_a"], json.dumps(x["cap"]), x["min_score"])
                for x in pick}
    if price_item is None:
        result["issues"].append("배점표에 입찰가격 배점 없음")
    elif f is None:
        result["issues"].append("입찰가격 배점 못 읽음" if price_max is None else f"입찰가격 {price_max:g}점 산식 못 찾음")
        add(parent=None, category="PRICE", item_name="입찰가격", rule_type="FORMULA", max_score=price_max,
            formula=None, formula_source=None, parameters=None, verification_status="REVIEW",
            source_location="[별표 1] 배점표 > 입찰가격", source_quote=price_item["quote"])
    else:
        status = "UNVERIFIED"
        if len(variants) > 1:
            result["issues"].append(f"입찰가격 {price_max:g}점 산식이 {len(variants)}가지 → 배점표 뒤 첫 산식 사용")
            status = "REVIEW"
        if f["round_digits"] is None:  # 최저평점은 문서에 없는 경우가 있어 NULL(규칙 없음)로 둔다
            result["issues"].append("입찰가격 반올림 규칙 못 찾음")
            status = "REVIEW"
        params = {"vars": {"기준율": f["ratio"], "계수": f["coef"]}, "subtract_a": f["subtract_a"],
                  "min_score": f["min_score"], "cap": f["cap"]}
        if f["subtract_a"]:
            params["vars"]["A"] = "a_amount"
        add(parent=None, category="PRICE", item_name="입찰가격", rule_type="FORMULA", max_score=price_max,
            formula=formula_text(f), formula_source="EQUATION" if "[수식:" in f["quote"] else "TEXT",
            parameters=params, input_key="bid_price", rounding_digits=f["round_digits"],
            rounding_mode="ROUND" if f["round_digits"] is not None else None, verification_status=status,
            source_location="[별표 1] 입찰가격 평가산식", source_quote=f["quote"])

    # 3) 수행능력 묶음과 분야
    group_rows = {}
    for it in items:
        if it["kind"] == "GROUP":
            group_rows[it["name"]] = add(parent=None, category="PERFORMANCE", item_name=it["name"],
                                         rule_type="GROUP", max_score=it["score"],
                                         source_location="[별표 1] 배점표", source_quote=it["quote"][:300])
    for it in items:
        if it["kind"] in ("PERFORMANCE", "MANAGEMENT", "OFFICE"):
            add(parent=group_rows.get(it.get("parent")), category=it["kind"], item_name=it["name"],
                rule_type="GROUP", max_score=it["score"], parameters={"심사항목": it["detail"]} if it.get("detail") else None,
                source_location=f"[별표 1] 배점표 > {it.get('parent')}", source_quote=it["quote"][:300])
        elif it["kind"] == "BONUS":
            rng = it.get("range")
            params = {"심사항목": it.get("detail")}
            if rng:
                params.update(min=rng[0], max=rng[1])
            if it.get("sub_items"):
                params["items"] = it["sub_items"]  # 항목별 범위. 세부 표는 3단계
            add(parent=group_rows.get(it.get("parent")), category="BONUS", item_name=it["name"], rule_type="GROUP",
                max_score=rng[1] if rng else None, parameters=params,
                source_location=f"[별표 1] 배점표 > {it.get('parent')}", source_quote=it["quote"][:300])
        elif it["kind"] == "PENALTY":
            add(parent=None, category="PENALTY", item_name=it["name"], rule_type="FIXED", max_score=it["score"],
                parameters={"score": it["score"], "condition": it.get("detail")},
                source_location="[별표 1] 배점표 > 결격여부", source_quote=it["quote"][:300])

    # 3-2) 2단계 세부 표: 경영상태 (재무비율 구간표 + 기준치, 신용평가등급표 중 입찰자가 하나 선택)
    result["detail_issues"] = []
    for mi in [i for i, r in enumerate(rows) if r["category"] == "MANAGEMENT" and r["item_name"] == "경영상태"]:
        mgmt = rows[mi]
        if mgmt["max_score"] is None:
            continue
        detail = (mgmt.get("parameters") or {}).get("심사항목") or ""
        ratio, basis, credit, quotes, issues = management_details(
            lines, mgmt["max_score"], price, gosi, special, wants_credit=bool(re.search(r"신용|회사채", detail)),
            wants_ratio=bool(re.search(r"부채|유동|자기자본|재무", detail)) or not detail)
        result["detail_issues"] += issues
        status = "REVIEW" if issues else "UNVERIFIED"
        if ratio:
            scale = ratio[0].get("scale")
            fparams = {k: v for k, v in (("basis", basis), ("scale", scale and {"numerator": scale[0],
                       "denominator": scale[1], "note": "하위 합계 × 분자/분모"})) if v}
            fi = add(parent=mi, category="MANAGEMENT", item_name="재무비율", rule_type="GROUP",
                     max_score=mgmt["max_score"] if scale else sum(i["max"] for i in ratio), parameters=fparams or None,
                     verification_status=status, source_location="[별표 2] 경영상태 > 재무비율",
                     source_quote=quotes.get("basis"))
            for it in ratio:
                key, formula = RATIO_NAMES[it["name"]]
                params = {"rows": it["rows"], "value_unit": "기준치 배수" if it["uses_basis"] else "년"}
                if it["uses_basis"]:
                    params["basis"] = (basis or {}).get(it["name"])
                add(parent=fi, category="MANAGEMENT", item_name=it["name"], rule_type="TABLE", max_score=it["max"],
                    formula=formula, formula_source="TEXT" if formula else None, parameters=params, input_key=key,
                    verification_status=status, source_location=f"[별표 2] 경영상태 > {it['name']}",
                    source_quote=it["quote"])
        if credit:
            add(parent=mi, category="MANAGEMENT", item_name="신용평가등급", rule_type="TABLE",
                max_score=max(r["score"] for r in credit), parameters={"rows": credit, "no_grade_score": 0},
                input_key="credit_grade", verification_status=status,
                source_location="[별표 2] 경영상태 > 신용평가등급", source_quote=quotes.get("credit"))
        if ratio and credit:
            mgmt["parameters"] = {**(mgmt.get("parameters") or {}), "choose_one": ["재무비율", "신용평가등급"]}

    # 3-3) 2단계 세부 표: 시공경험 (실적누계액 ÷ (사정액 × 배수) × 배점, 상한·반올림·사정액)
    amounts = db.execute("SELECT approximate_amount, estimated_price_basic_amount FROM bid_notice WHERE notice_no=?",
                         (notice_no,)).fetchone()
    for ei in [i for i, r in enumerate(rows) if r["item_name"] == "시공경험" and r["rule_type"] == "GROUP"]:
        exp = rows[ei]
        if exp["max_score"] is None:
            continue
        sec = experience_section(lines, exp["max_score"], price, gosi, special, table["start"])
        if not sec:
            other = sorted({float(m.group(1)) for l in lines for m in [EXP_HEAD.search(l)] if m})
            result["detail_issues"].append(
                f"시공경험 세부 배점({', '.join(f'{v:g}' for v in other)}점)이 배점표({exp['max_score']:g}점)와 달라 "
                "환산 양식으로 보임 — 원문 확인 필요" if other else "시공경험 세부 절 못 찾음")
            continue
        exp_items, digits, _ = experience_items(lines, sec[0], sec[1], exp["max_score"])
        issues = []
        if not exp_items:
            issues.append("시공경험 산식 못 찾음")
        for it in exp_items:
            p = it["params"]
            if p.get("denominator") == "사정액":
                if p.get("base_amount") is None:
                    p["base_amount"], p["base_amount_source"] = amounts[0], "SRM 사정액"
                else:
                    p["base_amount_source"] = "기준서"
                    if amounts[0] and p["base_amount"] != amounts[0]:
                        issues.append(f"기준서 사정액 {p['base_amount']:,} ≠ SRM 사정액 {amounts[0]:,}")
            elif p.get("denominator") == "예비가격기초금액":
                p["base_amount"], p["base_amount_source"] = amounts[1], "SRM 예비가격기초금액"
        office = office_table(lines, sec[0], sec[1])
        if office:  # 표로 읽은 숙지도가 있으면 제목만 보고 만든 숙지도 항목은 뺀다
            exp_items = [i for i in exp_items if i.get("category") != "OFFICE"]
        if office and not office["office"]:
            issues.append("사업소 숙지도: 해당 사업소 이름 못 찾음")
        filled = sum(i["cap"] for i in exp_items if not i["bonus"]) + (office["cap"] if office else 0)
        if exp_items and filled < exp["max_score"] - 1e-6:
            issues.append(f"시공경험 일부 항목 미추출 ({filled:g}/{exp['max_score']:g}점)")
        elif filled > exp["max_score"] + 1e-6:
            issues.append(f"시공경험 세부 배점 합계({filled:g}점)가 한도({exp['max_score']:g}점)보다 큼")
        result["detail_issues"] += issues
        status = "REVIEW" if issues else "UNVERIFIED"
        if office:
            add(parent=ei, category="OFFICE", item_name=office["name"], rule_type="TABLE", max_score=office["cap"],
                parameters={k: office[k] for k in ("rows", "office", "adjacent", "outside_region_score")},
                input_key="office_location", verification_status=status,
                source_location="[별표 2] 시공경험 > 사업소 숙지도", source_quote=office["quote"])
        for it in exp_items:
            key = it.get("input_key") or (
                "same_kind_amount" if "동종" in it["name"] else "trained_workers" if "교육" in it["name"]
                else "developed_workers" if "양성" in it["name"] else "performance_amount")
            if it.get("review"):
                result["detail_issues"].append(f"시공경험 '{it['name'][:20]}' 계산 방법은 원문 확인 필요")
            formula = it.get("formula")
            add(parent=ei, category=it.get("category") or ("BONUS" if it["bonus"] else "PERFORMANCE"),
                item_name=it["name"], rule_type=it.get("rule_type", "FORMULA"), max_score=it["cap"], formula=formula,
                formula_source=("EQUATION" if "실적" in formula else "TEXT") if formula else None,
                parameters=it["params"], input_key=None if it.get("review") else key,
                rounding_digits=digits if formula else None,
                rounding_mode="ROUND" if formula and digits is not None else None,
                verification_status="REVIEW" if it.get("review") else status,
                source_location="[별표 2] 시공경험", source_quote=it["quote"])

    # 3-4) 2단계 세부 표: 신인도·가감점 (등급별 가·감점 표)
    if any(r["category"] == "BONUS" for r in rows):
        found = bonus_tables(lines, price, gosi, special)
        in_table = False
        if not found:  # 변전: 등급별 점수가 [별표 1] 배점표 행 안에 있음 ('A. 사망자 1명 / B. … | -2.0 / -3.5 / …')
            for i, r in enumerate(list(rows)):
                if r["category"] != "BONUS" or not r.get("source_location", "").startswith("[별표 1]"):
                    continue
                cells = split_cells(r.get("source_quote") or "")
                for k, cell in enumerate(cells[:-1]):
                    grades = [g.strip() for g in cell.split(" / ") if re.match(r"\s*[A-G]\.\s", g)]
                    scores = [to_num(x) for x in re.findall(SIGNED, cells[k + 1])]
                    if len(grades) >= 2 and len(grades) == len(scores):
                        in_table = True
                        grade_rows = add_grade_bounds([{"grade": g, "score": sc} for g, sc in zip(grades, scores)])
                        add(parent=i, category="BONUS", item_name=r["item_name"], rule_type="TABLE",
                            max_score=None, input_key="bonus_grade",
                            parameters={"group": r["item_name"], "condition": cell.split(" / A.")[0][:300],
                                        "rows": grade_rows, **grade_input(grade_rows)},
                            source_location="[별표 1] 배점표 > 신인도 등급", source_quote=r["source_quote"])
        if not found and not in_table:
            result["detail_issues"].append("신인도 세부 표 못 찾음")
        for b in found:
            parent = bonus_parent(rows, b["group"])
            if parent is None:
                continue
            params = {"group": b["group"][:100], "condition": b["condition"], "rows": add_grade_bounds(b["rows"])}
            params.update(grade_input(params["rows"]))
            if b["scale"]:
                params["scale"] = {"numerator": b["scale"][0], "denominator": b["scale"][1]}
            if b.get("rule"):  # 감리: 건당·기간당 점수 + 최대 점수 (규칙 문장)
                rule = b["rule"]
                limit = b["rows"][0]["score"]
                if not limit and rule.get("band_caps") and pq_band(price) is not None:  # 표 안 구간별 최대 점수
                    limit = rule["sign"] * rule["band_caps"][pq_band(price)]
                params = {"group": b["group"], "per_unit": rule["per_unit"], "sign": rule["sign"],
                          "limit": limit, "설명": rule["설명"]}
                ok = rule["per_unit"] is not None and limit
                if not ok:
                    result["detail_issues"].append(f"가점/감점 '{b['name'][:15]}' 계산 방법은 원문 확인 필요")
                add(parent=parent, category="BONUS", item_name=b["name"], rule_type="FORMULA" if ok else "GROUP",
                    max_score=limit, formula=(f"{'-' if rule['sign'] < 0 else ''}min(건수 × {rule['per_unit']:g}, "
                                              f"{abs(limit):g})") if ok else None,
                    formula_source="TEXT" if ok else None, parameters=params,
                    input_key="bonus_count" if ok else None, verification_status="UNVERIFIED" if ok else "REVIEW",
                    source_location="[별표 2] 신인도", source_quote=b["quote"])
                continue
            add(parent=parent, category="BONUS", item_name=b["name"], rule_type="TABLE",
                max_score=None, parameters=params, input_key="bonus_grade",
                source_location="[별표 2] 신인도", source_quote=b["quote"])

    # 3-5) 2단계 세부 표: 감리 PQ (참여감리원·유사용역수행실적·신용도·업무중첩도, [별표 12] 세부평가기준)
    pq_rows = {r["item_name"]: i for i, r in enumerate(rows)
               if r["rule_type"] == "GROUP" and r["item_name"] in ("참여감리원", "유사용역수행실적", "신용도", "업무중첩도")}
    if pq_rows:
        pq, issues = pq_details(text, price, amounts[0])
        for name, gi in pq_rows.items():
            got = pq.get(name) or []
            limit = rows[gi]["max_score"]
            filled = sum(it["max"] for it in got)
            item_issues = [i for i in issues if f"'{name}'" in i or (name == "유사용역수행실적" and "유사용역" in i)]
            if not got:
                item_issues = item_issues or [f"감리 PQ '{name}' 세부 표 못 찾음"]
            elif limit is not None and abs(filled - limit) > 1e-6:
                item_issues.append(f"감리 PQ '{name}' 세부 배점 합계 {filled:g} ≠ {limit:g}")
            result["detail_issues"] += item_issues
            for it in got:
                add(parent=gi, category=rows[gi]["category"], item_name=it["name"], rule_type=it["rule_type"],
                    max_score=it["max"], parameters=it["parameters"], input_key=it["input_key"],
                    verification_status="REVIEW" if item_issues else "UNVERIFIED",
                    source_location=f"[별표] 감리 사업수행능력 세부평가기준 > {name}", source_quote=it["quote"])
        result["detail_issues"] += [i for i in issues if not any(f"'{n}'" in i for n in pq_rows) and "유사용역" not in i]

    # 3-6) 기술용역 사업수행능력: '평점 = (평가점수/100) × 70점, 소수점 셋째자리에서 반올림' → 환산 산식
    #      평가점수(100점 만점) 세부 기준은 별도 '사업수행능력 세부평가기준' 문서라 업체가 평가점수를 입력한다
    for r in rows:
        detail = (r.get("parameters") or {}).get("심사항목") or ""
        m = PQ_SCORE_RE.search(detail)
        if r["item_name"] != "사업수행능력" or r["rule_type"] != "GROUP" or not m:
            continue
        points = float(m.group(1))
        if r["max_score"] is not None and abs(r["max_score"] - points) > 1e-6:
            result["detail_issues"].append(f"사업수행능력 환산 배점 {points:g} ≠ 배점표 {r['max_score']:g}")
            r["verification_status"] = "REVIEW"
        digits = 2 if re.search(r"소수점\s*셋째\s*자리에서\s*반올림", detail) else None
        r.update(rule_type="FORMULA", max_score=r["max_score"] if r["max_score"] is not None else points,
                 formula=f"평가점수/100 × {points:g}", formula_source="TEXT", input_key="pq_score",
                 parameters={**r["parameters"], "points": points, "full": 100,
                             "입력": "사업수행능력 평가점수(100점 만점, 재평가했으면 재평가 점수)"},
                 rounding_digits=digits, rounding_mode="ROUND" if digits is not None else None)
        if digits is None:
            result["detail_issues"].append("사업수행능력 반올림 규칙 못 찾음")
            r["verification_status"] = "REVIEW"

    # 4) 검산: 수행능력 묶음 합 + 입찰가격 = 100, 묶음 안 분야 합 = 묶음 한도
    total = next((i["score"] for i in items if i["kind"] == "TOTAL"), None)
    groups = [r for r in rows if r["rule_type"] == "GROUP" and r["parent"] is None]
    if len(groups) == 1 and groups[0]["max_score"] is None and price_max is not None:
        groups[0]["max_score"] = (total or 100) - price_max  # 용역 양식은 묶음 한도가 소계 줄에만 있다
    top_sum = sum(r["max_score"] or 0 for r in groups) + (price_max or 0)
    if abs(top_sum - (total or 100)) > 1e-6:
        result["issues"].append(f"배점 합계 {top_sum:g} ≠ {total or 100:g}")
    for gi, g in enumerate(rows):
        if g["rule_type"] != "GROUP" or g["parent"] is not None:
            continue
        kids = [r for r in rows if r["parent"] == gi and r["category"] != "BONUS"]
        for k in kids:
            if k["max_score"] is None:
                result["issues"].append(f"'{k['item_name'][:20]}' 배점 못 읽음")
        if kids and all(k["max_score"] is not None for k in kids):
            s = sum(k["max_score"] for k in kids)
            if g["max_score"] is not None and abs(s - g["max_score"]) > 1e-6:
                result["issues"].append(f"'{g['item_name']}' 하위 합계 {s:g} ≠ {g['max_score']:g}")
    if not groups:
        result["issues"].append("수행능력 묶음 못 찾음")
    # 규칙 추출 결과 표시 (팀 결정 2026-11-22). 문제가 하나라도 있으면 본문 rule_extracted=0 → LLM 대상.
    #  - 항목 하나만 못 읽은 경우(계산 방법이 문장뿐, 기준비율 값 없음): 행은 남기고 그 행만 REVIEW → LLM은 REVIEW 행만 읽음
    #  - 배점표·세부 표 자체를 못 믿는 경우(PDF·구간 불일치·합계 안 맞음·표 못 찾음): 배점 행을 비움 → LLM이 기준서 전체를 읽음
    problems = result["issues"] + result["detail_issues"]
    result["rule_extracted"] = not problems
    if result["issues"] or any(not ITEM_ONLY_ISSUE.search(p) for p in result["detail_issues"]):
        result["rows"] = []
    return result


# ------------------------------------------------------------------ 저장
def insert(db, results, now, only_these=False):
    """파서 행을 지우고 다시 넣는다. only_these=True 면 results 에 있는 공고의 파서 행만 바꾼다 (수집 직후 1건 갱신)."""
    edited = {r[0] for r in db.execute("SELECT DISTINCT notice_no FROM evaluation_rule WHERE updated_by != 'PARSER'")}
    # LLM·사용자가 고친 행이 있는 공고는 파서 행도 그대로 둔다 (지우면 고친 행만 남아 구조가 깨짐)
    keep = "AND notice_no NOT IN (SELECT notice_no FROM evaluation_rule WHERE updated_by != 'PARSER')"
    if only_these:
        db.executemany(f"DELETE FROM evaluation_rule WHERE updated_by='PARSER' AND notice_no=? {keep}",
                       [(r["notice_no"],) for r in results])
    else:
        db.execute(f"DELETE FROM evaluation_rule WHERE updated_by='PARSER' {keep}")
    n = 0
    for res in results:
        if res.get("rule_extracted") is not None:  # 기준서 본문에 규칙 추출 성공(1)·실패(0) 표시
            db.execute("""UPDATE bid_document_content SET rule_extracted=? WHERE attachment_id IN
                          (SELECT id FROM bid_attachment WHERE notice_no=? AND doc_kind='CRITERIA')""",
                       (int(res["rule_extracted"]), res["notice_no"]))
        if res["notice_no"] in edited:
            continue  # LLM·사용자가 고친 공고는 건드리지 않는다
        ids = []
        for pos, r in enumerate(res["rows"], 1):
            parent_id = ids[r["parent"]] if r.get("parent") is not None else None
            cur = db.execute("""
                INSERT INTO evaluation_rule (notice_no, parent_id, position, category, item_name, rule_type, max_score,
                    formula, formula_source, parameters, input_key, rounding_digits, rounding_mode, attachment_id,
                    source_sha256, source_location, source_quote, verification_status, updated_by, extracted_at,
                    updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PARSER',?,?)""",
                (res["notice_no"], parent_id, pos, r["category"], r["item_name"], r["rule_type"], r.get("max_score"),
                 r.get("formula"), r.get("formula_source"),
                 json.dumps(r["parameters"], ensure_ascii=False) if r.get("parameters") is not None else None,
                 r.get("input_key"), r.get("rounding_digits"), r.get("rounding_mode"), r["attachment_id"],
                 r["source_sha256"], r.get("source_location"), r.get("source_quote"), r["verification_status"],
                 now, now))
            ids.append(cur.lastrowid)
            n += 1
    return n


def refresh_notice(db, notice_no):
    """수집기가 공고 1건을 처리한 뒤 부른다: 그 공고의 배점 행을 다시 뽑아 바꾼다 (커밋은 호출자).
    → (넣은 행 수, 1단계 문제, 2단계 세부 표 문제)"""
    res = extract_notice(db, notice_no)
    n = insert(db, [res], datetime.now().isoformat(timespec="seconds"), only_these=True)
    return n, res["issues"], res.get("detail_issues", [])


def summarize(results):
    done = [r for r in results if r["rows"] and not r["issues"]]
    review = [r for r in results if r["rows"] and r["issues"]]
    empty = [r for r in results if not r["rows"]]
    print(f"공고 {len(results)}건: 확인 필요 없음 {len(done)} / REVIEW {len(review)} / 못 뽑음 {len(empty)}")
    print(f"행 {sum(len(r['rows']) for r in results)}개")
    counts = {}
    for r in review + empty:
        for i in r["issues"]:
            key = re.sub(r"[\d,.]+", "N", i)
            counts[key] = counts.get(key, 0) + 1
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {v:3d}  {k}")
    detail = {}
    for r in results:
        for i in r.get("detail_issues", []):
            key = re.sub(r"[\d,.]+", "N", i)
            detail[key] = detail.get(key, 0) + 1
    if detail:
        print("2단계 세부 표 (해당 행만 REVIEW):")
        for k, v in sorted(detail.items(), key=lambda x: -x[1]):
            print(f"  {v:3d}  {k}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--notice", help="공고번호 1건만 (미리보기 출력)")
    ap.add_argument("--apply", action="store_true", help="DB 백업 후 반영")
    args = ap.parse_args()
    db_path = Path(args.db)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) if not args.apply else None
    if args.apply:
        backup = db_path.with_name(db_path.name + ".bak-" + datetime.now().strftime("%Y%m%d%H%M%S"))
        shutil.copy2(db_path, backup)
        print(f"백업: {backup}")
        init_db(db_path)
        db = sqlite3.connect(db_path)
        db.execute("PRAGMA foreign_keys = ON")
    notices = [args.notice] if args.notice else [r[0] for r in db.execute(
        "SELECT DISTINCT notice_no FROM bid_attachment WHERE doc_kind='CRITERIA' ORDER BY notice_no")]
    results = [extract_notice(db, n) for n in notices]
    if args.notice:
        print(json.dumps(results[0], ensure_ascii=False, indent=1))
        return
    summarize(results)
    if args.apply:
        now = datetime.now().isoformat(timespec="seconds")
        with db:
            n = insert(db, results, now)
        print(f"evaluation_rule {n}행 반영")
    else:
        PREVIEW_PATH.parent.mkdir(exist_ok=True)
        PREVIEW_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"미리보기: {PREVIEW_PATH}")


if __name__ == "__main__":
    main()
