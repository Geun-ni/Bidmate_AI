"""적격심사기준 HWP/HWPX/PDF에서 수식(산식)과 관련 본문을 추출하는 PoC.

한글 문서의 산식은 이미지가 아니라 '수식 개체'로 저장되는 경우가 많아
일반 텍스트 추출에서는 빠진다. 이 스크립트는 수식 스크립트를 직접 읽는다.
  - HWP(5.0, 바이너리): BodyText 레코드 중 HWPTAG_EQEDIT(88)의 수식 스크립트
  - HWPX(zip+XML): Contents/section*.xml 의 <hp:equation><hp:script>
  - PDF: 텍스트만 추출(산식 숫자가 소실될 수 있어 참고용)

설치:  python -m pip install olefile pypdf
실행:  python docs/planning/poc/extract_all.py <파일 또는 폴더> [...] [--json out.json]
예시:  python docs/planning/poc/extract_all.py downloads/E012607925
"""
import argparse
import json
import re
import struct
import sys
import zipfile
import zlib
from pathlib import Path

try:
    import olefile
    from pypdf import PdfReader
except ImportError:
    sys.exit("필요 패키지 설치: python -m pip install olefile pypdf")

PRICE_WORDS = re.compile(r"입찰가격|예정가격|투찰|기준금액|추정가격")
FORMULA_LINE = re.compile(r"(평점|평가산식|평점산식|산식)\s*[=＝(]|평점\s*=")
HWPTAG_PARA_TEXT, HWPTAG_EQEDIT = 67, 88
# HWP 문단 텍스트의 제어문자: 아래는 2바이트(char), 나머지 0~31은 16바이트(inline/extended)
CHAR_CONTROLS = {0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31}


def _records(data):
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


def _para_text(body):
    # 제어문자를 건너뛰고 나머지 UTF-16LE 바이트를 모아 한 번에 디코딩(서로게이트 쌍 보존)
    out, j = bytearray(), 0
    while j + 2 <= len(body):
        c = struct.unpack("<H", body[j:j + 2])[0]
        if c < 32:
            j += 2 if c in CHAR_CONTROLS else 16
        else:
            out += body[j:j + 2]
            j += 2
    return out.decode("utf-16le", "replace").strip()


def read_hwp(path):
    ole = olefile.OleFileIO(str(path))
    compressed = bool(struct.unpack("<I", ole.openstream("FileHeader").read()[36:40])[0] & 1)
    equations, paragraphs = [], []
    sections = sorted("/".join(s) for s in ole.listdir() if s[0] == "BodyText")
    for name in sections:
        data = ole.openstream(name).read()
        if compressed:
            data = zlib.decompress(data, -15)
        for tag, body in _records(data):
            if tag == HWPTAG_PARA_TEXT:
                text = _para_text(body)
                if text:
                    paragraphs.append(text)
            elif tag == HWPTAG_EQEDIT and len(body) >= 6:
                length = struct.unpack("<H", body[4:6])[0]
                script = body[6:6 + length * 2].decode("utf-16le", "replace")
                equations.append({"script": script, "context": paragraphs[-2:]})
    images = [s for s in ("/".join(x) for x in ole.listdir()) if s.startswith("BinData")]
    return equations, paragraphs, images


def _unescape(s):
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")


def read_hwpx(path):
    z = zipfile.ZipFile(path)
    equations, paragraphs = [], []
    sections = sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n))
    pattern = re.compile(r"<hp:t(?:\s[^>]*)?>(.*?)</hp:t>|<hp:equation\b.*?<hp:script>(.*?)</hp:script>", re.S)
    for name in sections:
        xml = z.read(name).decode("utf-8")
        for m in pattern.finditer(xml):
            if m.group(2) is not None:
                equations.append({"script": _unescape(m.group(2)), "context": paragraphs[-2:]})
            else:
                text = _unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
                if text:
                    paragraphs.append(text)
    images = [n for n in z.namelist() if n.startswith("BinData/")]
    return equations, paragraphs, images


def read_pdf(path):
    reader = PdfReader(str(path))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    images = [f"{sum(len(p.images) for p in reader.pages)} images / {len(reader.pages)} pages"]
    return [], paragraphs, images


READERS = {".hwp": read_hwp, ".hwpx": read_hwpx, ".pdf": read_pdf}


def analyze(path):
    equations, paragraphs, images = READERS[path.suffix.lower()](path)
    for eq in equations:
        eq["price_related"] = bool(PRICE_WORDS.search(eq["script"]))
    # 수식 개체 없이 본문 텍스트로 쓰인 산식(예: "*평점=50-2×｜( ... )×100｜")
    text_formulas = [p for p in paragraphs if FORMULA_LINE.search(p) and len(p) < 300]
    return {
        "file": path.name,
        "format": path.suffix.lower().lstrip("."),
        "equation_count": len(equations),
        "price_equation_count": sum(e["price_related"] for e in equations),
        "equations": equations,
        "text_formulas": text_formulas,
        "images": images,
    }


def safe(s):
    return s.encode("utf-8", "replace").decode("utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", help="HWP/HWPX/PDF 파일 또는 폴더")
    ap.add_argument("--json", help="결과를 JSON 파일로 저장")
    args = ap.parse_args()

    files = []
    for p in map(Path, args.paths):
        files += sorted(f for f in p.iterdir() if f.suffix.lower() in READERS) if p.is_dir() else [p]

    results = []
    for f in files:
        try:
            r = analyze(f)
        except Exception as exc:  # 손상·암호화 파일 등은 건너뛰고 계속 처리
            print(f"\n##### {f.name}: 오류 {exc}")
            results.append({"file": f.name, "error": str(exc)})
            continue
        results.append(r)
        print(f"\n##### {f.name} | 수식 {r['equation_count']}개 (가격 관련 {r['price_equation_count']}개) | 이미지 {r['images'][:3]}")
        for eq in r["equations"]:
            mark = "PRICE" if eq["price_related"] else "eq   "
            print(f"  {mark} {safe(eq['script'].strip())[:200]}")
            print(f"        ← {safe(' / '.join(eq['context']))[-120:]}")
        for line in r["text_formulas"]:
            print(f"  TEXT  {safe(line)[:200]}")

    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n저장: {args.json}")


if __name__ == "__main__":
    main()
