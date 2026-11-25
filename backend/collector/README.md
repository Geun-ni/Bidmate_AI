# SRM 공고 수집기 (백엔드·API 담당)

기능명세서 FUNC-005~008과 11-18 회의 결정(API 우선 + 누락 시 SRM 보완, 공고당 1회 수집)을 구현함.
DB 테이블 정의·초기화는 [`backend/db/README.md`](../db/README.md) 참고 (`03_bid_collection.sql`).

**서비스 범위 (11-19 결정):** 한전(`companyId=COM01`) 공사용역(`purchaseType=ConstructionService`) 중 낙찰방법이 **적격심사(`bidType=QualifiedEval`)**인 공고만 저장·처리한다. 업체 매칭(판정)은 아직 범위 밖이고, 공고의 자동심사 조건 확인까지만 확인

| 단계 | 하는 일 | 출처 | 저장 테이블 |
|---|---|---|---|
| 1. 공고 목록 | 최초 180일 / 매일 마지막 반영 이후 공고일 범위 조회, 정정 차수 중복은 최신 등록 행만 | 한전 Open API `electContract.do` | `bid_notice` |
| 2. 자동심사 조건 | 투찰제한 자동/수동 설정, 면허자격명·적격자격명·지역·주력분야 + A금액·경비·개찰일시 | SRM `findBidBasicInfo`, `find{License,Qualified,Region,LicenseMain}CodeData` | `bid_restriction_snapshot`, `bid_restriction_category`, `bid_restriction_item`, `bid_notice` |
| 3. 서류 확보 | 공고문·적격심사기준 다운로드 (API 첨부 → 없으면 SRM 전체 첨부) | Open API `filenlink`, SRM `getAttachments`/`downloadAttachment.do` | `bid_attachment` (`source`=OPEN_API/SRM_FALLBACK) |
| 4. 원문 읽기 | HWP/HWPX/PDF 전처리 본문(표 행·수식·그림 자리 표시, 분수 합침, HWP 전용 문자 정리) + 수식 스크립트 | 다운로드 파일 | `bid_document_content` (`parser_version`) |

LLM 요약·구조화 추출은 AI 파트에서 `bid_document_content`의 `text`, `equations`를 읽어 처리함

## 실행 — 최초 전체 적재 / 매일 증분

```bash
python -m pip install -r backend/collector/requirements.txt
# 프로젝트 루트 .env 에 SRM_API_KEY=발급키 (git 제외)
python -m backend.collector.collect --mode full               # 최초 전체 적재 (중단되면 다시 실행 → 남은 공고부터)
python -m backend.collector.collect                           # 매일 증분 (--mode daily, 예약 실행이 쓰는 명령)
python -m backend.collector.collect --dry-run                 # 목록만 반영하고 상세 수집 대상 출력 (SRM 요청 없음)
python -m backend.collector.collect --notice E012607925       # 특정 공고 상세 수집 (DB에 기본정보가 있어야 함)
```

| | 최초 적재 `--mode full` | 매일 증분 `--mode daily` |
|---|---|---|
| 공식 API 조회 범위(공고일) | 오늘 기준 최근 180일 (`--full-days`), 90일 창 2개 | 마지막으로 목록을 정상 반영한 실행의 조회 종료일 − 7일 ~ 오늘 (`--overlap-days`). 반영 기록이 없으면 최초 적재 범위 |
| 기본정보 저장 | 범위 안 서비스 대상 전체 | 신규·변경 공고 갱신 (같은 공고는 그대로) |
| 상세 수집 대상 | 진행 중 공고 중 상세 미완료 전체 (기존 DB에 기본정보만 있던 공고 포함) | 같음: 신규 + 정정·첨부변경(`needs_recollect`) + 이전에 못 끝낸 공고. 완료된 공고는 재사용 |
| 건수 제한 | 없음 (`--limit N`으로 제한 가능) | 없음 |
| SRM 속도 | 요청 간격 5초, 5건마다 60초 쉼, 차단 의심 시 10분 쉬고 이어서(최대 20번, 실측상 공고 16건쯤마다 끊김) | 3초, 10건마다 30초, 차단 시 10분 쉬고 1번 재시도 |

