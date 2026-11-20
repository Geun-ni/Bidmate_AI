# 입찰메이트 DB 스키마 설명서 (v1.1)

> 담당: 이정근 (개발팀 · DB 설계) · 2026-11-19  
> 기준 문서: [기능명세서 §5](../../docs/planning/기능명세서.md), [업체정보 데이터 정의서 v0.2](../../docs/planning/업체정보_데이터_정의서.md), 11-18 회의, 11-19 결정(적격심사 공고만, 매칭은 아직 범위 밖)

SQLite 하나(`bidmate.sqlite3`)에 테이블 28개 + 뷰 1개. 업무 영역별로 SQL 파일을 나눴고, **초기화 진입점은 `init_db.py` 하나**다.

## 1. 초기화 순서와 실행 명령

```bash
# 저장소 루트에서
python -m backend.db.init_db                          # ① 스키마 적용 (01 → 07 순서)
python -m backend.db.seed_reference                   # ② 기준정보 시드 (스키마도 함께 적용)
python -m pip install -r backend/collector/requirements.txt
python -m backend.collector.collect --dry-run         # ③ 수집 대상 확인 (요구: .env SRM_API_KEY)
python -m backend.collector.collect --limit 5         # ④ 공고 수집
```

- 모든 명령의 기본 DB는 저장소 루트 `bidmate.sqlite3`(`.gitignore` 제외), `--db 경로`로 변경.
- 코드에서 DB를 열 때는 `sqlite3.connect` 대신 아래를 쓴다. 외래키 검사(`PRAGMA foreign_keys = ON`)와 스키마 적용이 함께 된다.
  ```python
  from backend.db.init_db import DEFAULT_DB_PATH, init_db
  db = init_db(DEFAULT_DB_PATH)
  ```
- **다시 실행해도 안전:** 테이블은 `CREATE TABLE IF NOT EXISTS`라 기존 데이터가 유지되고, 뷰(07)만 지웠다가 다시 만든다(데이터 없음). 시드는 UPSERT라 여러 번 실행해도 같다.
- **컬럼 추가는 `init_db.py`의 `COLUMN_MIGRATIONS`에도 등록한다.** `CREATE TABLE IF NOT EXISTS`는 이미 있는 테이블에 새 컬럼을 더하지 않으므로, SQL 파일에 컬럼을 추가하면 같은 정의를 이 목록에 넣어 기존 DB에도 `ALTER TABLE ADD COLUMN`이 적용되게 한다. 현재 등록: `bid_notice`의 정정 감지 컬럼 5개.

## 2. 파일 구조 (적용 순서 = 외래키·뷰 의존 순서)

| 파일 | 영역 | 테이블 | 참조하는 파일 |
|---|---|---|---|
| `schema/01_reference.sql` | A. 기준정보 | `license_master`, `restricted_qualification_master`, `restricted_qualification_equivalence`, `license_equivalence`, `srm_region_group_master`, `srm_region_district_master` | — |
| `schema/02_company.sql` | B. 업체 | `company`, `company_license`, `company_restricted_qualification`, `company_notification_setting` | 01 |
| `schema/03_bid_collection.sql` | C. 공고·첨부·수집 이력 | `bid_notice`, `bid_restriction_snapshot`, `bid_restriction_category`, `bid_restriction_item`, `bid_attachment`, `bid_document_content`, `collect_run` | — |
| `schema/04_document_analysis.sql` | D. AI 문서 분석 | `notice_analysis_cache`, `document_extraction` | 03 |
| `schema/05_evaluation.sql` | E. 자격 판정·점수 | `company_bid_eligibility`, `evaluation_rule`, `company_evaluation_draft` | 02, 03 |
| `schema/06_notice_workflow.sql` | F. 필요서류·즐겨찾기·일정·알림 | `required_document_item`, `document_template_item`, `company_document_check`, `favorite_notice`, `notice_schedule_event`, `notice_notification` | 02, 03 |
| `schema/07_views.sql` | 화면 조회 뷰 | `v_recommendation` (뷰) | 03, 05, 06 |
| `init_db.py` | 초기화 진입점 | `schema/[0-9][0-9]_*.sql`을 파일명 순서로 적용 | — |
| `seed_reference.py` | 기준정보 시드 | 01의 테이블 (참고 엑셀 + 정의서) | — |

