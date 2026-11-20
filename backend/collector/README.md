# SRM 공고 수집기 (백엔드·API 담당)

기능명세서 FUNC-005~008과 11-18 회의 결정(API 우선 + 누락 시 SRM 보완, 공고당 1회 수집)을 구현함.
DB 테이블 정의·초기화는 [`backend/db/README.md`](../db/README.md) 참고 (`03_bid_collection.sql`).

**서비스 범위 (11-19 결정):** 한전(`companyId=COM01`) 공사용역(`purchaseType=ConstructionService`) 중 낙찰방법이 **적격심사(`bidType=QualifiedEval`)**인 공고만 저장·처리한다. 업체 매칭(판정)은 아직 범위 밖이고, 공고의 자동심사 조건 확인까지만 확인

| 단계 | 하는 일 | 출처 | 저장 테이블 |
|---|---|---|---|
| 1. 공고 목록 | 최근 N일 공고 조회, 정정 차수 중복은 최신 등록 행만 | 한전 Open API `electContract.do` | `bid_notice` |
| 2. 자동심사 조건 | 투찰제한 자동/수동 설정, 면허자격명·적격자격명·지역·주력분야 + A금액·경비·개찰일시 | SRM `findBidBasicInfo`, `find{License,Qualified,Region,LicenseMain}CodeData` | `bid_restriction_snapshot`, `bid_restriction_category`, `bid_restriction_item`, `bid_notice` |
| 3. 서류 확보 | 공고문·적격심사기준 다운로드 (API 첨부 → 없으면 SRM 전체 첨부) | Open API `filenlink`, SRM `getAttachments`/`downloadAttachment.do` | `bid_attachment` (`source`=OPEN_API/SRM_FALLBACK) |
| 4. 원문 읽기 | HWP/HWPX/PDF 본문 텍스트 + 한글 수식 스크립트 | 다운로드 파일 | `bid_document_content` |

LLM 요약·구조화 추출은 AI 파트에서 `bid_document_content`의 `text`, `equations`를 읽어 처리함

## 실행

```bash
python -m pip install -r backend/collector/requirements.txt
# 프로젝트 루트 .env 에 SRM_API_KEY=발급키 (git 제외)
python -m backend.collector.collect --dry-run                 # 처리 대상만 확인 (bid_notice 저장, SRM 요청 없음)
python -m backend.collector.collect --days 7 --limit 5        # 진행 중 적격심사 공사용역 공고 중 신규 5건
python -m backend.collector.collect --notice E012607925       # 특정 공고 (범위·마감과 무관하게 처리)
```

- DB: 기본 `bidmate.sqlite3`(저장소 루트, git 제외). 연결은 `backend.db.init_db.init_db()`로 열어 스키마를 먼저 적용
- 다운로드 파일: `downloads/<공고번호>/` (git 제외)
- 이미 수집이 끝난 공고는 건너뛰고(`--refresh`로 재수집), 실패·부분 수집 공고는 다음 실행에서 재시도함.

## 정정 공고 재수집

정정은 드물지만(영래언니 확인), 자동심사 면허자격명이 바뀌는 정정도 있다(예: E012300814 1차 정정 "감리업(전력종합감리) → 감리업(전력전문감리)"). 그래서 전체를 매번 다시 확인하지 않고 정정된 공고만 다시 수집함.

1. 감지 (SRM 요청 없음): 매 실행에서 받는 Open API 행의 `createDatetime`이 저장된 `bid_notice.api_created_at`보다 늦으면 `needs_recollect=1`, `recollect_reason`에 이전 → 새 등록일시를 기록한다. 더 이른 행(이전 차수)이 와도 기준값은 되돌아가지 않는다.
2. 재수집: 표시된 공고는 수집 완료로 보지 않고, 처리 대상의 맨 앞에 둠(`--dry-run`에서 `[정정]` 표시). 투찰제한·첨부를 다시 받고 SRM 정정 차수를 `srm_revision`에 기록함
3. 정리: 재수집이 성공하면 이번에 다시 받지 않은 첨부(정정으로 빠진 파일)를 삭제하고, 남은 첨부는 같은 행(id)을 유지한다. 첨부 내용(sha256 목록)이 바뀐 경우에만 `notice_analysis_cache`를 `INVALIDATED`로 바꿔 AI 파트가 재분석 대상을 알 수 있게 하고, 끝나면 `needs_recollect=0`, `last_collected_at` 갱신.
4. 마감이 지난 정정 공고는 진행 중 필터로 처리 대상에서 빠진다(표시는 남음). 일정·금액 같은 Open API 값은 정정 여부와 상관없이 매 실행 갱신된다.

## 운영 규칙

- 진행 중 판단: Open API `progressState`는 마감 후에도 `AttendProgress`로 남는 경우가 많으므로(30일 공사용역 1,092건 중 904건이 신청 마감 경과). `progressState` + 입찰참가신청 마감 일시를 같이 확인함 (`--include-closed`로 해제).
- SRM 요청 간격 기본 3초(`--interval`). 연속 조회 시 타임아웃·연결 끊김·HTTP 404로 차단된 적이 있어, 이런 응답이 오면 `SrmBlocked`로 그 실행을 멈추고 `collect_run.stopped_reason`에 기록
- 조회 실패 ≠ 제한 없음: `bid_restriction_category.fetch_status='SUCCESS'`이고 `item_count=0`일 때만 해당 제한이 없다는 뜻임
- 문서 확인: 받은 내용이 HTML 오류 페이지·빈 응답이거나 확장자(hwp/hwpx/pdf)와 파일 형식이 다르면 파일을 남기지 않고 `download_status='FAILED'`(`error`에 이유)로 기록하고 SRM 첨부에서 다시 찾는다(E012606980: API 링크가 HTML 1,064 bytes를 줌)
- 문서 종류: 파일명에 `적격심사` → 적격심사기준, `공고문` → 공고문. 단 `안내`·`담당자`가 들어간 이름은 공고문으로 보지 않는다(`공고 문의 담당자 안내.hwp`는 공백을 지우면 `공고문의…`가 됨, E012607149)
- 금액: SRM `overHeadCostAmount`는 공고문 금액표의 **경비**라 `overhead_cost_amount`에 저장한다. SRM에는 순공사원가 값이 없어 `net_construction_cost`는 채우지 않는다(NULL=미확인)
- 개인정보 미저장: SRM 응답의 담당자 이름·사번·연락처(`creator*`, `updater*`, `representative*` 등)는 저장 전에 제거