- 이어하기: 공고 1건 상세 수집이 끝날 때마다 커밋한다. 실행이 중단(차단·종료·PC 꺼짐)돼도 끝난 공고는 남고, 다음 실행은 `is_done()`이 거짓인 공고만 다시 고른다.
- 상세 수집 대상은 진행 중 공고(`is_open` + 신청 마감 전)만이다. 마감된 공고는 기본정보만 저장한다(`--include-closed`로 상세 수집 가능). 180일 범위 서비스 대상 3,210건 중 진행 중은 163건(2026-11-20).
- 다운로드 파일: `downloads/<공고번호>/` (git 제외)
- `--with-llm` (유료, 하루 1회 실행 기준): 공고마다 배점 저장 뒤 `ai/notice_extraction.py`로 서류를 보완한다. 규칙 추출 실패 산식이 있는 공고는 바로 부르지 않고, 수집이 끝난 뒤 **실행당 1건**만 LLM으로 보완한다(`llm_evaluation_target()`: 접수 중, 아직 LLM 결과 없음, 신청 마감 가까운 순). 그 공고의 서류도 이때 같이 처리된다. LLM이 실패하면 저장된 게 없어 다음 실행에서 다시 고른다.

### 공식 API로 변경을 찾는 방법과 한계 (2026-11-20 확인)

- 공식 명세(전력데이터 개방포털 '전자입찰 계약정보 가이드'): 조회 인자는 `noticeBeginDate`·`noticeEndDate`(**공고일**, 최대 90일), 선택 `no`·`name`·`bidAttendReqCloseDatetime`·`progressState`뿐이다. **수정시각으로 조회하는 인자는 없다.** 응답의 시각 필드는 `createDatetime`(등록년월시분초) 하나.
- 90일 폭만 지키면 과거 구간도 조회된다(예: 200~111일 전). 당일 공고일만 조회하면 오후에도 `404 NotFound`가 와서, 당일 공고는 다음 날 조회된다고 보고 7일을 겹쳐 조회한다.
- 정정 공고는 같은 공고번호로 **새 행**이 생기고 공고일이 정정일로, `createDatetime`이 더 늦게 바뀐다(최근 180일 3건: E012606821, E012607113, E012604590). 그래서 정정 공고는 공고일 기준 조회에 다시 잡힌다.
- 변경 판정(`upsert_notice()`): 저장된 원본(`api_raw`)과 비교해
  - 처음 보는 공고번호 → **신규**
  - `createDatetime`이 더 늦거나 첨부(`filename1~5`, `filenlink1~5`)가 다름 → **정정·첨부변경** → `needs_recollect=1`, 상세 재수집
  - 그 밖의 값(마감일시·금액·상태 등)만 다름 → **기본정보 갱신**, 상세는 재사용
  - 같음 → 변경 없음 / 저장값보다 이전 차수 행 → 무시
- **찾을 수 없는 경우:** 공고일이 조회 범위(마지막 반영일 − 7일)보다 이전인 공고가 **공고일을 바꾸지 않고** 같은 행의 내용만 고쳐지는 경우. 이런 수정이 실제로 있는지는 하루치 비교(973건 모두 같음)로는 확인하지 못했다. 필요하면 `--mode full`을 다시 돌리면 180일 전체 기본정보를 다시 비교한다(상세는 재사용).
- `progressState`는 마감 후에도 갱신되지 않는다(180일 범위 3,210건 모두 `AttendProgress`/`PreAttendProgress`). 마감 여부는 신청 마감 일시로 판단한다.

### SRM 상세만 바뀐 경우