`company_notification_setting`은 업체가 소유하는 설정이라 02에 두었다(06에는 발송 이력만).

## 3. 테이블별 쓰는 모듈 / 데이터 출처 / 읽는 모듈 (코드 확인 결과, 2026-11-19)

저장소 전체를 확인한 결과 **DB를 실제로 쓰는 코드는 `seed_reference.py`와 `backend/collector/collect.py` 두 개뿐**이다. `ai/summary.py`, `backend/server.py`(`/api/demo`만 있음, `data/sample/demo.json` 사용), `backend/matching.py`, `frontend/app.js`에는 SQL·DB 접근이 없다. 아래에서 **예정** = 아직 코드 없음, **팀 결정 필요** = 저장 주체를 정하지 않음.

| 테이블 | 직접 쓰는 모듈 | 데이터 출처 | 읽는 모듈 |
|---|---|---|---|
| `license_master`, `restricted_qualification_master`, `srm_region_group_master`, `srm_region_district_master`, `restricted_qualification_equivalence` | `seed_reference.py` ✅ | 참고 엑셀 3종, 업체정보 정의서 §5.3·§6.2 | 예정: 업체정보 API, 룰 엔진 |
| `license_equivalence` | 예정: 시드 (감리 전력종합 → 전력전문) | 팀 정리본 (대표 공고 감리용역) | 예정: 룰 엔진 |
| `company`, `company_license`, `company_restricted_qualification` | 예정: 업체정보 API ← 프론트 업체정보 입력(UI-02) | 사용자 입력 (가상 업체만) | 예정: 룰 엔진 |
| `company_notification_setting` | 예정: 알림 설정 API ← 프론트 알림 설정(UI-07) | 사용자 입력 | 예정: 알림 발송 모듈 |
| `bid_notice` | `collect.py` ✅ (`upsert_notice`, SRM 금액·개찰일·정정 차수 갱신, 정정 감지 표시) | Open API + SRM `findBidBasicInfo` | `collect.py`(재수집 여부), 07 뷰, 예정: 공고 API |
| `bid_restriction_snapshot`, `bid_restriction_category`, `bid_restriction_item` | `collect.py` ✅ (`collect_srm`) | SRM `/router` 투찰제한 | `collect.py`(`is_done`), 예정: 룰 엔진·요약 탭 API |
| `bid_attachment` | `collect.py` ✅ (`save_attachment`) | Open API `filenlink` / SRM 첨부 | 예정: AI 문서 분석, 공고 상세 API |
| **`bid_document_content`** | `collect.py` ✅ (`save_content`) — 해석 없이 원문만 | 다운로드한 HWP/HWPX/PDF | 예정: AI 파트 (LLM 입력) |
| `collect_run` | `collect.py` ✅ | 수집 실행 결과 | 운영 확인용 |
| `notice_analysis_cache` | **팀 결정 필요** (AI 파트 직접 vs 백엔드 API) | AI 분석 상태 | 예정: 수집기·AI 파트 재분석 판단 |
| **`document_extraction`** | **팀 결정 필요** (AI 파트가 직접 저장 vs 백엔드가 검증 후 저장) | AI 파트 LLM 추출 (`bid_document_content` 기반) | 예정: 요약 탭 API, 룰 엔진 |
| `company_bid_eligibility` | 예정: 룰 엔진 (업체 매칭은 현재 범위 밖) | `bid_restriction_*` + 업체 정보 | 07 뷰 |
| `evaluation_rule` | **팀 결정 필요** (AI 직접 저장 vs 룰 엔진이 산식·숫자 검증 후 저장) | AI 추출값 + 수식 스크립트 | 예정: 점수 계산 탭 API |
| `company_evaluation_draft` | 예정: 점수 계산 API ← 프론트 점수 계산 탭(UI-04) | 사용자 입력(입찰가격·예정가격 등) + 계산 결과 | 예정: 점수 계산 탭 API |
| **`required_document_item`** | **팀 결정 필요** (AI 직접 vs 백엔드 검증 후) | AI 추출 (공고문·적격심사기준) | 예정: 필요서류 탭 API |
| `document_template_item` | 예정: 시드 (팀 정리본 1~7번) | 팀 정리본 | 예정: 필요서류 추출 비교 |
| **`company_document_check`** | 예정: 서류 체크 API ← 프론트 필요서류 탭(UI-05). 현재 프론트는 브라우저 메모리(`Map`)에만 보관 | 사용자 체크 | 예정: 필요서류 탭 API |
| `favorite_notice` | 예정: 관심공고 API ← 프론트 별표 토글(UI-01·06) | 사용자 클릭 | 07 뷰, 예정: 달력 |
| `notice_schedule_event` | **팀 결정 필요** (수집기가 API·SRM 일정 저장 + 문서 추출 제출기한은 누가?) | Open API·SRM 일정, 적격심사기준 제8조 ② | 예정: 달력 API |
| **`notice_notification`** | 예정: 알림 발송 모듈 — **담당 팀 결정 필요** (기획서상 알림 연동은 AI 팀) | 판정 결과 + 알림 설정 | 알림 발송 모듈(중복 방지) |
| `v_recommendation` (뷰) | — | 03·05·06 조인 | 예정: 추천 목록 API |

