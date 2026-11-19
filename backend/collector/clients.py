"""한전 Open API 클라이언트와 SRM 웹 내부 요청(/router) 클라이언트.

SRM /router 는 공식 외부 연동 API가 아니다. 연속 조회 시 차단(타임아웃·연결 끊김·HTTP 404)이
확인되었으므로 요청 사이 간격을 두고, 차단으로 보이면 SrmBlocked 를 올려 수집 주기를 멈춘다.
"""
import time
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

import requests

OPEN_API_URL = "https://bigdata.kepco.co.kr/openapi/v1/electContract.do"
SRM_BASE = "https://srm.kepco.net"
SEARCH = "smartsuit.ui.etnajs.spt.joinpublicannouncement.sp.JoinPublicAnnounceController"
DETAIL = "smartsuit.ui.etnajs.pro.rfx.sp.BidDetailController"
COMMON = "smartsuit.ui.etnajs.cmmn.CommonController"

# 범주 → SRM 메서드 (공개 공고 상세 화면이 보내는 요청과 동일)
RESTRICTION_METHODS = {
    "LICENSE": "findLicenseCodeData",
    "QUALIFIED": "findQualifiedCodeData",
    "REGION": "findRegionCodeData",
    "LICENSE_MAIN": "findLicenseMainCodeData",
}


def load_api_key(root):
    env = Path(root) / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("SRM_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("프로젝트 루트 .env 에 SRM_API_KEY=... 가 필요합니다.")


def fetch_open_api_notices(api_key, days=7, company_id="COM01"):
    """최근 days일(최대 90) 공고를 Open API에서 받는다."""
    end = date.today()
    start = end - timedelta(days=min(days, 90))
    res = requests.get(OPEN_API_URL, timeout=90, params={
        "companyId": company_id, "noticeBeginDate": start.strftime("%Y%m%d"),
        "noticeEndDate": end.strftime("%Y%m%d"), "apiKey": api_key, "returnType": "json"})
    res.raise_for_status()
    body = res.json()
    return body.get("data", []) if isinstance(body, dict) else []


class SrmBlocked(RuntimeError):
    """SRM이 요청을 막는 것으로 보일 때 (404, 연결 끊김, 타임아웃)."""


class _CsrfParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("name") in ("_csrf", "_csrf_header"):
            self.meta[a["name"]] = a.get("content", "")


def _records(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("data", "list", "rows", "items", "records"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


class SrmClient:
    def __init__(self, interval=3.0):
        self.interval = interval
        self._last = 0.0
        self._tid = 0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0",
                                     "Referer": SRM_BASE + "/index.do", "Origin": SRM_BASE})
        self._connect()

    def _wait(self):
        delay = self.interval - (time.monotonic() - self._last)
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()

    def _connect(self):
        self._wait()
        try:
            html = self.session.get(SRM_BASE + "/index.do", timeout=35).text
        except requests.RequestException as exc:
            raise SrmBlocked(f"SRM 접속 실패: {exc}") from exc
        p = _CsrfParser()
        p.feed(html)
        if not p.meta.get("_csrf") or not p.meta.get("_csrf_header"):
            raise SrmBlocked("SRM CSRF 토큰을 찾지 못했습니다.")
        self.session.headers.update({p.meta["_csrf_header"]: p.meta["_csrf"],
                                     "X-Requested-With": "XMLHttpRequest"})

    def rpc(self, action, method, data):
        """Ext.Direct 요청. 서버 예외는 RuntimeError, 차단 징후는 SrmBlocked."""
        self._wait()
        self._tid += 1
        payload = {"action": action, "method": method, "data": data, "tid": self._tid, "type": "rpc"}
        try:
            res = self.session.post(SRM_BASE + "/router", json=payload, timeout=35, headers={
                "Accept": "application/json, text/javascript, */*",
                "Content-Type": "application/json;charset=UTF-8"})
        except requests.RequestException as exc:
            raise SrmBlocked(f"{method}: {exc}") from exc
        if res.status_code in (403, 404, 429) or res.status_code >= 500:
            raise SrmBlocked(f"{method}: HTTP {res.status_code}")
        try:
            body = res.json()
        except ValueError as exc:
            raise SrmBlocked(f"{method}: JSON 아닌 응답") from exc
        item = next((v for v in body if v.get("tid") == self._tid), None) if isinstance(body, list) else body
        if not isinstance(item, dict) or item.get("type") == "exception" or "result" not in item:
            raise RuntimeError(f"{method}: 서버 오류 {str(item)[:200]}")
        return item["result"]

    def find_bid(self, notice_no, days=120):
        """공고번호 → SRM 검색 결과 행(내부 id, 지역명 등)."""
        end = date.today()
        query = {"companyId": "COM01",
                 "fromSearchDate": (end - timedelta(days=days)).isoformat() + "T00:00:00",
                 "toSearchDate": end.isoformat() + "T23:59:59", "searchDateType": "NoticeDateTime",
                 "searchType": "AnnounceNo", "searchText": notice_no,
                 "page": 1, "start": 0, "limit": 100, "totalCount": None}
        rows = [r for r in _records(self.rpc(SEARCH, "getPageList", [query])) if r.get("announceNo") == notice_no]
        return rows[0] if rows else None

    def basic_info(self, bid_id):
        return self.rpc(DETAIL, "findBidBasicInfo", [bid_id])

    def restriction(self, bid_id, category):
        arg = {"bidId": bid_id, "type": "Bid", "fileGroupId": "ConstructionBidFileGroup",
               "bidFileType": "Bid", "page": 1, "start": 0, "limit": 100}
        return _records(self.rpc(DETAIL, RESTRICTION_METHODS[category], [arg]))

    def attachments(self, group_id):
        return _records(self.rpc(COMMON, "getAttachments",
                                 [{"groupId": group_id, "page": 1, "start": 0, "limit": 100}]))

    def download(self, file_id):
        self._wait()
        try:
            res = self.session.get(SRM_BASE + "/downloadAttachment.do", params={"id": file_id}, timeout=90)
        except requests.RequestException as exc:
            raise SrmBlocked(f"downloadAttachment: {exc}") from exc
        if res.status_code != 200:
            raise SrmBlocked(f"downloadAttachment: HTTP {res.status_code}")
        return res.content


def download_open_api_file(url, timeout=90):
    """Open API 응답의 filenlink 다운로드 (http 링크는 https로 요청)."""
    res = requests.get(url.replace("http://", "https://", 1), timeout=timeout,
                       headers={"User-Agent": "Mozilla/5.0"})
    res.raise_for_status()
    return res.content