- 공식 API 목록과 SRM 검색 결과(`getPageList`)에는 SRM 정정 차수가 없다. 공고별 상세(`findBidBasicInfo`)의 `bidRevision`만 있고, 마지막 수정시각 같은 필드는 없다.
- 그래서 공식 API 기본정보는 그대로인데 SRM 상세만 바뀐 경우는 목록만으로는 **감지할 수 없다.** `--srm-revision-check N`을 주면 완료된 진행 중 공고 N건(마지막 수집이 오래된 순)의 `bidRevision`을 다시 조회해 저장값(`srm_revision`)과 다르면 재수집한다. 공고마다 SRM 요청 1번이 들어서 기본값은 0(끔).
- 차수 변화 없이 투찰제한 목록만 바뀌는 경우는 차수로도 알 수 없다(각 목록을 다시 받아야 함).

## 매일 자동 수집 (Windows 작업 스케줄러)

운영 PC(Windows)에서 매일 12:00(한국시간)에 `--mode daily`를 실행한다. 최초 1회는 `--mode full`을 직접 실행한다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_daily_collect.ps1   # 작업 'BidmateDailyCollect' 등록 (다시 실행하면 덮어씀)
Start-ScheduledTask -TaskName BidmateDailyCollect                             # 지금 한 번 실행
Get-ScheduledTaskInfo -TaskName BidmateDailyCollect                           # 마지막·다음 실행 시각, 결과 코드
Unregister-ScheduledTask -TaskName BidmateDailyCollect -Confirm:$false        # 해제
```

- 실행 스크립트: `scripts/collect_daily.ps1` → 로그 `logs/collect_YYYYMMDD.log` (git 제외)
- 현재 사용자로 로그인돼 있을 때 실행된다. 12:00에 PC가 꺼져 있었으면 켜진 뒤 한 번 실행하고, 며칠 꺼져 있었으면 마지막 반영일부터 조회 범위가 넓어진다. 배터리에서도 실행, 최대 3시간.
- 실행 결과는 `collect_run`에 남는다: `mode`, `window_begin`~`window_end`(조회 공고일), `list_synced`(1=목록 정상 반영 → 다음 daily 기준), `new_notice_count`(신규), `updated_count`(기본정보 갱신), `corrected_count`(정정·첨부변경), `completed_count`(상세 완료), `failed_count`(실패·부분, 다음 실행에서 재시도), `deferred_count`(미처리: 한도·차단·중단으로 못 한 건, 다음 실행에서 이어서). 웹 API `GET /api/notices`의 `last_run`으로도 볼 수 있다.

```sql
SELECT id, mode, window_begin, window_end, list_synced, new_notice_count, updated_count, corrected_count,
       completed_count, failed_count, deferred_count, stopped_reason