**화면 → 백엔드 API (전부 미구현, 이름은 제안):** 업체정보 `PUT /api/companies/{company_id}`, 알림 설정 `PUT /api/companies/{company_id}/notification-setting`, 관심공고 `PUT/DELETE /api/companies/{company_id}/favorites/{notice_no}`, 서류 체크 `PUT /api/companies/{company_id}/document-checks/{item_id}`, 점수 계산 `PUT /api/companies/{company_id}/evaluation-drafts/{notice_no}`, 추천 목록 `GET /api/companies/{company_id}/recommendations`.

## 4. 이름 규칙

- 테이블·컬럼: 소문자 `snake_case`, 테이블명은 단수형, 데이터 의미가 드러나게 (`company_license`, `bid_attachment`).
- 기준정보 테이블은 업체정보 정의서 이름(`*_master`, `*_equivalence`)을 따른다.
- **키:** 기존 방식 우선.
  - 공고는 외부 고유 코드 `notice_no`(공고번호)를 PK·FK로 쓴다 (코드 키 유지).
  - 업체 계열 PK는 정의서 이름(`company_id`, `company_license_id` 등, UUID 문자열)을 유지한다.
  - 새로 만든 단일 내부 PK는 `id`(INTEGER) — `bid_attachment.id`, `bid_restriction_item.id` 등.
  - 새 외래키는 참조 대상을 드러내는 이름을 쓴다(아래 변경 제안 참고).
- **코드값은 전부 TEXT:** 면허 `0041`, 자격 `1`, 시군구 `011013`. 참고 엑셀 `지역제한 목록.xlsx`는 시군구 162건이 숫자로 저장돼 0이 빠져 있어(`11013`) `seed_reference.py`가 6자리로 복원한다.
- 상태값은 대문자 열거형 + CHECK 제약(`MATCH/MISMATCH/REVIEW/NOT_APPLICABLE` 등). 금액은 원 단위 INTEGER, 일시는 문자열(KST).

### 이름 변경 제안 (적용하지 않음 — 팀 확인 후 진행)

