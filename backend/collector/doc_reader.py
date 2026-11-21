"""공고문·적격심사기준 원문 읽기 + 전처리 (HWP / HWPX / PDF).

본문은 사람이 읽는 순서대로 한 줄씩 만든다. 해석·요약은 하지 않는다(AI 파트).
  - 문단: 한 줄. 문단 안 줄바꿈·묶음 빈칸·탭은 띄어쓰기 한 칸으로 (띄어쓰기는 지우지 않음)
  - 표: '[표]' 줄 → 행마다 '칸1 | 칸2 | …' 한 줄 → '[표 끝]'. 칸 안의 여러 문단은 ' / ' 로 잇는다
  - 수식 개체: 나온 자리(문단 다음 줄 또는 표 칸 안)에 '[수식: 스크립트]'
  - 그림: 나온 자리에 '[이미지 BIN0001.bmp]' (원본 HWP 안의 그림 이름. extract_images 로 꺼낼 수 있음)
  - 글자로 친 분수(분자 줄 / 막대 줄 / 분모 줄)는 '(분자/분모)'로 합친다
  - HWP 전용 문자: 가로선·분수 막대는 지우고, 기호 글꼴 글머리는 일반 기호로 바꾼다
  - PDF: 페이지 텍스트를 줄 단위로 읽고, 문장 중간에서 끊긴 줄은 이어 붙인다 (표·수식 구조 없음)

read_document(filename, data) → (lines, equations)
  lines: 본문 줄 목록 (bid_document_content.text 는 '\n'.join(lines), paragraph_count 는 줄 수)
  equations: [{"script", "line", "context"}]  line = 그 수식이 들어 있는 본문 줄 번호(0부터)
"""
import io
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib

import olefile
from pypdf import PdfReader

PARSER_VERSION = "2"

TAG = {"PARA_TEXT": 67, "CTRL_HEADER": 71, "LIST_HEADER": 72, "TABLE": 77, "PICTURE": 85, "EQEDIT": 88}
# HWP 문단 안 제어문자: 2바이트짜리(문자형)와 16바이트짜리(인라인·확장 컨트롤)
CHAR_CONTROLS = {0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31}
AS_SPACE = {9: " ", 10: " ", 24: "-", 30: " ", 31: " "}  # 탭, 줄바꿈, 하이픈, 묶음 빈칸, 고정폭 빈칸

# 기호 글꼴(Wingdings 등) 글머리 → 일반 기호. 실제 문서 맥락으로 확인한 것만 (2026-11-21)
SYMBOLS = {"": "▪", "": "■", "": "•", "": "·", "": "·", "": "○"}
# 한컴 네모 숫자 U+F02B1~F02B9 → [1]~[9] (E012608001 공사내용 표에서 1·2·3 확인, 2026-11-21)
SYMBOLS.update({chr(0xF02B0 + n): f"[{n}]" for n in range(1, 10)})
# 가로선·분수 막대로 쓰인 HWP 전용 문자 (U+F000, U+F0800~F08FF), 발신란 도장 자리 U+F00E1
BAR = "[\U000F0800-\U000F08FF\U000F00E1]"
BAR_RUN = re.compile(BAR + "{2,}")
BAR_ANY = re.compile(BAR + "+")
SPACES = re.compile(r"[ \t　 ]+")
EQ_MARK = re.compile("\x01(\\d+)\x01")
EQ_SLOT = "\x02"  # HWP 문단 안 수식 개체 자리 (수식 레코드를 읽으면 \x01번호\x01 로 바뀜)


# ------------------------------------------------------------------ 텍스트 정리
def clean(text):
    text = text.replace(EQ_SLOT, " ")
    for k, v in SYMBOLS.items():
        text = text.replace(k, v)
    text = BAR_ANY.sub(" ", text)
    return SPACES.sub(" ", text).strip()


def _tokens(segment, k):
    """분자·분모 줄을 막대 개수(k)만큼 나눈다. 못 나누면 None."""
    s = segment.strip()
    if not s or BAR_ANY.search(s) or len(s) > 40:
        return None
    if k == 1:
        return [SPACES.sub(" ", s)]
    parts = re.split(r"\s{2,}", s)
    if len(parts) != k:
        parts = s.split()
    return parts if len(parts) == k else None


