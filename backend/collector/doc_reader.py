"""공고문·적격심사기준 원문 읽기 (HWP / HWPX / PDF).

한글 문서의 산식은 이미지가 아니라 '수식 개체'로 저장되는 경우가 많아 일반 텍스트 추출에서 빠진다.
본문 문단과 함께 수식 스크립트를 따로 읽어 반환한다. 해석·요약은 하지 않는다(AI 파트).
  - HWP 5.0: BodyText 레코드의 PARA_TEXT(67) 문단, EQEDIT(88) 수식 스크립트
  - HWPX: Contents/section*.xml 의 <hp:t> 문단, <hp:equation><hp:script> 수식
  - PDF: 페이지 텍스트 (산식 숫자가 소실될 수 있음)
"""
import io
import re
import struct
import zipfile
import zlib

import olefile
from pypdf import PdfReader

HWPTAG_PARA_TEXT, HWPTAG_EQEDIT = 67, 88
CHAR_CONTROLS = {0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31}  # 2바이트 제어문자, 나머지 0~31은 16바이트


def _hwp_records(data):
    i = 0
    while i + 4 <= len(data):
        header = struct.unpack("<I", data[i:i + 4])[0]
        i += 4
        tag, size = header & 0x3FF, (header >> 20) & 0xFFF
        if size == 0xFFF:
            size = struct.unpack("<I", data[i:i + 4])[0]
            i += 4
        yield tag, data[i:i + size]
        i += size


def _hwp_para_text(body):
    out, j = bytearray(), 0
    while j + 2 <= len(body):
        c = struct.unpack("<H", body[j:j + 2])[0]
        if c < 32:
            j += 2 if c in CHAR_CONTROLS else 16
        else:
            out += body[j:j + 2]
            j += 2
    return out.decode("utf-16le", "replace").strip()


def read_hwp(data):
    ole = olefile.OleFileIO(io.BytesIO(data))
    compressed = bool(struct.unpack("<I", ole.openstream("FileHeader").read()[36:40])[0] & 1)
    paragraphs, equations = [], []
    for name in sorted("/".join(s) for s in ole.listdir() if s[0] == "BodyText"):
        raw = ole.openstream(name).read()
        if compressed:
            raw = zlib.decompress(raw, -15)
        for tag, body in _hwp_records(raw):
            if tag == HWPTAG_PARA_TEXT:
                text = _hwp_para_text(body)
                if text:
                    paragraphs.append(text)
            elif tag == HWPTAG_EQEDIT and len(body) >= 6:
                n = struct.unpack("<H", body[4:6])[0]
                equations.append({"script": body[6:6 + n * 2].decode("utf-16le", "replace"),
                                  "context": paragraphs[-2:]})
    return paragraphs, equations


def _unescape(s):
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")


def read_hwpx(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    paragraphs, equations = [], []
    pattern = re.compile(r"<hp:t(?:\s[^>]*)?>(.*?)</hp:t>|<hp:equation\b.*?<hp:script>(.*?)</hp:script>", re.S)
    for name in sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)):
        xml = z.read(name).decode("utf-8")
        for m in pattern.finditer(xml):
            if m.group(2) is not None:
                equations.append({"script": _unescape(m.group(2)), "context": paragraphs[-2:]})
            else:
                text = _unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
                if text:
                    paragraphs.append(text)
    return paragraphs, equations


def read_pdf(data):
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    return [line.strip() for line in text.splitlines() if line.strip()], []


READERS = {"hwp": read_hwp, "hwpx": read_hwpx, "pdf": read_pdf}


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
    """→ (paragraphs, equations). 지원하지 않는 형식이면 ValueError."""
    ext = detect_ext(filename, data)
    if ext not in READERS:
        raise ValueError(f"지원하지 않는 형식: {ext or '알 수 없음'}")
    return READERS[ext](data)