| 기존명 | 제안명 | 이유 | 영향받는 코드 |
|---|---|---|---|
| `bid_notice.company_id_src` | `orderer_company_code` | `company_id`는 우리 업체(`company`) FK 이름인데, 이 컬럼은 발주사 구분(API `companyId=COM01`)이라 혼동 | `03_bid_collection.sql`, `07_views.sql`, `collect.py`(`upsert_notice`) |
| `bid_document_content.attachment_id` | `bid_attachment_id` | 외래키 이름에 참조 테이블을 드러냄 | `03_bid_collection.sql`, `collect.py`(`save_content`) |
| `document_extraction.attachment_id` | `bid_attachment_id` | 위와 같음 | `04_document_analysis.sql` (코드 없음) |
| `required_document_item.source_attachment_id` | `source_bid_attachment_id` | 위와 같음 | `06_notice_workflow.sql` (코드 없음) |
| `company_document_check.item_id` | `required_document_item_id` | `item_id`만으로는 어떤 항목인지 알 수 없음 | `06_notice_workflow.sql` (코드 없음) |
| `notice_analysis_cache`, `notice_schedule_event`, `notice_notification`, `favorite_notice` | `bid_notice_analysis`, `bid_notice_schedule_event`, `bid_notice_notification`, `company_favorite_notice` | 공고 테이블 접두어(`bid_notice`) 통일, 즐겨찾기는 업체 소유임을 드러냄. 기능명세서 §5 이름과 달라지므로 기획 문서도 함께 수정 필요 | 04·06·07 SQL, 기능명세서 §5 (코드 없음) |
| `collect_run` | `collection_run` | 동사형 → 명사형 | `03_bid_collection.sql`, `collect.py` |

현재 운영 DB는 없으므로 변경 비용은 SQL·코드 수정뿐이다. 운영 DB가 생긴 뒤 바꾸면 `ALTER TABLE ... RENAME` 마이그레이션이 필요하다.

### 쓰기 책임 정리 제안 (팀 결정 필요)

- **AI 추출 결과(`document_extraction`, `evaluation_rule`, `required_document_item`):** AI 파트는 JSON을 반환하고, 백엔드가 [`validate_extraction.py`](../../docs/planning/poc/validate_extraction.py) 같은 규칙 검증(사정액=API, 근거 문장 원문 존재 등)을 거쳐 저장하는 방식을 제안한다. 검증 실패는 `verification_status='REVIEW'`.
- **`notice_schedule_event`:** API·SRM 일정은 수집기가, 문서에서 뽑은 제출기한은 AI 결과 저장 시 함께 넣는 방식을 제안한다.
- **`notice_notification`:** 발송은 AI 파트(알림 문구 생성), 대상 선정과 이력 저장은 백엔드가 맡는 방식을 제안한다.

## 5. 팀 결정 → 컬럼 대응

| 팀 결정 | 저장 위치 |
|---|---|
| 한전(`COM01`) 공사용역(`ConstructionService`) 중 **적격심사(`QualifiedEval`)** 공고만 | 수집기 `in_scope()` — 범위 밖 공고는 `bid_notice`에 저장하지 않음, 뷰에도 같은 조건 |
| 진행 중 공고만 | `progress_state` + `attend_req_close_at` (API 상태만으로는 마감 공고가 섞임) |
| 예비가격 기초금액 / 추정가격 / 사정액 | `estimated_price_basic_amount` / `presumed_price` / `approximate_amount` |
| A금액(`totalAAmt`) / 경비(`overHeadCostAmount`) | `a_amount` / `overhead_cost_amount` — SRM 상세 조회에 포함, 추가 요청 없음 |
| 순공사원가(재료비+노무비+경비) | `net_construction_cost` — SRM에 값이 없어 수집기는 채우지 않음(NULL=미확인). 공고문 금액표 추출 방식은 팀 결정 |
| 자동심사 조건: 면허자격명 / 적격자격명 / 지역 / 주력분야 | `bid_restriction_item.category` = `LICENSE` / `QUALIFIED` / `REGION` / `LICENSE_MAIN` |
| 적격심사기준: 배점표, 입찰가격 산식, 해당/인접 사업소, 업종별 경영상태 평균비율 | `evaluation_rule.category` = `PERFORMANCE`·`PASS_SCORE` / `PRICE` / `OFFICE` / `MANAGEMENT` (세부는 `parameters` JSON) |
| 제출서류: 계약부서/감독부서, "2가지 중 1가지" | `required_document_item.submit_to`, `alternative_group` |
| 제출기한: 통보서 접수일로부터 5일 이상 (제8조 ②), 개찰 후 약 1주 | `notice_schedule_event` `QUALIFICATION_DOC_DUE`, 개찰 전 `confirmed=0` |
| 감리 상위 면허 자동 통과 | `license_equivalence` (시드 예정) |

