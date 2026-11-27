"""입찰메이트 MCP 서버 — Claude 같은 AI 앱이 입찰메이트 공고 데이터를 도구로 쓰게 한다.

공개 데이터만 연다 (공고·배점·원문). 회원·업체 정보는 다루지 않는다. LLM 은 부르지 않는다.
DB 는 백엔드와 같은 파일 (BIDMATE_DB → .env → 저장소 루트 bidmate.sqlite3).

    pip install -r bidmate_mcp/requirements.txt
    python -m bidmate_mcp.server                      # stdio (Claude 데스크톱 앱이 직접 띄움, 개발용)
    python -m bidmate_mcp.server --http --port 8001   # 원격 MCP (서버: nginx /mcp → 127.0.0.1:8001)

원격 MCP 는 nginx 가 넘겨 주는 Host 만 받는다 (MCP_ALLOWED_HOSTS, 쉼표로 여러 개).

원문 근거 도구는 처음 묻는 공고의 본문을 검색 색인(rag_chunk)에 넣는다 (챗봇과 같은 색인).
"""
import argparse
import os
from datetime import datetime, timedelta

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from ai import qna, rag_index
from backend import companies_api, notices_api

CONTACT = os.environ.get("MCP_CONTACT_EMAIL", "").strip()  # 사용자 문의처 (서비스 파일 Environment 로 지정)
ASK = f"{CONTACT} 로 문의" if CONTACT else "입찰메이트 사이트의 「의견 보내기」로 문의"

server = MCPServer(
    name="bidmate",
    instructions=(
        "입찰메이트: 한국전력공사(KEPCO) 전자입찰(SRM)의 적격심사 공사·용역 입찰공고 정보. "
        "여기 있는 공고는 모두 적격심사 대상이므로 공고명에 '적격심사'가 없어도 된다. "
        "공고 검색(search_notices·recent_notices) → 상세(get_notice)·배점(get_evaluation) → 원문 근거(find_evidence) 순으로 쓴다. "
        "금액은 원, 시각은 한국 시간. 답할 때 공고번호와 근거(원문 조항)를 함께 보여 준다. "
        "답변 원칙: 공고 내용(공고번호·금액·일정·참가 조건·배점·원문 근거)만 안내한다. 데이터를 모으는 방식, 서버, DB, API, 로그, "
        "개발·운영 작업 같은 입찰메이트 내부 사항은 말하거나 추측하지 않는다. "
        "모든 도구 응답의 data_as_of 는 입찰메이트 정보가 마지막으로 갱신된 시각이다. 질문한 기간이 그 이후이거나 결과가 비어 있으면 "
        "'입찰메이트 정보는 data_as_of 기준'이라고 알리고, 최신 공고는 한전 SRM(srm.kepco.net)에서 확인하도록 안내하며, "
        f"정보가 이상하면 {ASK}하도록 안내한다."))


def _with_meta(db, payload):
    """도구 응답에 정보 기준 시각·문의처를 붙인다"""
    run = notices_api.last_run(db) or {}
    as_of = (run.get("finished_at") or run.get("started_at") or "")[:16].replace("T", " ") or None
    return {**payload, "data_as_of": as_of, "contact": ASK}


@server.tool()
def search_notices(keyword: str = "", closing_within_days: int | None = None,
                   min_price: int | None = None, max_price: int | None = None, limit: int = 20) -> dict:
    """진행 중인 공고를 찾는다. keyword: 공고명·공고번호 일부. closing_within_days: 참가신청 마감이 N일 안.
    min_price·max_price: 추정가격(원) 범위. 참가신청 마감이 빠른 순."""
    db = notices_api.connect()
    try:
        rows = notices_api.list_notices(db, q=keyword)["notices"]
        meta = _with_meta(db, {})
    finally:
        db.close()
    if closing_within_days is not None:
        until = (datetime.now() + timedelta(days=closing_within_days)).strftime("%Y-%m-%d %H:%M:%S")
        rows = [r for r in rows if r["attend_req_close_at"] and r["attend_req_close_at"] <= until]
    if min_price is not None:
        rows = [r for r in rows if (r["presumed_price"] or 0) >= min_price]
    if max_price is not None:
        rows = [r for r in rows if r["presumed_price"] is not None and r["presumed_price"] <= max_price]
    return {**meta, "count": len(rows), "notices": rows[:max(1, min(limit, 100))]}


