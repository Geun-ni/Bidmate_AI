"""적격심사기준 본문(bid_document_content) → evaluation_rule 1단계 추출 (규칙 파서, LLM 안 씀)

1단계 범위: 공고 추정가격에 맞는 [별표 1] 배점표를 골라
  - 통과점수 (PASS_SCORE / THRESHOLD)
  - 입찰가격 배점과 평가산식 (PRICE / FORMULA): 기준율·계수·A값 차감·반올림·최저평점·상한 구간
  - 수행능력 묶음과 그 아래 분야별 배점 (GROUP), 가감점 범위, 기술자 미달 등 결격 감점 (FIXED)
세부 점수표(경영상태 구간표, 신용등급표, 신인도 표)는 2·3단계에서 GROUP 아래에 붙인다.

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


# ------------------------------------------------------------------ 공고 1건
def is_special(db, notice_no, title):
    names = " ".join(r[0] or "" for r in db.execute(
        "SELECT name FROM bid_restriction_item WHERE notice_no=? AND category='LICENSE'", (notice_no,)))
    return any(t in names for t in SPECIAL_TRADES) or any(t in (title or "") for t in ("전기공사",))


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
    if result["issues"]:
        for r in rows:
            r["verification_status"] = "REVIEW"
    return result


# ------------------------------------------------------------------ 저장
def insert(db, results, now):
    edited = {r[0] for r in db.execute("SELECT DISTINCT notice_no FROM evaluation_rule WHERE updated_by != 'PARSER'")}
    db.execute("DELETE FROM evaluation_rule WHERE updated_by='PARSER'")
    n = 0
    for res in results:
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
