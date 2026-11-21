"""대표 공고 공고문·적격심사기준에서 팀이 정한 항목의 위치와 원문 발췌 찾기 (LLM 사용 안함).

- 수집기 파서(doc_reader)는 바꾸지 않는다. 여기의 표 해석기는 검증 전용으로, 원래 표의 행·열 구조를 복원해
  현재 파서가 읽은 문단(표 칸이 한 줄씩 흩어진 상태)과 비교하는 데 쓴다.
- 항목 찾기는 키워드·조항 제목 규칙으로만 한다. 내용 해석·요약은 하지 않는다.

입력: verify_reference_documents.py 결과 JSON (파일 경로 포함)
실행 (저장소 루트):
  python -m backend.collector.analyze_reference_documents --docs 결과.json --out 분석.json
"""
import argparse
import io
import json
import re
import struct
import zipfile
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

import olefile

from backend.collector.collect import ROOT
from backend.collector.verify_reference_notices import EXPECTED

# ------------------------------------------------------------------ 표 구조 복원 (검증 전용)
TAG = {"PARA_TEXT": 67, "CTRL_HEADER": 71, "LIST_HEADER": 72, "TABLE": 77, "EQEDIT": 88}
CHAR_CONTROLS = {0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31}


def _records(data):
    i = 0
    while i + 4 <= len(data):
        h = struct.unpack("<I", data[i:i + 4])[0]
        i += 4
        tag, level, size = h & 0x3FF, (h >> 10) & 0x3FF, (h >> 20) & 0xFFF
        if size == 0xFFF:
            size = struct.unpack("<I", data[i:i + 4])[0]
            i += 4
        yield tag, level, data[i:i + size]
        i += size


def _para_text(body):
    out, j = bytearray(), 0
    while j + 2 <= len(body):
        c = struct.unpack("<H", body[j:j + 2])[0]
        if c < 32:
            j += 2 if c in CHAR_CONTROLS else 16
        else:
            out += body[j:j + 2]
            j += 2
    return out.decode("utf-16le", "replace").strip()


def hwp_blocks(data):
    """HWP → 블록 목록. ('p', 문단) / ('table', {rows, cols, cells:[(row, col, text)]}) / ('eq', 스크립트)"""
    ole = olefile.OleFileIO(io.BytesIO(data))
    compressed = bool(struct.unpack("<I", ole.openstream("FileHeader").read()[36:40])[0] & 1)
    blocks, stack = [], []  # stack: [{'level', 'table', 'cell'}]
    for name in sorted("/".join(s) for s in ole.listdir() if s[0] == "BodyText"):
        raw = ole.openstream(name).read()
        raw = zlib.decompress(raw, -15) if compressed else raw
        for tag, level, body in _records(raw):
            while stack and level <= stack[-1]["level"]:  # 표 컨트롤보다 얕은 레코드 → 표 끝
                stack.pop()
            if tag == TAG["CTRL_HEADER"] and body[:4][::-1] == b"tbl ":
                table = {"rows": None, "cols": None, "cells": []}
                stack.append({"level": level, "table": table, "cell": None})
                if len(stack) == 1:
                    blocks.append(("table", table))
            elif tag == TAG["TABLE"] and stack and len(body) >= 8:
                stack[-1]["table"]["rows"], stack[-1]["table"]["cols"] = struct.unpack("<HH", body[4:8])
            elif tag == TAG["LIST_HEADER"] and stack and level == stack[-1]["level"] + 1 and len(body) >= 16:
                col, row, colspan, rowspan = struct.unpack("<HHHH", body[8:16])
                cell = [row, col, ""]
                stack[-1]["table"]["cells"].append(cell)
                stack[-1]["cell"] = cell
            elif tag == TAG["PARA_TEXT"]:
                text = _para_text(body)
                if not text:
                    continue
                if stack and stack[-1]["cell"] is not None:
                    cell = stack[-1]["cell"]
                    cell[2] = (cell[2] + " / " + text) if cell[2] else text
                elif not stack:
                    blocks.append(("p", text))
            elif tag == TAG["EQEDIT"] and len(body) >= 6:
                n = struct.unpack("<H", body[4:6])[0]
                script = body[6:6 + n * 2].decode("utf-16le", "replace")
                if stack and stack[-1]["cell"] is not None:
                    stack[-1]["cell"][2] += f" [수식: {script.strip()}]"
                else:
                    blocks.append(("eq", script))
    return blocks


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def hwpx_blocks(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    blocks = []

    def text_of(el, skip_tables=True):
        """칸 안의 문단은 ' / ' 로 구분 (여러 줄 값이 붙지 않게)."""
        paras = []
        for p in el.iter():
            if _local(p.tag) != "p":
                continue
            parts = [c.text for c in p.iter() if _local(c.tag) == "t" and c.text]
            parts += [f"[수식: {c.text.strip()}]" for c in p.iter() if _local(c.tag) == "script" and c.text]
            if "".join(parts).strip():
                paras.append("".join(parts).strip())
        return " / ".join(paras)

    def walk(el):
        for child in el:
            name = _local(child.tag)
            if name == "tbl":
                table = {"rows": int(child.get("rowCnt", 0) or 0), "cols": int(child.get("colCnt", 0) or 0), "cells": []}
                for tc in child.iter():
                    if _local(tc.tag) != "tc":
                        continue
                    addr = next((a for a in tc if _local(a.tag) == "cellAddr"), None)
                    row = int(addr.get("rowAddr", 0)) if addr is not None else 0
                    col = int(addr.get("colAddr", 0)) if addr is not None else 0
                    table["cells"].append([row, col, text_of(tc, skip_tables=False)])
                blocks.append(("table", table))
            elif name == "p":
                own = []
                for run in child:
                    for item in run:
                        n = _local(item.tag)
                        if n == "t" and item.text:
                            own.append(item.text)
                        elif n == "equation":
                            blocks.append(("eq", "".join(s.text or "" for s in item.iter() if _local(s.tag) == "script")))
                if "".join(own).strip():
                    blocks.append(("p", "".join(own).strip()))
                walk(child)  # 문단 안의 표·도형
            else:
                walk(child)

    for name in sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)):
        walk(ET.fromstring(z.read(name)))
    return blocks