def join_fractions(segments):
    """[분자 줄, 막대 줄, 분모 줄] → '(분자/분모)'가 들어간 한 줄. 모양이 맞지 않으면 그대로 둔다."""
    out, i = [], 0
    while i < len(segments):
        seg = segments[i]
        runs = list(BAR_RUN.finditer(seg))
        is_formula = bool(BAR_ANY.sub("", seg).strip())  # 막대만 있는 줄은 제목 밑줄
        if runs and is_formula and out and i + 1 < len(segments):
            nums, dens = _tokens(out[-1], len(runs)), _tokens(segments[i + 1], len(runs))
            if nums and dens:
                for run, a, b in reversed(list(zip(runs, nums, dens))):
                    seg = f"{seg[:run.start()]}({a}/{b}){seg[run.end():]}"
                out[-1] = seg
                i += 2
                continue
        out.append(seg)
        i += 1
    return out


def _cell_text(segments):
    return " / ".join(t for t in (clean(s) for s in join_fractions(segments)) if t)


def render(blocks, scripts):
    """블록 → (lines, equations). 수식은 \\x01번호\\x01 자리표시를 '[수식: …]'로 바꾸며 줄 번호를 기록한다."""
    raw_lines = []
    pending = []  # 표 밖 문단·수식·그림은 연속 구간으로 모아 분수 합치기
    for kind, val in blocks + [("end", None)]:
        if kind in ("p", "eq", "img"):
            pending.append(val)
            continue
        raw_lines += [t for t in (clean(s) for s in join_fractions(pending)) if t]
        pending = []
        if kind == "table":
            rows = {}
            for r, c, segs in val["cells"]:
                rows.setdefault(r, {})[c] = _cell_text(segs)
            body = [" | ".join(rows[r][c] for c in sorted(rows[r])) for r in sorted(rows)]
            body = [b for b in body if b.replace("|", "").strip()]
            if body:
                raw_lines += ["[표]", *body, "[표 끝]"]
    lines, equations = [], []
    for line in raw_lines:
        def put(m):
            script = SPACES.sub(" ", scripts[int(m.group(1))]).strip()
            line_no = len(lines) if script else max(len(lines) - 1, 0)  # 빈 수식만 있던 줄은 지워지므로 앞 줄 기준
            equations.append({"script": scripts[int(m.group(1))], "line": line_no, "context": lines[-2:]})
            return f"[수식: {script}]" if script else ""  # 빈 수식 개체는 본문에 표시하지 않음
        line = EQ_MARK.sub(put, line)
        line = re.sub(r"(?: / \s*){2,}", " / ", line).strip(" /")  # 빈 수식이 빠진 자리의 겹친 구분자
        if line:
            lines.append(line)
    return lines, equations


# ------------------------------------------------------------------ HWP 5.0
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
    # 바이트로 모은 뒤 한 번에 디코딩 (2바이트씩 디코딩하면 U+F0832 같은 4바이트 문자가 깨진다)
    out, j = bytearray(), 0
    while j + 2 <= len(body):
        c = struct.unpack("<H", body[j:j + 2])[0]
        if c < 32:
            if c == 11 and body[j + 2:j + 6][::-1] == b"eqed":
                out += EQ_SLOT.encode("utf-16le")  # 수식 개체가 문장 안에 있던 자리
            out += AS_SPACE.get(c, "").encode("utf-16le")
            j += 2 if c in CHAR_CONTROLS else 16
        else:
            out += body[j:j + 2]
            j += 2
    return out.decode("utf-16le", "replace").strip()


def _open_hwp(data):
    ole = olefile.OleFileIO(io.BytesIO(data))
    compressed = bool(struct.unpack("<I", ole.openstream("FileHeader").read()[36:40])[0] & 1)
    return ole, compressed


def _hwp_bin_names(ole):
    names = {}
    for s in ole.listdir():
        if s[0] == "BinData":
            m = re.match(r"BIN([0-9A-Fa-f]{4})\.", s[-1])
            if m:
                names[int(m.group(1), 16)] = s[-1]
    return names


