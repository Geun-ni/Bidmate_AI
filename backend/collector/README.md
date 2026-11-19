# SRM 공고 수집기 (백엔드·API 담당)

기능명세서 FUNC-005~008과 11-18 회의 결정(API 우선 + 누락 시 SRM 보완, 공고당 1회 수집)을 구현한다.

| 단계 | 하는 일 | 출처 | 저장 테이블 |
|---|---|---|---|
| 1. 공고 목록 | 최근 N일 공고 조회 | 한전 Open API `electContract.do` | `bid_notice` |
| 2. 자동심사 조건 | 투찰제한 자동/수동 설정, 면허자격명·적격자격명·지역·주력분야 | SRM `findBidBasicInfo`, `find{License,Qualified,Region,LicenseMain}CodeData` | `bid_restriction_snapshot`, `bid_restriction_category`, `bid_restriction_item` |
| 3. 서류 확보 | 공고문·적격심사기준 다운로드 (API 첨부 → 없으면 SRM 전체 첨부) | Open API `filenlink`, SRM `getAttachments`/`downloadAttachment.do` | `bid_attachment` (`source`=OPEN_API/SRM_FALLBACK) |
| 4. 원문 읽기 | HWP/HWPX/PDF 본문 텍스트 + 한글 수식 스크립트 | 다운로드 파일 | `bid_document_content` |

LLM 요약·구조화 추출은 AI 파트에서 `bid_document_content`의 `text`, `equations`를 읽어 처리한다. 이 수집기는 LLM을 호출하지 않는다.

## 실행

```bash
python -m pip install -r backend/collector/requirements.txt
# 프로젝트 루트 .env 에 SRM_API_KEY=발급키 (git 제외)
python -m backend.collector.collect --days 7 --limit 5            # 최근 7일 진행 중 공사용역 공고 중 신규 5건
python -m backend.collector.collect --notice E012607925           # 특정 공고
python -m backend.collector.collect --bid-type QualifiedEval      # 적격심사 공고만
```

- DB 기본 경로: 저장소 루트 `bidmate.sqlite3` (git 제외, `--db`로 변경)
- 다운로드 파일: `downloads/<공고번호>/` (git 제외)
- 이미 수집이 끝난 공고는 건너뛴다(`--refresh`로 재수집). 실패·부분 수집 공고는 다음 실행에서 다시 시도한다.

## 운영 규칙

- **SRM 요청 간격** 기본 3초(`--interval`). SRM은 연속 조회 시 타임아웃·연결 끊김·HTTP 404로 차단된 적이 있어, 이런 응답이 오면 `SrmBlocked`로 그 실행을 멈추고 `collect_run.stopped_reason`에 기록한다.
- **조회 실패 ≠ 제한 없음:** `bid_restriction_category.fetch_status`가 `SUCCESS`이고 `item_count=0`일 때만 해당 제한이 없다는 뜻이다. `FAILED`면 판정에 쓰지 않는다.
- **개인정보 미저장:** SRM 응답의 담당자 이름·사번·연락처(`creator*`, `updater*`, `representative*` 등)는 저장 전에 제거한다.
- `bid_notice.criteria_doc_found=0`은 적격심사기준을 찾지 못했다는 뜻이다. 낙찰방법이 적격심사가 아닌 공고(`bid_type`≠QualifiedEval)는 원래 없을 수 있다.

## 검증 (2026-11-18)

| 공고 | 확인 결과 |
|---|---|
| E012607925 (적격심사) | 면허 A06 전기공사업(Auto/And), 지역 I 전남광주(Auto), 공고문=Open API, 적격심사기준=SRM 보완(API 5개에 없음), 기준서 수식 4개 추출 |
| E012607994 (제한적최저가) | 면허 A06, 제한경쟁입찰자격 2·3(Auto/And), 지역 제한 없음(조회 성공·0건), 공고문=Open API |

미검증: 주력분야(`findLicenseMainCodeData`) 응답의 필드명은 제한이 있는 공고로 아직 확인하지 못했다. 물품(Product) 공고에서 `fileGroupId=ConstructionBidFileGroup` 인자가 그대로 동작하는지도 확인 필요.