def read_blocks(path):
    data = (ROOT / path).read_bytes()
    if data[:8] == bytes.fromhex("d0cf11e0a1b11ae1"):
        return hwp_blocks(data)
    if data[:2] == b"PK":
        return hwpx_blocks(data)
    return None  # PDF 등: 표 구조 복원 불가


def table_grid(table, max_rows=6, max_cols=8):
    rows = {}
    for r, c, text in table["cells"]:
        rows.setdefault(r, {})[c] = text
    lines = []
    for r in sorted(rows)[:max_rows]:
        cells = [rows[r][c][:30] for c in sorted(rows[r])][:max_cols]
        lines.append(" | ".join(cells))
    return lines


# ------------------------------------------------------------------ 항목 위치 찾기
ITEMS = {
    "CRITERIA": ["배점 기준표", "입찰가격 평가산식", "해당·인접 사업소", "경영상태 평균비율", "적격심사 제출서류"],
    "NOTICE": ["입찰참가자격", "입찰참가자격 증명 서류", "A금액", "순공사원가"],
}
NOT_APPLICABLE = re.compile(r"해당\s*(사항)?\s*없음")
# 항목별 패턴 목록: 앞 패턴부터 문서 전체에서 찾고, 없으면 다음 패턴으로 넘어간다
P = {
    "입찰가격 평가산식": [r"입찰가격.{0,8}(평가|평점)\s*산식|입찰가격.{0,20}계산식|다음\s*(계산식|산식)에\s*의거"],
    "해당·인접 사업소": [r"(해당|인접)\s*사업소"],
    "경영상태 평균비율": [r"평균\s*비율|가중\s*평균", r"기준\s*비율"],
    "적격심사 제출서류": [r"^제\s*\d+\s*조.{0,25}(제출\s*서류|자료\s*제출)",
                    r"적격심사\s*(대상자.{0,10})?서류.{0,10}제출|적격심사\s*서류"],
    "입찰참가자격": [r"^\s*(\d+[.)]|[가-하][.)])?\s*입\s*찰\s*참\s*가\s*(자\s*격|요\s*건)"],
    "입찰참가자격 증명 서류": [r"입찰참가자격은\s*아래와\s*같이\s*확인|참가자격.{0,15}(확인|증명)\s*(방법|서류)",
                       r"참가자격\s*증빙\s*서류|증빙\s*(서류|자료)로\s*다음의?\s*서류",
                       r"(참가자격|입찰참가신청).{0,40}(제출\s*서류|증빙|증명\s*서류)|제출\s*서류.{0,30}(스캔|첨부)",
                       r"제출\s*서류\s*[:：]"],
    "A금액": [r"(^|[^가-힣A-Za-z])A\s*(값|금액)?\s*[:=：]|A\s*값|\(\s*A\s*\)"],
    "순공사원가": [r"순\s*공\s*사\s*원\s*가.{0,15}\d{1,3}(,\d{3})+", r"순\s*공\s*사\s*원\s*가"],  # 금액이 적힌 문장 우선
}
# 입찰참가신청 시 제출서류(스캔 첨부) 문단 — 증명 서류 항목의 보조 정보로 따로 기록
APPLY_DOCS = re.compile(r"제출\s*서류\s*[:：]")