def hwp_blocks(data):
    """→ (blocks, scripts). blocks: ('p', 글) / ('eq', 자리표시) / ('img', 표시) / ('table', {cells: [row, col, [문단…]]})
    표 안의 표는 바깥 표의 칸 글로 합친다 (구조보다 내용 보존 우선)."""
    ole, compressed = _open_hwp(data)
    bins = _hwp_bin_names(ole)
    blocks, scripts, stack = [], [], []  # stack: [{'level', 'table', 'cell'}]
    slots = []  # 수식 자리가 남은 문단: [문단 레코드 level, 담긴 목록, 번호]

    def add(kind, text):
        """문단·수식·그림을 표 밖이면 blocks, 표 안이면 바깥 표의 현재 칸에 넣는다 → (목록, 번호)."""
        if stack:
            cell = stack[0]["cell"]
            if cell is None:
                return None
            cell[2].append(text)
            return cell[2], len(cell[2]) - 1
        blocks.append([kind, text])
        return blocks, len(blocks) - 1

    def text_at(where):
        box, i = where
        return box[i][1] if box is blocks else box[i]

    def set_text(where, text):
        box, i = where
        if box is blocks:
            box[i][1] = text
        else:
            box[i] = text

    for name in sorted("/".join(s) for s in ole.listdir() if s[0] == "BodyText"):
        raw = ole.openstream(name).read()
        raw = zlib.decompress(raw, -15) if compressed else raw
        for tag, level, body in _records(raw):
            while stack and level <= stack[-1]["level"]:
                stack.pop()
            if tag == TAG["CTRL_HEADER"] and body[:4][::-1] == b"tbl ":
                table = {"cells": []}
                stack.append({"level": level, "table": table, "cell": None})
                if len(stack) == 1:
                    blocks.append(("table", table))
            elif tag == TAG["LIST_HEADER"] and len(stack) == 1 and level == stack[0]["level"] + 1 and len(body) >= 16:
                col, row = struct.unpack("<HH", body[8:12])
                cell = [row, col, []]
                stack[0]["table"]["cells"].append(cell)
                stack[0]["cell"] = cell
            elif tag == TAG["PARA_TEXT"]:
                text = _para_text(body)
                if text.replace(EQ_SLOT, "").strip() or EQ_SLOT in text:
                    where = add("p", text)
                    if where:
                        slots += [[level, *where]] * text.count(EQ_SLOT)
            elif tag == TAG["EQEDIT"] and len(body) >= 6:
                n = struct.unpack("<H", body[4:6])[0]
                scripts.append(body[6:6 + n * 2].decode("utf-16le", "replace"))
                mark = f"\x01{len(scripts) - 1}\x01"
                # 수식 레코드는 자기 문단 글(PARA_TEXT)보다 한 단계 깊다 → 같은 문단의 첫 빈자리에 넣는다
                slot = next((s for s in slots if s[0] == level - 1), None)
                if slot:
                    slots.remove(slot)
                    set_text(slot[1:], text_at(slot[1:]).replace(EQ_SLOT, f" {mark} ", 1))
                else:
                    add("eq", mark)
            elif tag == TAG["PICTURE"] and len(body) >= 73:
                bin_id = struct.unpack("<H", body[71:73])[0]
                add("img", f"[이미지 {bins.get(bin_id, f'BIN{bin_id:04X}')}]")
    ole.close()
    return blocks, scripts


def hwp_images(data):
    """원본 HWP 안의 그림 → {이름: 바이트}. 본문의 '[이미지 이름]'과 같은 이름."""
    ole, compressed = _open_hwp(data)
    out = {}
    for s in ole.listdir():
        if s[0] == "BinData":
            raw = ole.openstream(s).read()
            try:
                out[s[-1]] = zlib.decompress(raw, -15) if compressed else raw
            except zlib.error:
                out[s[-1]] = raw
    ole.close()
    return out


# ------------------------------------------------------------------ HWPX
def _local(tag):
    return tag.rsplit("}", 1)[-1]