@server.tool()
def recent_notices(days: int = 1) -> dict:
    """최근 N일 안에 새로 공고된(공고일 기준) 진행 중 공고. '오늘 새로 뜬 공고' 같은 질문에 쓴다. 공고일이 최근인 순."""
    since = (datetime.now() - timedelta(days=max(0, min(days, 30)))).strftime("%Y%m%d")
    db = notices_api.connect()
    try:
        rows = notices_api.list_notices(db)["notices"]
        dates = dict(db.execute("SELECT notice_no, notice_date FROM bid_notice WHERE notice_date >= ?", (since,)))
        meta = _with_meta(db, {})
    finally:
        db.close()
    rows = sorted(({**r, "notice_date": dates[r["notice_no"]]} for r in rows if r["notice_no"] in dates),
                  key=lambda r: (r["notice_date"], r["notice_no"]), reverse=True)
    return {**meta, "since": since, "count": len(rows), "notices": rows}


@server.tool()
def get_notice(notice_no: str) -> dict:
    """공고 1건 상세: 발주부서, 추정가격·A값·사정액, 일정, 면허·지역 등 참가 제한 조건, 첨부 문서."""
    db = notices_api.connect()
    try:
        notice = notices_api.get_notice(db, notice_no.strip())
        return _with_meta(db, notice or {"error": f"공고 {notice_no} 를 찾을 수 없습니다."})
    finally:
        db.close()


@server.tool()
def get_evaluation(notice_no: str) -> dict:
    """공고 1건의 적격심사 배점표: 항목별 배점, 산식, 통과점수. 규칙으로 못 읽은 항목은 '확인 필요'."""
    db = notices_api.connect()
    try:
        evaluation = notices_api.get_evaluation(db, notice_no.strip())
        return _with_meta(db, evaluation or {"error": f"공고 {notice_no} 의 배점을 찾을 수 없습니다."})
    finally:
        db.close()


@server.tool()
def find_evidence(notice_no: str, question: str) -> dict:
    """공고문·적격심사기준 원문에서 질문과 관련된 조항을 최대 3개 찾는다 (예: '실적증명서 제출', '공동수급').
    해석은 하지 않고 원문 조각과 위치(파일·조항·줄)만 준다."""
    db = companies_api.connect()  # 색인을 만들거나 갱신하므로 쓰기 가능한 연결
    try:
        rows = rag_index.search(db, notice_no.strip(), qna.search_terms(question))
        meta = _with_meta(db, {})
    finally:
        db.close()
    return {**meta, "notice_no": notice_no, "evidence": [
        {"file": r["filename"], "section": r["section_path"], "lines": f"{r['line_start']}–{r['line_end']}",
         "text": r["body"]} for r in rows]}


def main():
    ap = argparse.ArgumentParser(description="입찰메이트 MCP 서버")
    ap.add_argument("--http", action="store_true", help="원격 MCP (streamable HTTP). 없으면 stdio")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()
    if not args.http:
        server.run("stdio")
        return
    hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "211-233-216-208.sslip.io").split(",") if h.strip()]
    server.run("streamable-http", host="127.0.0.1", port=args.port, streamable_http_path="/mcp",
               stateless_http=True, json_response=True,
               transport_security=TransportSecuritySettings(
                   allowed_hosts=hosts + ["127.0.0.1:*", "localhost:*"], allowed_origins=[]))


if __name__ == "__main__":
    main()