def heading_of(text, ctx):
    t = text.strip()
    if re.match(r"^제\s*\d+\s*조", t):
        ctx.update(article=t[:40], note=None)
    if re.match(r"^[\[【]?\s*별\s*표\s*\d+", t):
        ctx.update(annex=t[:30], article=None, note=None)  # 별표는 본문 조항 밖
    if re.match(r"^[(（]?\s*주\s*\d*\s*[)）]", t):
        ctx["note"] = t[:30]
    # 공고문은 '3. 입찰참가자격 …' 같은 번호 항목이 큰 제목 (조항이 없을 때만 위치로 씀)
    if re.match(r"^\d{1,2}\s*\.\s*\S", t) and len(t) < 80:
        ctx["section"] = t[:30]


def location(ctx, index):
    parts = [ctx.get("annex"), ctx.get("article")] if ctx.get("annex") or ctx.get("article") else [ctx.get("section")]
    parts.append(ctx.get("note"))
    return " > ".join(p for p in parts if p) or f"본문 {index + 1}번째 블록 부근"


def excerpt_after(blocks, i, n=4):
    out = []
    for kind, val in blocks[i:i + n]:
        if kind == "p":
            out.append(val[:140])
        elif kind == "eq":
            out.append(f"[수식] {val.strip()[:140]}")
        else:
            out.append("[표] " + " // ".join(table_grid(val, 4)))
    return out


def table_has(table, *words):
    text = " ".join(c[2] for c in table["cells"])
    return all(re.search(w, text) for w in words)


def parser_view(flat, table):
    """현재 파서(doc_reader) 출력에서 같은 표가 어떻게 읽혔는지: 첫 칸 문단부터 10줄."""
    first = next((c[2].split(" / ")[0] for c in sorted(table["cells"]) if c[2].strip()), "")
    key = re.sub(r"\s+", "", first)[:12]
    for i, line in enumerate(flat or []):
        if key and key in re.sub(r"\s+", "", line):
            return [l[:60] for l in flat[i:i + 10]]
    return ["(파서 출력에서 표 첫 칸을 찾지 못함)"]


def with_context(blocks):
    """각 블록에 그 시점의 위치(별표·조항·주) 문자열을 붙인다."""
    ctx, out = {}, []
    for i, (kind, val) in enumerate(blocks):
        if kind == "p":
            heading_of(val, ctx)
        out.append((i, kind, val, location(ctx, i)))
    return out


def next_table(blocks, i, words, within=8):
    for kind, val in blocks[i + 1:i + 1 + within]:
        if kind == "table" and table_has(val, *words):
            return val
    return None


