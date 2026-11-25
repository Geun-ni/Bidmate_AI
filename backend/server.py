import argparse
import html
import os
import json
import mimetypes
from http.cookies import SimpleCookie
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ai.summary import summarize_notice
from ai import qna
from backend import auth, companies_api, favorites, notices_api, schedule, scoring, uploaded_criteria
from backend.matching import match_notice


ROOT = Path(__file__).resolve().parents[1]
DEMO_DATA = ROOT / "data" / "sample" / "demo.json"
DEV_PAGE = Path(__file__).with_name("dev_page.html")  # --dev 일 때만 /dev 로 연다 (API 확인용, 배포 서버에서는 끔)


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

    def send_json(self, status, payload, cookie=None):
        response = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if cookie is not None:  # 빈 문자열이면 쿠키 삭제 (로그아웃)
            self.send_header("Set-Cookie", f"{auth.COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Lax; "
                                           f"Max-Age={auth.SESSION_SECONDS if cookie else 0}")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(response)

    def send_html(self, body):
        body = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def juso_popup(self, method):
        """/dev 의 주소검색 팝업 중계 (행안부 도로명주소 팝업 API, 키: .env ADRRESS_API_KEY).
        GET → 행안부 주소검색 창으로 넘김, POST ← 행안부가 고른 주소를 돌려줌 → 부모 창 jusoCallBack 호출"""
        if method == "GET":
            key = auth._env("ADRRESS_API_KEY")
            if not key:
                self.send_html("<p>.env 에 ADRRESS_API_KEY 가 없습니다.</p>")
                return
            return_url = f"http://{self.headers.get('Host')}/dev/juso-popup"
            self.send_html(f"""<!doctype html><meta charset="utf-8"><title>주소 검색</title>
<form id="f" method="post" action="https://business.juso.go.kr/addrlink/addrLinkUrl.do">
<input type="hidden" name="confmKey" value="{html.escape(key)}">
<input type="hidden" name="returnUrl" value="{html.escape(return_url)}">
<input type="hidden" name="resultType" value="4"></form>
<script>document.getElementById('f').submit();</script>""")
            return
        length = int(self.headers.get("Content-Length") or 0)
        data = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
        payload = json.dumps(data, ensure_ascii=True).replace("</", "<\\/")
        self.send_html(f"""<!doctype html><meta charset="utf-8"><script>
if (window.opener && window.opener.jusoCallBack) window.opener.jusoCallBack({payload});
window.close();</script>""")

    def do_GET(self):
        url = urlsplit(self.path)
        dev = getattr(self.server, "dev_page", False)
        if url.path == "/dev" and dev:
            self.send_html(DEV_PAGE.read_bytes())
        elif url.path == "/dev/juso-popup" and dev:
            self.juso_popup("GET")
        elif url.path == "/api/demo":  # 가상 데이터 데모 (기본 화면 frontend/ 가 사용)
            self.send_json(200, build_demo())
        elif url.path == "/api/notices" or (url.path.startswith("/api/notices/") and len(url.path.split("/")) <= 5):
            self.handle_notices(url)
        elif url.path.startswith("/api/attachments/") and len(url.path.split("/")) == 4:
            self.handle_attachment(url.path.split("/")[3])
        elif url.path.startswith("/api/"):
            self.handle_api("GET", url)
        else:
            super().do_GET()

    def do_POST(self):
        url = urlsplit(self.path)
        if url.path == "/dev/juso-popup" and getattr(self.server, "dev_page", False):
            self.juso_popup("POST")
        else:
            self.handle_api("POST", url)

    def do_PUT(self):
        self.handle_api("PUT", urlsplit(self.path))

    def do_DELETE(self):
        self.handle_api("DELETE", urlsplit(self.path))

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def session_user(self, db):
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        return auth.user_from_token(db, cookie[auth.COOKIE].value) if auth.COOKIE in cookie else None

    def route(self, method, parts, query, user):
        """→ (함수, 로그인 필요 여부) 또는 None. 함수(db, body) → 결과(None 이면 404). GET 은 읽기 전용 DB"""
        q = lambda k: query.get(k, [""])[0]
        match (method, parts):
            case ("POST", ["api", "qna"]):
                return (lambda db, b: qna.answer(db, b, user), False)
            case ("GET", ["api", "auth", "me"]):                     # 로그인 안 했으면 {"user": null}
                return (lambda db, b: {"user": user}), False
            case ("POST", ["api", "notices", no, "score"]):          # 예상 점수 계산 (저장 안 함)
                # company_id 를 주면 공용 배점이 없을 때 그 업체가 올린 기준서로 계산 (소속 업체만)
                return (lambda db, b: scoring.calculate(
                    db, no, b.get("inputs") or {},
                    company_id=b.get("company_id") if b.get("company_id") and user and
                    auth.can_access_company(db, user, b.get("company_id")) else None)), False
            case ("PUT", ["api", "companies", cid, "uploaded-criteria", no]):  # 기준서 직접 올리기 (본문=파일)
                return (lambda db, b: uploaded_criteria.upload(db, cid, no, q("filename"), b, user)), True
            case ("GET", ["api", "companies", cid, "uploaded-criteria", no]):
                return (lambda db, b: uploaded_criteria.get(db, cid, no, user)), True
            case ("DELETE", ["api", "companies", cid, "uploaded-criteria", no]):
                return (lambda db, b: uploaded_criteria.remove(db, cid, no, user)), True
            case ("GET", ["api", "schedule"]) if q("favorites") in ("1", "true"):  # 내 관심공고 일정만 (로그인)
                return (lambda db, b: schedule.list_events(db, q("from") or None, q("to") or None,
                                                           user_id=user["user_id"])), True
            case ("GET", ["api", "schedule"]):                       # 달력: ?from=YYYY-MM-DD&to=YYYY-MM-DD
                return (lambda db, b: schedule.list_events(db, q("from") or None, q("to") or None)), False
            case ("GET", ["api", "favorites"]):                      # 내 관심공고 목록
                return (lambda db, b: favorites.list_favorites(db, user)), True
            case ("PUT", ["api", "favorites", no]):                  # 별표 켜기
                return (lambda db, b: favorites.add(db, user, no)), True
            case ("DELETE", ["api", "favorites", no]):               # 별표 끄기
                return (lambda db, b: favorites.remove(db, user, no)), True
            case ("GET", ["api", "reference", "regions"]):
                return (lambda db, b: companies_api.regions(db)), False
            case ("GET", ["api", "reference", "region-match"]):     # 주소 팝업 결과 → SRM 광역·시군구
                return (lambda db, b: companies_api.region_match(db, q("si"), q("sgg"))), False
            case ("GET", ["api", "reference", "licenses"]):
                return (lambda db, b: companies_api.licenses(db, q("q"))), False
            case ("GET", ["api", "reference", "qualifications"]):
                return (lambda db, b: companies_api.qualifications(db)), False
            case ("GET", ["api", "companies"]):                      # 내 업체 목록 (관리자는 전체)
                return (lambda db, b: companies_api.list_companies(db, user)), True
            case ("GET", ["api", "companies", "lookup"]):            # 가입 화면: 사업자번호로 합류할 업체 찾기
                return (lambda db, b: companies_api.lookup_by_brn(db, q("brn"))), False
            case ("GET", ["api", "companies", cid, "members"]):       # 소속·합류 요청 목록 (소유자·관리자)
                return (lambda db, b: companies_api.members(db, cid, user)), True
            case ("PUT", ["api", "companies", cid, "members", uid]):  # 합류 승인·거절 {"status": "ACTIVE"|"REJECTED"}
                return (lambda db, b: companies_api.set_member_status(db, cid, uid, b.get("status"), user)), True
            case ("POST", ["api", "companies"]):
                return (lambda db, b: companies_api.create_company(db, b, user)), True
            case ("GET", ["api", "companies", cid]):
                return (lambda db, b: companies_api.get_company(db, cid, user)), True
            case ("PUT", ["api", "companies", cid]):
                return (lambda db, b: companies_api.update_company(db, cid, b, user)), True
            case ("PUT", ["api", "companies", cid, "evaluation-drafts", no]):  # 계산 + 저장
                return (lambda db, b: companies_api.save_evaluation(db, cid, no, b.get("inputs") or {}, user)), True
            case ("GET", ["api", "companies", cid, "evaluation-drafts", no]):
                return (lambda db, b: companies_api.get_evaluation_draft(db, cid, no, user)), True
        return None

    def handle_auth(self, action, db, body):
        """POST /api/auth/signup · login · logout — 쿠키를 함께 보낸다"""
        if action == "logout":
            self.send_json(200, {"user": None}, cookie="")
            return
        try:
            user, token = auth.signup(db, body) if action == "signup" else auth.login(db, body)
        except auth.AuthError as exc:
            self.send_json(401, {"error": str(exc)})
            return
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
            return
        self.send_json(201 if action == "signup" else 200, {"user": user}, cookie=token)

    def handle_api(self, method, url):
        parts = [unquote(p) for p in url.path.strip("/").split("/")]
        raw = method == "PUT" and len(parts) == 5 and parts[3] == "uploaded-criteria"  # 파일 그대로 받음
        try:
            if method == "POST" and parts == ["api", "qna"]:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 <= length <= 65536:
                    self.send_json(400, {"error": "질문 요청이 너무 큽니다."})
                    return
            if raw:
                length = int(self.headers.get("Content-Length") or 0)
                if length > uploaded_criteria.MAX_BYTES:
                    self.send_json(400, {"error": "파일이 너무 큽니다 (최대 20MB)."})
                    return
                body = self.rfile.read(length)
            else:
                body = self.read_json() if method in ("POST", "PUT") else {}
        except ValueError:
            self.send_json(400, {"error": "JSON 형식이 아닙니다."})
            return
        is_auth = method == "POST" and len(parts) == 3 and parts[:2] == ["api", "auth"] and \
            parts[2] in ("signup", "login", "logout")
        try:
            db = companies_api.connect() if is_auth or method != "GET" else notices_api.connect()
        except notices_api.DatabaseMissing as exc:
            self.send_json(503, {"error": str(exc)})
            return
        try:
            if is_auth:
                self.handle_auth(parts[2], db, body)
                return
            user = self.session_user(db)
            found = self.route(method, parts, parse_qs(url.query), user)
            if found is None:
                self.send_json(404, {"error": "없는 경로입니다."})
                return
            func, need_login = found
            if need_login and user is None:
                self.send_json(401, {"error": "로그인이 필요합니다."})
                return
            result = func(db, body)
            if result is None:
                self.send_json(404, {"error": "찾을 수 없습니다."})
            else:
                self.send_json(201 if method == "POST" and parts[1] == "companies" else 200, result)
        except (companies_api.InputError, uploaded_criteria.UploadError) as exc:
            self.send_json(400, {"error": str(exc)})
        except (TypeError, ValueError, IndexError, KeyError) as exc:  # 입력값 형식 오류
            self.send_json(400, {"error": f"입력값을 확인하세요: {exc}"})
        except Exception as exc:
            self.send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            db.close()

    def handle_attachment(self, attachment_id):
        """GET /api/attachments/<첨부 id> — 수집기가 받아 둔 원본 파일 (공고 상세 attachments[].id)"""
        if not attachment_id.isdigit():
            self.send_json(404, {"error": "첨부를 찾을 수 없습니다."})
            return
        try:
            db = notices_api.connect()
        except notices_api.DatabaseMissing as exc:
            self.send_json(503, {"error": str(exc)})
            return
        try:
            found = notices_api.attachment_file(db, int(attachment_id))
        finally:
            db.close()
        if found is None:
            self.send_json(404, {"error": "서버에 저장된 파일이 없습니다."})
            return
        filename, data = found
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(filename)[0] or "application/octet-stream")
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
    parser.add_argument("--dev", action="store_true", help="API 확인 페이지 /dev 열기 (로컬 개발용)")
    args = parser.parse_args()
    handler = partial(DemoHandler, directory=str(ROOT / "frontend"))

    # Windows 는 SO_REUSEADDR 이면 같은 포트에 서버가 여러 개 떠도 에러가 안 나서 옛 서버가 응답한다 → 막는다
    ThreadingHTTPServer.allow_reuse_address = os.name != "nt"
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        server.dev_page = args.dev
        print(f"입찰메이트 http://127.0.0.1:{server.server_port}  (DB: {notices_api.DEFAULT_DB_PATH})", flush=True)
        if args.dev:
            print(f"API 확인 페이지 http://127.0.0.1:{server.server_port}/dev", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
