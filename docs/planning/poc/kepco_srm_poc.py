"""한전 SRM 공개공고 첨부파일 다운로드 가능성 검증(PoC).
실행: python -m pip install requests
      python docs/planning/poc/kepco_srm_poc.py
공개 화면에서 확인한 비공식 내부 요청을 사용하므로 운영용 API가 아닙니다.
"""
import argparse
import json
import re
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

import requests

BASE = "https://srm.kepco.net"
SEARCH = "smartsuit.ui.etnajs.spt.joinpublicannouncement.sp.JoinPublicAnnounceController"
DETAIL = "smartsuit.ui.etnajs.pro.rfx.sp.BidDetailController"
COMMON = "smartsuit.ui.etnajs.cmmn.CommonController"


class CsrfMetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            fields = dict(attrs)
            if fields.get("name") in ("_csrf", "_csrf_header"):
                self.meta[fields["name"]] = fields.get("content", "")


def configure_csrf(session, html):
    parser = CsrfMetaParser()
    parser.feed(html)
    token = parser.meta.get("_csrf")
    header = parser.meta.get("_csrf_header")
    if not token or not header:
        raise RuntimeError("index.do HTML에서 CSRF 토큰 또는 헤더 이름을 찾지 못했습니다.")
    session.headers.update({header: token, "X-Requested-With": "XMLHttpRequest"})
    print("  CSRF 토큰 자동 추출 성공 (값은 표시하지 않음)")


def rpc(session, action, method, data, tid):
    payload = {"action": action, "method": method, "data": data,
               "tid": tid, "type": "rpc"}
    res = session.post(BASE + "/router", json=payload, timeout=35,
                       headers={"Accept": "application/json, text/javascript, */*",
                                "Content-Type": "application/json;charset=UTF-8"})
    if res.status_code == 404:
        raise RuntimeError("SRM /router에서 HTTP 404가 반환됐습니다. 브라우저와 세션 또는 요청 정책 차이를 확인해야 합니다.")
    res.raise_for_status()
    try:
        body = res.json()
    except ValueError as exc:
        raise RuntimeError("RPC가 JSON 대신 다른 응답을 반환했습니다.") from exc
    item = next((v for v in body if v.get("tid") == tid), None) if isinstance(body, list) else body
    if not isinstance(item, dict) or item.get("type") == "exception" or item.get("error"):
        raise RuntimeError("RPC 오류: " + str(item)[:350])
    if "result" not in item:
        raise RuntimeError("RPC 결과(result)가 없습니다: " + str(item)[:350])
    return item["result"]


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def records(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("data", "list", "rows", "items", "records"):
            if isinstance(value.get(key), list):
                return value[key]
        for obj in walk(value):
            for child in obj.values():
                if isinstance(child, list) and child and isinstance(child[0], dict):
                    return child
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notice", default="E012607925")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0",
                            "Referer": BASE + "/index.do", "Origin": BASE})
    print("[1/5] 공개 페이지 접속")
    landing = session.get(BASE + "/index.do", timeout=35)
    landing.raise_for_status()
    configure_csrf(session, landing.text)

    print("[2/5] 공고번호 검색:", args.notice)
    end = date.today()
    start = end - timedelta(days=args.days)
    query = {"companyId": "COM01",
             "fromSearchDate": start.isoformat() + "T00:00:00",
             "toSearchDate": end.isoformat() + "T23:59:59",
             "searchDateType": "NoticeDateTime",
             "searchType": "AnnounceNo", "searchText": args.notice,
             "page": 1, "start": 0, "limit": 100, "totalCount": None}
    candidates = records(rpc(session, SEARCH, "getPageList", [query], 101))
    matches = [row for row in candidates if args.notice in json.dumps(row, ensure_ascii=False)]
    if len(matches) == 1:
        bid = matches[0]
    elif len(candidates) == 1:
        bid = candidates[0]
    else:
        raise RuntimeError("공고 검색 결과를 특정하지 못했습니다.")
    bid_id = bid.get("id") or bid.get("bidId")
    if not bid_id:
        raise RuntimeError("공고 ID를 찾지 못했습니다.")

    print("[3/5] 공고 상세정보 조회")
    detail = rpc(session, DETAIL, "findBidBasicInfo", [bid_id], 102)
    group_id = next((obj["attachFileGroupId"] for obj in walk(detail)
                     if isinstance(obj.get("attachFileGroupId"), str)
                     and obj["attachFileGroupId"]), None)
    if not group_id:
        raise RuntimeError("attachFileGroupId를 찾지 못했습니다.")

    print("[4/5] 전체 첨부파일 목록 조회")
    attachments = records(rpc(session, COMMON, "getAttachments",
                              [{"groupId": group_id, "page": 1, "start": 0, "limit": 100}], 103))
    for attachment in attachments:
        print(" -", attachment.get("name", "(이름 없음)"))
    # 파일명에 공고문 또는 적격심사가 포함된 모든 첨부파일을 선택합니다.
    keywords = ("공고문", "적격심사")
    targets = [item for item in attachments
               if isinstance(item.get("name"), str)
               and any(word in re.sub(r"\\s+", "", item["name"]) for word in keywords)]
    if not targets:
        print("[안내] 공고문/적격심사 키워드가 포함된 첨부파일이 없습니다.")
        return

    print(f"[5/5] 대상 파일 {len(targets)}개 다운로드")
    folder = Path("downloads") / re.sub(r'[^0-9A-Za-z_-]', "_", args.notice)
    folder.mkdir(parents=True, exist_ok=True)
    success = 0
    for target in targets:
        filename = re.sub(r'[\\\\/:*?"<>|]', "_", target["name"])
        print(" - 다운로드 시도:", filename)
        try:
            if not target.get("id"):
                raise RuntimeError("다운로드 ID가 없습니다.")
            # id의 %2F 등을 직접 재인코딩하지 않습니다.
            res = session.get(BASE + "/downloadAttachment.do",
                              params={"id": target["id"]}, timeout=60)
            res.raise_for_status()
            data = res.content
            if "text/html" in res.headers.get("Content-Type", "").lower() or data.lstrip()[:20].lower().startswith((b"<html", b"<!doctype html")):
                raise RuntimeError("파일 대신 HTML이 반환됐습니다.")
            ext = Path(filename).suffix.lower()
            if ext == ".hwp" and not data.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
                raise RuntimeError("HWP 파일 형식이 아닙니다.")
            if ext == ".pdf" and not data.startswith(b"%PDF-"):
                raise RuntimeError("PDF 파일 형식이 아닙니다.")
            if ext == ".hwpx" and not data.startswith(b"PK"):
                raise RuntimeError("HWPX 파일 형식이 아닙니다.")
            expected = target.get("size")
            if isinstance(expected, int) and len(data) != expected:
                raise RuntimeError(f"파일 크기 불일치: 예상 {expected}, 실제 {len(data)}")
            destination = folder / filename
            destination.write_bytes(data)
            success += 1
            print(f"   [성공] {destination} ({len(data)} bytes)")
        except (requests.RequestException, RuntimeError) as exc:
            print(f"   [실패] {filename}: {exc}")
    print(f"[완료] {success}/{len(targets)}개 다운로드 성공")



if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, RuntimeError) as exc:
        print("[검증 실패]", exc)
        print("오류 메시지만 공유하세요. 쿠키나 API 키는 공유하지 마세요.")
        raise SystemExit(1)