def find_item(item, blocks, flat, srm):
    located = with_context(blocks)
    if item == "배점 기준표":
        for i, kind, val, loc in located:
            if kind == "table" and table_has(val, r"배\s*점", r"심\s*사\s*(분\s*야|항\s*목)|평\s*가\s*항\s*목"):
                return {"status": "찾음", "location": loc, "format": "표",
                        "excerpt": table_grid(val, 6), "parser_view": parser_view(flat, val)}
        return {"status": "문서에서 찾지 못함"}
    # 금액 항목: 공고문 금액표의 행 이름(첫 칸)으로 먼저 찾는다 (예: '－ A금액 | ￦ | 10,280,757')
    label = {"A금액": r"A\s*금액|A\s*값", "순공사원가": r"순\s*공\s*사\s*원\s*가"}.get(item)
    if label:
        for i, kind, val, loc in located:
            if kind != "table":
                continue
            for r, c, text in sorted(val["cells"]):
                if c == 0 and re.search(label, text):
                    row = " | ".join(t[:40] for rr, cc, t in sorted(val["cells"], key=lambda x: x[1]) if rr == r)
                    return {"status": "찾음", "location": loc, "format": f"표 {r + 1}행", "excerpt": [row],
                            "parser_view": parser_view(flat, {"cells": [[r, 0, text]]})}
        # 순공사원가라는 행 이름 없이 재료비·노무비·경비·합계로만 된 표 → 합계 행
        if item == "순공사원가":
            for i, kind, val, loc in located:
                if kind == "table" and table_has(val, r"재\s*료\s*비", r"노\s*무\s*비", r"경\s*비", r"합\s*계"):
                    total = next(((r, t) for r, c, t in sorted(val["cells"]) if c == 0 and re.search(r"합\s*계", t)), None)
                    if total:
                        row = " | ".join(t[:40] for rr, cc, t in sorted(val["cells"], key=lambda x: x[1]) if rr == total[0])
                        return {"status": "찾음", "location": loc, "format": f"표 {total[0] + 1}행 (행 이름 '합계', 순공사원가 명칭 없음)",
                                "excerpt": [row], "parser_view": parser_view(flat, {"cells": [[total[0], 0, total[1]]]})}
    for pattern in P[item]:
        rx = re.compile(pattern)
        # 표 칸 안의 문단도 찾는다 (예: 진단용역 배점표 칸 안의 '다음 산식에 의거 … *평점=90-20×')
        for i, kind, val, loc in located:
            if kind != "table":
                continue
            for r, c, text in sorted(val["cells"]):
                m = rx.search(text)
                if m and not any(rx.search(v) for k, v in blocks if k in ("p", "eq")):
                    # 한 칸에 여러 내용이 있을 수 있어(예: 수행능력 환산식 + 입찰가격 산식) 일치한 곳부터 발췌
                    start = text.rfind(" / ", 0, m.start())
                    cell_excerpt = text[start + 3 if start >= 0 else 0:][:300]
                    status = "해당 없음이 확인됨" if NOT_APPLICABLE.search(cell_excerpt[:120]) else "찾음"
                    return {"status": status, "location": loc, "format": f"표 칸 안 ({r + 1}행 {c + 1}열)",
                            "excerpt": [cell_excerpt], "table": table_grid(val, 8),
                            "parser_view": parser_view(flat, {"cells": [[r, c, text[m.start():]]]})}
        for i, kind, val, loc in located:
            if kind not in ("p", "eq") or not rx.search(val):
                continue
            ex = excerpt_after(blocks, i)
            status = "해당 없음이 확인됨" if NOT_APPLICABLE.search(" ".join(ex[:2])) else "찾음"
            found = {"status": status, "location": loc, "format": "본문" if kind == "p" else "수식", "excerpt": ex}
            if item == "해당·인접 사업소":
                table = next_table(blocks, i, [r"해당\s*사업소"])
                if table:
                    found.update(format="본문 + 표", table=table_grid(table, 4), parser_view=parser_view(flat, table))
            if item == "입찰참가자격 증명 서류":
                apply = next(((loc2, val2) for _, k2, val2, loc2 in located if k2 == "p" and APPLY_DOCS.search(val2)), None)
                if apply:
                    found["apply_docs"] = {"location": apply[0], "excerpt": apply[1][:140]}
            return found
    return {"status": "문서에서 찾지 못함"}


def find_value(blocks, value):
    """SRM 금액이 원문 어디에 나오는지 (쉼표 형식 또는 숫자 그대로)."""
    if value is None:
        return None
    keys = (f"{value:,}", str(value))
    for i, kind, val, loc in with_context(blocks):
        text = val if kind in ("p", "eq") else " ".join(c[2] for c in val["cells"])
        if not any(k in text for k in keys):
            continue
        if kind != "table":
            return {"location": loc, "excerpt": val[:160]}
        # 표: 금액이 든 칸의 행 전체(앞 칸에 항목 이름이 있음) + 표 제목 행
        cell = next(c for c in val["cells"] if any(k in c[2] for k in keys))
        row = " | ".join(c[2][:40] for c in sorted(val["cells"], key=lambda c: c[1]) if c[0] == cell[0])
        header = " | ".join(c[2][:20] for c in sorted(val["cells"], key=lambda c: c[1]) if c[0] == 0)
        return {"location": loc, "excerpt": f"[표 {cell[0] + 1}행] {row}", "table_header": header}
    return None


def srm_value_check(item, blocks, srm):
    """A금액·순공사원가: SRM 수집값(웹) ↔ 원문에서 같은 금액을 직접 찾은 위치를 구분해 기록.
    순공사원가 행의 SRM 값은 overHeadCostAmount(=경비)다. SRM에는 순공사원가 값이 없다."""
    key = {"A금액": "a_amount", "순공사원가": "overhead_cost_amount"}.get(item)
    if not key:
        return None
    srm = srm or {}
    value = srm.get(key, srm.get("net_construction_cost") if key == "overhead_cost_amount" else None)  # 예전 결과 파일 키
    if value is None:
        return {"srm_value": "확인 불가(SRM 값 없음)", "document_value": "확인 불가"}
    if value == 0:  # 0원은 원문 숫자 '0'과 구분할 수 없으므로 찾지 않는다
        return {"srm_value": 0, "document_value": "SRM 값이 0이라 원문 대조하지 않음"}
    hit = find_value(blocks, value)
    return {"srm_value": value,
            "document_value": "원문에 같은 금액 있음" if hit else "원문에서 같은 금액을 찾지 못함",
            "document_location": hit}


