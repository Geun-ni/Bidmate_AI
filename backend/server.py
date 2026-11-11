import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from ai.summary import summarize_notice
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

    def do_GET(self):
        if urlsplit(self.path).path != "/api/demo":
            super().do_GET()
            return

        response = json.dumps(build_demo(), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(response)


def main():
    parser = argparse.ArgumentParser(description="입찰메이트 로컬 데모")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    handler = partial(DemoHandler, directory=str(ROOT / "frontend"))

    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"입찰메이트 http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
