# SRM 공고 수집기 (백엔드·API 담당)

기능명세서 FUNC-005~008과 11-18 회의 결정(API 우선 + 누락 시 SRM 보완, 공고당 1회 수집)을 구현한다.
DB 테이블 정의·초기화는 [`backend/db/README.md`](../db/README.md) 참고 (`03_bid_collection.sql`).

**서비스 범위 (11-19 결정):** 한전(`companyId=COM01`) 공사용역(`purchaseType=ConstructionService`) 중 낙찰방법이 **적격심사(`bidType=QualifiedEval`)**인 공고만 저장·처리한다. 업체 매칭(판정)은 아직 범위 밖이고, 공고의 자동심사 조건 확인까지만 한다.

| 단계 | 하는 일 | 출처 | 저장 테이블 |
|---|---|---|---|
| 1. 공고 목록 | 최근 N일 공고 조회, 정정 차수 중복은 최신 등록 행만 | 한전 Open API `electContract.do` | `bid_notice` |
| 2. 자동심사 조건 | 투찰제한 자동/수동 설정, 면허자격명·적격자격명·지역·주력분야 + A금액·순공사원가·개찰일시 | SRM `findBidBasicInfo`, `find{License,Qualified,Region,LicenseMain}CodeData` | `bid_restriction_snapshot`, `bid_restriction_category`, `bid_restriction_item`, `bid_notice` |
| 3. 서류 확보 | 공고문·적격심사기준 다운로드 (API 첨부 → 없으면 SRM 전체 첨부) | Open API `filenlink`, SRM `getAttachments`/`downloadAttachment.do` | `bid_attachment` (`source`=OPEN_API/SRM_FALLBACK) |
| 4. 원문 읽기 | HWP/HWPX/PDF 본문 텍스트 + 한글 수식 스크립트 | 다운로드 파일 | `bid_document_content` |

LLM 요약·구조화 추출은 AI 파트에서 `bid_document_content`의 `text`, `equations`를 읽어 처리한다. 이 수집기는 LLM을 호출하지 않는다.

## 실행

```bash
python -m pip install -r backend/collector/requirements.txt
# 프로젝트 루트 .env 에 SRM_API_KEY=발급키 (git 제외)
python -m backend.collector.collect --dry-run                 # 처리 대상만 확인 (bid_notice 저장, SRM 요청 없음)
python -m backend.collector.collect --days 7 --limit 5        # 진행 중 적격심사 공사용역 공고 중 신규 5건
python -m backend.collector.collect --notice E012607925       # 특정 공고 (범위·마감과 무관하게 처리)
```

- DB: 기본 `bidmate.sqlite3`(저장소 루트, git 제외). 연결은 `backend.db.init_db.init_db()`로 열어 스키마를 먼저 적용한다.
- 다운로드 파일: `downloads/<공고번호>/` (git 제외)
- 이미 수집이 끝난 공고는 건너뛴다(`--refresh`로 재수집). 실패·부분 수집 공고는 다음 실행에서 다시 시도한다.

## 운영 규칙

- **진행 중 판단:** Open API `progressState`는 마감 후에도 `AttendProgress`로 남는 경우가 많다(30일 공사용역 1,092건 중 904건이 신청 마감 경과). 그래서 `progressState` + **입찰참가신청 마감 일시**를 함께 본다 (`--include-closed`로 해제).
- **SRM 요청 간격** 기본 3초(`--interval`). 연속 조회 시 타임아웃·연결 끊김·HTTP 404로 차단된 적이 있어, 이런 응답이 오면 `SrmBlocked`로 그 실행을 멈추고 `collect_run.stopped_reason`에 기록한다.
- **조회 실패 ≠ 제한 없음:** `bid_restriction_category.fetch_status='SUCCESS'`이고 `item_count=0`일 때만 해당 제한이 없다는 뜻이다.
- **개인정보 미저장:** SRM 응답의 담당자 이름·사번·연락처(`creator*`, `updater*`, `representative*` 등)는 저장 전에 제거한다.

## 검증

| 일자 | 공고 | 확인 결과 |
|---|---|---|
| 11-18 | E012607925 | 면허 A06(Auto/And), 지역 I 전남광주(Auto), 공고문=Open API, 적격심사기준=SRM 보완, 기준서 수식 4개 |
| 11-18 | E012607994 (제한적최저가, 현재 범위 밖) | 면허 A06, 제한경쟁입찰자격 2·3(Auto/And), 지역 제한 없음(조회 성공·0건) |
| 11-19 | E012608220 (진행 중) | 면허 A06, 지역 C 충북, 사정액 271,946,271, A금액 10,171,627, 순공사원가 131,079,269, 개찰 11-26 11:00, 공고문=SRM 보완, 적격심사기준=Open API(수식 4개) |
| 11-19 | 범위 필터 | 30일 API 1,724건 → 범위 957건만 저장, 처리 대상은 신청 마감 전 공고만 |

미검증: 주력분야(`findLicenseMainCodeData`) 응답의 필드명은 제한이 있는 공고로 아직 확인하지 못했다.
