import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from ai.summary import summarize_notice
from backend import companies_api, notices_api, schedule, scoring
from backend.matching import match_notice


ROOT = Path(__file__).resolve().parents[1]
DEMO_DATA = ROOT / "data" / "sample" / "demo.json"


def build_demo():
    data = json.loads(DEMO_DATA.read_text(encoding="utf-8"))
    profile = data["profile"]
    recommendations = []
    for notice in data["notices"]:
        reasons = match_notice(profile, notice)
        if reasons is None:
            continue
        recommendations.append({
            "id": notice["id"],
            "title": notice["title"],
            "issuer": notice["issuer"],
            "deadline": notice["deadline"],
            "budget": notice["budget"],
            "reasons": reasons,
            **summarize_notice(notice),
        })
    return {"profile": profile, "recommendations": recommendations}


class DemoHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
    }

    def send_json(self, status, payload):
        response = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(response)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/api/demo":  # 가상 데이터 데모 (기본 화면 frontend/ 가 사용)
            self.send_json(200, build_demo())
        elif url.path == "/api/notices" or (url.path.startswith("/api/notices/") and len(url.path.split("/")) <= 5):
            self.handle_notices(url)
        elif url.path.startswith("/api/"):
            self.handle_api("GET", url)
        else:
            super().do_GET()

    def do_POST(self):
        self.handle_api("POST", urlsplit(self.path))

    def do_PUT(self):
        self.handle_api("PUT", urlsplit(self.path))

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def route(self, method, parts, query):
        """→ (함수, 쓰기 DB 필요 여부) 또는 None. 함수(db, body) → 결과(None 이면 404)"""
        q = lambda k: query.get(k, [""])[0]
        match (method, parts):
            case ("POST", ["api", "notices", no, "score"]):          # 예상 점수 계산 (저장 안 함)
                return (lambda db, b: scoring.calculate(db, no, b.get("inputs") or {})), False
            case ("GET", ["api", "schedule"]):                       # 달력: ?from=YYYY-MM-DD&to=YYYY-MM-DD
                return (lambda db, b: schedule.list_events(db, q("from") or None, q("to") or None)), False
            case ("GET", ["api", "reference", "regions"]):
                return (lambda db, b: companies_api.regions(db)), False
            case ("GET", ["api", "reference", "licenses"]):
                return (lambda db, b: companies_api.licenses(db, q("q"))), False
            case ("GET", ["api", "reference", "qualifications"]):
                return (lambda db, b: companies_api.qualifications(db)), False
            case ("POST", ["api", "companies"]):
                return (lambda db, b: companies_api.create_company(db, b)), True
            case ("GET", ["api", "companies", cid]):
                return (lambda db, b: companies_api.get_company(db, cid)), False
            case ("PUT", ["api", "companies", cid]):
                return (lambda db, b: companies_api.update_company(db, cid, b)), True
            case ("PUT", ["api", "companies", cid, "evaluation-drafts", no]):  # 계산 + 저장
                return (lambda db, b: companies_api.save_evaluation(db, cid, no, b.get("inputs") or {})), True
            case ("GET", ["api", "companies", cid, "evaluation-drafts", no]):
                return (lambda db, b: companies_api.get_evaluation_draft(db, cid, no)), False
        return None

    def handle_api(self, method, url):
        parts = [unquote(p) for p in url.path.strip("/").split("/")]
        found = self.route(method, parts, parse_qs(url.query))
        if found is None:
            self.send_json(404, {"error": "없는 경로입니다."})
            return
        func, write = found
        try:
            body = self.read_json() if method in ("POST", "PUT") else {}
        except ValueError:
            self.send_json(400, {"error": "JSON 형식이 아닙니다."})
            return
        try:
            db = companies_api.connect() if write else notices_api.connect()
        except notices_api.DatabaseMissing as exc:
            self.send_json(503, {"error": str(exc)})
            return
        try:
            result = func(db, body)
            if result is None:
                self.send_json(404, {"error": "찾을 수 없습니다."})
            else:
                self.send_json(201 if method == "POST" and parts[1] == "companies" else 200, result)
        except companies_api.InputError as exc:
            self.send_json(400, {"error": str(exc)})
        except (TypeError, ValueError, IndexError, KeyError) as exc:  # 입력값 형식 오류
            self.send_json(400, {"error": f"입력값을 확인하세요: {exc}"})
        except Exception as exc:
            self.send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            db.close()

    def handle_notices(self, url):
        """GET /api/notices[?include_closed=1] · GET /api/notices/<공고번호>[/evaluation] — 수집기 DB 조회."""
        try:
            db = notices_api.connect()
        except notices_api.DatabaseMissing as exc:
            self.send_json(503, {"error": str(exc)})
            return
        try:
            if url.path == "/api/notices":
                query = parse_qs(url.query)
                include_closed = query.get("include_closed", ["0"])[0] in ("1", "true")
                keyword = query.get("q", [""])[0]
                self.send_json(200, notices_api.list_notices(db, include_closed, q=keyword))
                return
            parts = url.path.strip("/").split("/")  # ['api', 'notices', 공고번호(, 'evaluation')]
            if len(parts) == 4 and parts[3] == "evaluation":
                notice = notices_api.get_evaluation(db, unquote(parts[2]))
            elif len(parts) == 4 and parts[3] == "schedule":  # 공고 1건 일정 (날짜 없는 서류 제출기한 포함)
                notice = schedule.list_events(db, notice_no=unquote(parts[2]))
            elif len(parts) == 3:
                notice = notices_api.get_notice(db, unquote(parts[2]))
            else:
                self.send_json(404, {"error": "없는 경로입니다."})
                return
            if notice is None:
                self.send_json(404, {"error": "공고를 찾을 수 없습니다."})
            else:
                self.send_json(200, notice)
        except Exception as exc:  # DB 잠김 등: 화면에서 원인을 보여 줄 수 있게 JSON으로 응답
            self.send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            db.close()


def main():
    parser = argparse.ArgumentParser(description="입찰메이트 로컬 서버 (데모 화면 + 공고 조회 API)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    handler = partial(DemoHandler, directory=str(ROOT / "frontend"))

    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"입찰메이트 http://127.0.0.1:{server.server_port}  (DB: {notices_api.DEFAULT_DB_PATH})", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
