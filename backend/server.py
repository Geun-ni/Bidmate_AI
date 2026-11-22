import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from ai.summary import summarize_notice
from backend import notices_api
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
        elif url.path == "/api/notices" or url.path.startswith("/api/notices/"):
            self.handle_notices(url)
        else:
            super().do_GET()

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