FROM collect_run ORDER BY id DESC LIMIT 5;
```

## DB 경로

수집기·웹 서버·예약 실행은 모두 `backend.db.init_db.DEFAULT_DB_PATH`를 쓴다. 기본은 저장소 루트 `bidmate.sqlite3`(실행 위치와 무관). 다른 위치를 쓰려면 환경 변수 `BIDMATE_DB` 또는 루트 `.env`에 `BIDMATE_DB=경로`를 넣는다(상대 경로는 저장소 루트 기준).

## 신규 공고 감지 지점 (알림 연결용, 발송은 아직 없음)

- 위치: `collect.py` `main()`에서 `upsert_notice()`가 `NEW`를 돌려주는 공고(`changes[NEW]`, 주석 `[알림 연결 지점]`) = `bid_notice`에 없던 공고번호. 서비스 범위(한전 공사·용역 적격심사) 안의 공고만 해당한다. 최초 적재(`--mode full`)는 신규가 수천 건이라 알림 대상에서 빼야 한다(`collect_run.mode`로 구분).
- 실행이 끝난 뒤에는 DB로도 찾을 수 있다: `bid_notice.first_seen_at >= collect_run.started_at` (`first_seen_at`은 처음 저장할 때만 기록되고 갱신되지 않음). 건수는 `collect_run.new_notice_count`.
- 정정·첨부변경은 `CORRECTED`(`needs_recollect=1`), 기본정보만 바뀐 공고는 `UPDATED`로 신규와 구분된다.
- 면허·적격자격까지 붙여 알리려면 상세 수집이 끝난 뒤(`process_notice()` 이후 `is_done()`이 참)가 맞다. SRM 차단으로 상세 수집이 다음 실행으로 넘어간 신규 공고는 그때 조건이 비어 있다.

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
- 이름에 `적격심사`가 없는 기준서: SRM 보완에서도 적격심사기준을 못 찾으면, 이름에 `심사기준`이 들어간 hwp·hwpx·pdf(`인원장비` 제외, 예: `pq심사기준`, `입찰참가자격사전심사기준`)를 받아 **40자 이하 문단에 `적격심사…기준`이 있을 때만** 기준서로 저장한다(E012606754: `2. pq심사기준_동부안 최종수정.hwp` 안의 `5.2.1 적격심사기준`). 저장된 기준서 69개 중 68개가 이 본문 판정을 통과하고, 나머지 1개는 `적격심사기준 제출서류 목록표`(기준서 아님)
- 배점표 없는 기준서: 적격심사기준을 받았어도 그 안에 배점표(입찰가격 행이 있는 표, `extract_evaluation_rules.top_tables`)가 없으면 진짜 기준서를 놓친 것으로 보고 SRM 전체 첨부에서 아직 안 받은 `적격심사`·`심사기준` 파일을 더 받는다(E012606999: API 5개 중 `PQ 세부기준`만 잡히고 배점표가 든 `3. 적격심사 세부기준`은 6번째 이후 첨부). PDF만 있는 공고는 매번 이 확인을 하므로 SRM 첨부 목록 요청이 1번 늘어난다
- 적격심사 배점: 공고 1건 수집이 끝나면(투찰제한 조회 성공) 그 공고의 `evaluation_rule`을 다시 뽑아 바꾼다(`extract_evaluation_rules.refresh_notice`, 로그에 `배점 N행`). LLM·사용자가 고친 행이 있는 공고는 건드리지 않고, 추출이 실패해도 수집 결과는 그대로 둔다
- AI 파트(LLM)용 첨부 전달은 `fetch_attachments.py` (DB에 쓰지 않음)
  - 파서가 배점을 못 뽑은 공고(PDF 기준서, 기준서 금액 구간이 공고 추정가격과 안 맞음 등)는 `llm_targets()`로 찾고, 수집기가 받아 둔 기준서를 `saved_documents()`로 넘긴다(로컬에 없으면 SRM에서 받음)
  - 그 밖의 첨부(예: `시방서 등.zip`)는 수집기가 받지 않는다. 필요할 때 `fetch_attachments()`로 이름을 골라 받는다

```bash
python -m backend.collector.fetch_attachments --llm-targets                 # 파서가 못 뽑은 공고번호 (evaluation_rule 비어 있음)
python -m backend.collector.fetch_attachments E012608048 --saved           # 받아 둔 기준서 → downloads/<공고번호>/extra/
python -m backend.collector.fetch_attachments E012608185 --list            # SRM 첨부 목록
python -m backend.collector.fetch_attachments E012608185 --name "\.zip$"   # SRM 첨부 받기 → downloads/<공고번호>/extra/
```
- SRM 보완 도중 차단·오류로 끝나면 공고문·기준서 확보 여부를 `0`(없음)이 아니라 `NULL`(미확인)로 남겨 다음 실행에서 다시 찾는다. 예전엔 `0`으로 저장돼 완료로 간주됐다(E012608004, 2026-11-20 재수집 표시로 바로잡음)
- 금액: SRM `overHeadCostAmount`는 공고문 금액표의 **경비**라 `overhead_cost_amount`에 저장한다. SRM에는 순공사원가 값이 없어 `net_construction_cost`는 채우지 않는다(NULL=미확인)
- 개인정보 미저장: SRM 응답의 담당자 이름·사번·연락처(`creator*`, `updater*`, `representative*` 등)는 저장 전에 제거
