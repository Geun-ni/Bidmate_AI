# 입찰메이트 · Bidmate AI

업체의 면허, 지역, 금액대에 맞는 공공 입찰 공고를 찾고 공고 요약과 제출서류를 확인하는 팀 프로젝트입니다. 시작 버전은 가상 공고와 업체 프로필로 기획서의 P0 화면을 시연합니다.

## 로컬 실행

Python 3.10 이상이 필요합니다. 저장소 루트에서 아래 명령을 실행한 뒤 브라우저에서 `http://127.0.0.1:8000`을 엽니다. 서버 실행에는 별도 패키지 설치나 API 키가 필요 없습니다(수집에는 `.env`의 `SRM_API_KEY` 필요). 포트가 사용 중이면 `python -m backend.server --port 8080`처럼 변경할 수 있습니다.

```bash
python -m backend.server
```

1. 가상 업체의 면허·지역·금액대를 확인합니다.
2. 추천된 공고를 선택해 매칭 사유와 원문 예시가 연결된 요약을 확인합니다.
3. 업체가 보유한 서류를 기준으로 표시된 체크리스트에서 준비 여부를 바꿉니다. 체크 상태는 페이지를 새로고침하면 초기화됩니다.

`--dev`를 붙여 실행하면 `http://127.0.0.1:8000/dev`에 API 확인 페이지(로그인·업체·관심공고·점수 버튼과 응답 JSON)가 열립니다. 로컬 확인용이라 배포 서버에서는 붙이지 않습니다. 이 페이지의 주소 검색 버튼은 `.env`의 `ADRRESS_API_KEY`(행안부 도로명주소 팝업 API 개발키, 등록 URL `http://127.0.0.1:8000`)를 씁니다.

수집한 한전 공고를 읽는 API도 같은 서버에 있습니다(`bidmate.sqlite3`를 읽기 전용으로 사용, 수집은 [수집기 문서](backend/collector/README.md) 참고). 기본 화면(`frontend/`)에는 아직 연결하지 않았습니다.

| API | 내용 |
| --- | --- |
| `GET /api/notices` | 진행 중 한전 공사·용역 적격심사 공고 목록 (`?include_closed=1`이면 마감 포함) + 마지막 수집 실행 결과. 값이 없으면 `null` |
| `GET /api/notices/<공고번호>` | 기본정보 + 면허·적격자격 조건(조회 전·실패는 `status: "확인 필요"`) + SRM 상세(A금액·경비·개찰일시) |
| `POST /api/notices/<공고번호>/score` | 예상 점수 계산(저장 안 함). 본문 `{"inputs": {"bid_price", "planned_price", "management_method": "ratio"\|"credit", "<규칙 key>": 값}}` → 항목별 점수·미산정·산정 보류, 합계, 통과점수 비교. 입력 안 한 항목은 0점이 아니라 미산정 ([backend/scoring.py](backend/scoring.py) 맨 위 설명) |
| `GET /api/notices/<공고번호>/schedule` | 공고 일정(신청·투찰·개찰 + 적격심사서류 제출기한 원문 문구. 제출기한은 통보 후 정해져 날짜 없음) |
| `GET /api/schedule?from=YYYY-MM-DD&to=YYYY-MM-DD` | 달력용 기간 일정. `&favorites=1`이면 내 관심공고 일정만(로그인, 날짜 없는 제출기한 포함) |
| `POST /api/auth/signup` · `login` · `logout`, `GET /api/auth/me` | 이메일·비밀번호 로그인(세션 쿠키). `me`는 로그인 안 했으면 `{"user": null}` |
| `GET /api/favorites` · `PUT/DELETE /api/favorites/<공고번호>` | 관심공고(별표) 목록·켜기·끄기. 사용자 개인별, 마감 공고는 `closed: true`로 구분 (로그인) |
| `GET /api/reference/regions` · `licenses?q=` · `qualifications` | 업체 입력용 선택 목록 (SRM 광역·시군구, 면허, 제한경쟁입찰자격) |
| `GET /api/reference/region-match?si=시도명&sgg=시군구명` | 행안부 도로명주소 팝업에서 고른 주소의 시도·시군구 → SRM 광역·시군구 코드 (화면 표시용) |
| `GET/POST /api/companies` · `GET/PUT /api/companies/<업체ID>` | 내 업체 목록, 업체정보(기본정보·본점 소재지·면허·자격) 등록·조회·수정. 로그인 필요: 등록한 사람이 소유자가 되고, 소속 아닌 업체는 404. 본점 소재지는 주소 검색 팝업 결과만 받는다: `head_office_address`와 `juso: {siNm, sggNm}`을 같이 보내면 서버가 SRM 광역·시군구를 정하고 MATCH로 저장(지역 직접 지정·주소 직접 입력은 400). 관리자(`.env` `ADMIN_EMAIL`)는 전체. 1차 자격 판정은 AI 파트(LLM) 담당 |
| `GET /api/companies/lookup?brn=사업자등록번호` · 가입 때 `join_company_id` | 기존 업체 합류: 사업자번호로 업체를 찾아 가입하면 합류 요청(PENDING)이 되고, 소유자가 승인해야 업체 정보를 볼 수 있다. 내 업체 목록의 `join_requests`에 요청 상태가 나온다 |
| `GET /api/companies/<업체ID>/members` · `PUT …/members/<사용자ID>` | 소유자·관리자: 소속·합류 요청 목록, 승인(`{"status": "ACTIVE"}`)·거절(`REJECTED`) |
| `PUT/GET /api/companies/<업체ID>/evaluation-drafts/<공고번호>` | 점수 계산 후 저장 / 직전 계산 결과 조회 (업체·공고당 1건) |
| `GET /api/notices/<공고번호>/evaluation` | 적격심사 배점·산식 계층(`rules`: 통과점수·입찰가격 산식·수행능력 분야와 세부 표·가감점·결격) + 요약(`summary`). 배점이 없으면 `status: "확인 필요"`와 이유(기준서 없음, AI 분석 대기 등) |
| `GET /api/demo` | 가상 업체 프로필과 룰을 통과한 공고 목록 (기본 화면이 사용) |