def load_collected_documents(db_path):
    from contextlib import closing

    from backend.notices_api import SCOPE, connect

    with closing(connect(db_path)) as db:
        notices = {
            row["notice_no"]: {"srm": dict(row), "files": {}}
            for row in db.execute(f"SELECT notice_no, item_type, a_amount, overhead_cost_amount FROM bid_notice WHERE {SCOPE}")
        }
        for row in db.execute('''
            SELECT a.*, c.read_status, c.text, c.equation_count
            FROM bid_attachment a
            LEFT JOIN bid_document_content c ON c.attachment_id = a.id
            ORDER BY a.fetched_at DESC, a.id DESC
        '''):
            entry = notices.get(row["notice_no"])
            if entry is None:
                continue
            if row["download_status"] != "SUCCESS":
                status = "DOWNLOAD_FAILED"
            elif row["read_status"] == "SUCCESS":
                status = "OK"
            else:
                status = "READ_FAILED"
            entry["files"].setdefault(row["doc_kind"], []).append({
                "filename": row["filename"], "source": row["source"], "status": status,
                "format": row["file_ext"], "equation_count": row["equation_count"],
                "error": row["error"], "path": row["local_path"],
                "paragraphs": (row["text"] or "").splitlines(),
            })
    return notices


def analyze(docs, expected=EXPECTED):
    out = {}
    for no, exp in expected.items():
        entry = docs.get(no, {})
        res = {"type": exp["type"], "srm": entry.get("srm", {}), "docs": {}}
        for kind in ("NOTICE", "CRITERIA"):
            files = entry.get("files", {}).get(kind, [])
            ok = [f for f in files if f.get("status") == "OK"]
            if not files:
                state = "미확보"
            elif not ok:
                state = "확보했지만 읽기 실패" if any(f["status"] == "READ_FAILED" for f in files) else "미확보"
            else:
                state = "확보·읽기 성공"
            doc = {"state": state, "attempts": entry.get("attempts", {}).get(kind, []),
                   "files": [{k: f.get(k) for k in ("filename", "source", "status", "format", "equation_count", "error")}
                             for f in files], "items": {}}
            # 파일명에 '공고문'이 들어 있어도 안내문(예: '[★필독] 공고문의 담당자 안내')은 공고문이 아니다
            misfiled = [f for f in ok if "안내" in f["filename"]]
            if misfiled:
                doc["misclassified"] = [f["filename"] for f in misfiled]
                ok = [f for f in ok if f not in misfiled]
                state = "확보·읽기 성공" if ok else "미확보(공고문 오분류)"
                doc["state"] = state
            target = ok[0] if ok else None
            blocks = None
            if target and target["path"]:
                try:
                    blocks = read_blocks(target["path"])
                except (OSError, ValueError):
                    pass
            flat = target.get("paragraphs") if target else None
            if target and blocks is None:  # PDF: 표 구조 없이 문단만
                blocks = [("p", line) for line in flat]
                doc["note"] = "표 구조 없이 저장된 문단 텍스트로만 찾음 (PDF이거나 원본 파일을 읽을 수 없음)"
            for item in ITEMS[kind]:
                if not target:
                    found = {"status": "파일 미확보나 읽기 실패로 확인 불가"}
                else:
                    found = find_item(item, blocks, flat, entry.get("srm"))
                    check = srm_value_check(item, blocks, entry.get("srm"))
                    if check:
                        found["srm_check"] = check
                doc["items"][item] = found
            res["docs"][kind] = doc
        out[no] = res
    return out


def main():
    ap = argparse.ArgumentParser(description="대표 공고 항목 위치·원문 발췌")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--db", help="수집기 SQLite DB (bidmate.sqlite3)")
    source.add_argument("--docs", help="verify_reference_documents.py 결과 JSON")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.db:
        docs = load_collected_documents(args.db)
        expected = {no: {"type": entry["srm"]["item_type"]} for no, entry in docs.items()}
        result = analyze(docs, expected)
    else:
        docs = json.loads(Path(args.docs).read_text(encoding="utf-8"))
        result = analyze(docs)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    tally = {}
    for r in result.values():
        for kind, d in r["docs"].items():
            for item, f in d["items"].items():
                tally[(kind, f["status"])] = tally.get((kind, f["status"]), 0) + 1
    print(json.dumps({f"{k[0]}:{k[1]}": v for k, v in tally.items()}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