## 6. 검증 (2026-11-19, 임시 DB)

| 확인 | 결과 |
|---|---|
| 분리 전후 구조 비교 | 기존 `schema.sql`과 분리 파일로 만든 DB의 테이블·컬럼·외래키·인덱스 동일 (뷰만 적격심사 조건 추가) |
| ① 초기화 | 01~07 순서 적용, 테이블 28·뷰 1, `integrity_check=ok` |
| ② 시드 | 면허 3,629 / 자격 20 / 상하위 21 / 지역그룹 18 / 시군구 253, 6자리 복원 확인(`F_011013` 춘천시) |
| ③ 수집 범위 | 30일 API 1,724건 → 적격심사 공사용역 957건만 저장, 처리 대상은 신청 마감 전 공고만. 정정 중복(E012607113)은 최신 행 저장 |
| ④ 수집 저장 | E012608220: 면허 A06·지역 C, 사정액 271,946,271, A금액 10,171,627, 경비 131,079,269(당시 `net_construction_cost`에 잘못 저장 → 2026-11-20 정정), 개찰 11-26, 첨부 2종·원문 저장 |
| ⑤ 추천 뷰 | 가상 업체 + 판정 2건 중 신청 마감 전 공고만 조회, 별표 반영 |
| 재초기화 | 새 DB(업체가 면허 참조 중)에 `init_db`+`seed_reference` 재실행, 구 `schema.sql`로 만든 DB에 `init_db` 실행 → 두 DB 모두 행 수 변화 없음, 외래키 위반 0, 뷰 갱신 |
| 컬럼 마이그레이션 | 정정 감지 컬럼이 없던 DB(957건)에 `init_db` → 5개 컬럼 추가, `api_created_at` 957건·`last_collected_at` 1건 채움, 행 수 변화 없음, 재실행해도 동일 |
| 정정 재수집 | 등록일시가 더 늦은 API 행 → 재수집 표시, 이른 행·같은 행은 무시. 재수집 시 정정으로 빠진 첨부 삭제·남은 첨부 행 id 유지·내용 바뀐 경우만 `notice_analysis_cache` 무효화. 실제 SRM 재수집(E012608220)에서 차수 `1` 저장·표시 해제 |
| 제약 | 없는 시군구, 광역과 맞지 않는 시군구, 사업자번호 형식, 없는 면허, 면허 중복, 판정 상태 오타 → 저장 차단 |

참고: 시드를 `INSERT OR REPLACE`에서 UPSERT로 바꿨다. 현재 외래키는 `NO ACTION`이라 REPLACE도 실패하지는 않지만, 나중에 `ON DELETE CASCADE`가 붙으면 REPLACE가 참조 행(업체 면허 등)을 조용히 지운다.

### 경비 오저장 정정 (2026-11-20)

예전 수집기는 SRM `overHeadCostAmount`(=경비)를 `net_construction_cost`(순공사원가)에 저장했다. 지금 수집기는 이 값을 `overhead_cost_amount`에 쓰고 `net_construction_cost`는 쓰지 않으므로, `net_construction_cost IS NOT NULL`인 행이 정정 대상이다.

```bash
python -m backend.db.fix_overhead_cost            # 미리보기: 대상·바뀔 값·공고문 원문 근거 (DB 변경 없음)
python -m backend.db.fix_overhead_cost --apply    # 백업(bidmate.sqlite3.bak-시각) 후 한 트랜잭션으로 반영
```

반영 내용: `overhead_cost_amount`가 비어 있으면 저장값을 옮기고, `net_construction_cost`는 NULL(미확인)로 바꾼다. 여러 번 실행해도 결과가 같다.

## 7. 아직 정하지 않은 것

- 위 **이름 변경 제안**과 **쓰기 책임 제안** 확정
- `work_type`(공고유형) 분류 규칙, `document_template_item`·`license_equivalence` 시드 데이터
- 다중 사용자(`company_user`) — 정의서 §9 후속
- 운영 DB를 PostgreSQL로 옮길지 (기획서 5장) — 표준 SQL 위주로 작성, `GENERATED` 컬럼·`datetime('now')`만 SQLite 전용