## 데이터베이스 스키마

SQLite 스키마와 초기화 방법은 [백엔드 DB 문서](backend/db/README.md)에서 확인할 수 있습니다.

## 팀 구성과 역할

| 팀 | 담당 | 주요 업무 | GitHub 산출물 | 작업 브랜치 | 배정 이유 |
| --- | --- | --- | --- | --- | --- |
| 기획팀 (PM·도메인) | 곽영래 | 입찰 도메인 규칙 정의(입찰참가자격·적격심사 기준), 사용자 시나리오, 기능 명세, 일정 관리, 회의 진행 | `docs/planning`, 요구사항 명세, 룰 정의 문서, 회의록 | `docs/matching-rules` | 아이디어 제안자이자 입찰 절차를 가장 잘 알고 있음 |
| 디자인팀 (UI/UX·프론트) | 강보라 | 와이어프레임, 화면 디자인, 프론트엔드 구현, 홍보 콘텐츠 제작(README 배너·카드뉴스) | `docs/design`, 화면 시안, 프론트 코드, 홍보 자료 | `feat/notice-dashboard` | 대시보드·검색 화면 기획 경험 |
| 개발팀 (백엔드·데이터) | 이정근 | 공고 수집(Open API), DB 설계, 룰 엔진(자격 매칭·예상 점수), 백엔드 API, 테스트·배포 | `backend/`, DB 스키마, API 명세, 테스트 | `feat/notice-api` | DB·API·UI 연결 관점의 개발 설계 경험 |
| AI팀 (LLM·알림) | 조용희 | PDF 파싱·구조화 추출, 요약·RAG Q&A, 프롬프트 설계, 환각 검증, 이메일·텔레그램 알림 연동 | `ai/`, 프롬프트 문서, 평가 결과(추출 정확도) | `feat/pdf-extraction` | GPT API·알림 연동 구현 경험 |

## 팀원별 수정 위치

| 담당 | 위치 | 이어서 구현할 부분 |
| --- | --- | --- |
| 기획 | `docs/planning/`, `docs/decisions/` | 요구사항과 면허·지역·금액 매칭 규칙 정리 |
| 디자인·프론트 | `docs/design/`, `frontend/` | 시안 작성, 공고 탐색·상세 화면 개선 |
| 백엔드·데이터 | `backend/`, `scripts/`, `data/sample/demo.json` | 공고 수집·저장, 실제 자격 조건 검증, API 확장 |
| AI | `ai/summary.py` | PDF 파싱, 구조화 추출, 원문 근거 검증, 추천 사유 문장 생성 |
| 공통 | `docs/meetings/`, `docs/devlog/`, `docs/promotion/`, `.github/` | 공개용 회의록, 개발·홍보 기록, 이슈·PR 관리 |

백엔드·데이터 폴더의 역할은 아래와 같습니다.

| 위치 | 역할 |
| --- | --- |
| `backend/server.py`, `backend/notices_api.py`, `backend/matching.py` | 웹 서버, 수집한 공고 조회 API, 데모용 매칭 규칙 |
| `backend/collector/` | 한전 공고·첨부파일 수집, 첨부 본문 읽기(HWP·HWPX·PDF), 대표 공고 검증 스크립트 |
| `backend/db/` | SQLite 스키마(`schema/`), DB 초기화·기준정보 입력, 저장 데이터 정정·본문 재처리 도구 |
| `scripts/` | 매일 12시 자동 수집 실행과 Windows 예약 작업 등록 |
| `docs/devlog/data/` | 개발 일지에 쓴 검증 결과 원본(JSON) |

수집기를 실행하면 `bidmate.sqlite3`(수집 DB), `downloads/`(첨부 원본), `logs/`(수집 로그)가 로컬에 생깁니다. 팀 공유용 DB·CSV는 `dist/`에 만듭니다. 이 파일과 폴더는 저장소에 올리지 않습니다.

`GET /api/demo`는 가상 업체 프로필과 룰을 통과한 공고 목록을 반환합니다. 현재 금액 범위는 양 끝값을 포함하며, 공고에 등록된 모든 필수 면허와 허용 지역을 확인합니다. 요약과 체크리스트는 샘플에 작성된 원문 예시를 사용하고, 근거가 빠진 요약 항목은 `확인 필요`로 표시합니다. 샘플에서 공고를 추가하거나 업체 조건을 바꾼 뒤 페이지를 새로고침해 흐름을 확인할 수 있습니다.

관리자 계정은 `.env`에 `ADMIN_EMAIL=`을 적고 `python -m backend.auth create-admin`으로 만듭니다(비밀번호가 없으면 만들어 `.env`의 `ADMIN_PASSWORD`에 적고, 로그인 서명용 `SESSION_SECRET`도 함께 만듭니다). 비밀번호는 저장소·문서에 적지 않습니다.

공개 저장소에는 공개 가능한 자료와 가상 데이터만 넣고, API 키는 로컬 `.env`나 GitHub Secrets에서 관리합니다. 이 서비스는 참고용이며 법적 효력이 없습니다. 최종 확인은 공고 원문과 발주처 기준에 따릅니다.

[GitHub 저장소](https://github.com/Geun-ni/Bidmate_AI)