def hwpx_blocks(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    blocks, scripts = [], []

    def inline(item, sink):
        n = _local(item.tag)
        if n == "t":
            sink.append("".join(item.itertext()))
        elif n == "equation":
            scripts.append("".join(s.text or "" for s in item.iter() if _local(s.tag) == "script"))
            sink.append(f" \x01{len(scripts) - 1}\x01 ")
        elif n == "pic":
            ref = next((e.get("binaryItemIDRef") for e in item.iter() if e.get("binaryItemIDRef")), None)
            sink.append(f" [이미지 {ref or ''}] ".replace(" ]", "]"))

    def paragraphs_of(el):
        """칸 안 문단 목록 (칸 안의 표도 글로 풀어 넣음)."""
        out = []
        for p in (x for x in el.iter() if _local(x.tag) == "p"):
            parts = []
            for run in p:
                for item in run:
                    inline(item, parts)
            text = "".join(parts).strip()
            if text:
                out.append(text)
        return out

    def walk(el):
        for child in el:
            name = _local(child.tag)
            if name == "tbl":
                table = {"cells": []}
                for tc in (x for x in child.iter() if _local(x.tag) == "tc"):
                    addr = next((a for a in tc if _local(a.tag) == "cellAddr"), None)
                    row = int(addr.get("rowAddr", 0)) if addr is not None else 0
                    col = int(addr.get("colAddr", 0)) if addr is not None else 0
                    table["cells"].append([row, col, paragraphs_of(tc)])
                blocks.append(("table", table))
            elif name == "p":
                parts = []
                for run in child:
                    for item in run:
                        inline(item, parts)
                text = "".join(parts).strip()
                if text:
                    blocks.append(("p", text))
                walk(child)  # 문단 안의 표
            elif name not in ("t", "equation", "pic"):
                walk(child)

    for name in sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)):
        walk(ET.fromstring(z.read(name)))
    return blocks, scripts


# ------------------------------------------------------------------ PDF
LINE_END = re.compile(r"[.。:;)\]」』]$|(다|함|음|요|것|임|됨|바람)\.?$")
LINE_START = re.compile(r"^([0-9]+[.)]|[가-하][.)]|\(?[0-9가-하]{1,2}\)|[①-⑳]|[-•▪○□■※*▷◇◆◎●·]|제\s*\d+\s*조|\[)")


def pdf_lines(data):
    reader = PdfReader(io.BytesIO(data))
    raw = [clean(x) for p in reader.pages for x in (p.extract_text() or "").splitlines()]
    lines = []
    for line in (x for x in raw if x):
        if lines and not LINE_END.search(lines[-1]) and not LINE_START.match(line):
            lines[-1] = f"{lines[-1]} {line}"  # 문장 중간에서 끊긴 줄
        else:
            lines.append(line)
    return lines


# ------------------------------------------------------------------ 진입점
def detect_ext(filename, data):
    """확장자 + 파일 시그니처로 형식 확인 (확장자가 틀린 첨부 대비)."""
    if data[:8] == bytes.fromhex("d0cf11e0a1b11ae1"):
        return "hwp"
    if data[:2] == b"PK":
        return "hwpx"
    if data[:5] == b"%PDF-":
        return "pdf"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext


SIGNATURES = {"hwp": bytes.fromhex("d0cf11e0a1b11ae1"), "hwpx": b"PK", "pdf": b"%PDF-"}


def document_problem(filename, data):
    """받은 내용이 문서 파일이 아니면 이유를, 문서면 None.
    Open API 링크가 파일 대신 HTML 오류 페이지를 200으로 돌려주는 경우가 있다 (E012606980)."""
    if not data:
        return "빈 응답 (0 bytes)"
    head = data[:1024].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith((b"<!doctype html", b"<html")) or (head.startswith(b"<") and b"<html" in head):
        return f"파일 대신 HTML 응답 ({len(data)} bytes)"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext in SIGNATURES and not any(data.startswith(sig) for sig in SIGNATURES.values()):
        return f"확장자 {ext}인데 문서 형식이 아님 (앞부분 {data[:8].hex()}, {len(data)} bytes)"
    return None


def read_document(filename, data):
    """→ (lines, equations). 지원하지 않는 형식이면 ValueError."""
    ext = detect_ext(filename, data)
    if ext == "hwp":
        return render(*hwp_blocks(data))
    if ext == "hwpx":
        return render(*hwpx_blocks(data))
    if ext == "pdf":
        return pdf_lines(data), []
    raise ValueError(f"지원하지 않는 형식: {ext or '알 수 없음'}")
