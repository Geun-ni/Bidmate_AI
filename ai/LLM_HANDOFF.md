# LLM 담당 ↔ DB 담당 연동 기준

수집 담당 코드는 본문과 산식 룰 결과를 SQLite에 저장하고 커밋한다. `--with-llm` 옵션을 켜면 그 직후 LLM 담당 코드인 `process_notice_extraction(db, notice_no)`를 호출한다. 이 함수는 산식 LLM 보완 → 제출서류 룰 추출 → 제출서류 LLM 보완 순서로 실행하며 결과를 같은 DB에 저장한다.

## 호출 및 상태

| 담당 | 연결 기준 |
| --- | --- |
| 수집·백엔드 | 수집 DB 연결과 공고번호를 전달한다. 산식 룰 추출 결과와 실패 상태는 수집기가 먼저 저장한다. DB 초기화는 기존 SQLite에도 제출서류 근거·출처 열을 추가한다. |
| LLM | 실패 산식과 부족한 서류를 별도 API 요청으로 처리한다. 산식 오류가 나도 서류는 계속 처리한다. 함수 반환값과 수집 로그에 단계별 상태·저장 건수·오류를 남긴다. |
| 서비스 화면 | `evaluation_rule`과 `required_document_item`에서 결과를 조회할 때 `REVIEW`와 원문 근거를 확인할 수 있도록 연결한다. |

제출서류 처리 상태는 `notice_analysis_cache`에 기록한다. `DONE`은 해당 본문 버전의 처리가 끝났다는 뜻이다. 서류 개별 행의 검수 상태는 `verification_status`로 확인한다. 호출·검증에 실패하면 룰 저장분을 유지하고, 공고번호로 재처리할 수 있다.

## 입력

| 값 | 의미 |
| --- | --- |
| `notice_no` | 공고번호 |
| `attachment_id`, `filename`, `doc_kind` | 공고문 또는 적격심사기준 첨부 식별 |
| `content`, `read_status`, `sha256` | 전처리 본문·읽기 상태·원본 버전 |
| `rule_extracted`, `evaluation_rule` | 산식 규칙 파서 결과와 `REVIEW` 행 |

원문이 읽히지 않으면 LLM 호출 전에 재파싱·OCR이 필요하다. 첨부 버전이 바뀐 결과는 저장하지 않는다.

## 산식·배점 결과

`rule_extracted=0`인 공고에서 평가 행이 없으면 전체 항목을 요청하고, 룰이 만든 `REVIEW` 행이 있으면 해당 행만 보완한다. 단독 실행으로 초안을 볼 때는 `python -m ai.llm_evaluation --notice 공고번호 --execute`의 `review_file` JSON을 확인한다.

각 행에는 `category`, `item_name`, `rule_type`, `max_score`, `formula`, `parameters`(JSON 문자열), `attachment_id`, `source_location`, `source_quote`가 있다. `REVIEW` 모드의 `row_id`는 수정할 기존 행 ID이고, `FULL` 모드의 `parent_position`은 상위 항목 순서다. 함수가 원문 인용, 대상 행, 첨부 버전을 검사하고 `evaluation_rule`에 `updated_by=LLM`, `verification_status=REVIEW`로 저장한다. 배점 합계와 산식 결과의 정합성은 후속 평가에서 확인한다.

## 제출서류 결과

함수가 적격심사 서류의 룰 결과를 먼저 저장하고, 후보가 없거나 미해결 제출 근거를 발견한 공고에서 LLM 보완을 요청한다. 각 서류에는 `stage`, `submit_to`, `name`, `requirement_type`, `alternative_group`, `condition_note`, `validity_note`, `source_attachment_id`, `source_location`, `source_quote`가 있다. `position`은 새 항목을 저장할 때 단계별 표시 순서로 생성한다.

함수가 원문 인용과 첨부 버전을 확인한 뒤 `required_document_item`에 `verification_status=REVIEW`로 저장한다. 대응하는 자동 추출 항목은 기존 행을 갱신하고 새 항목은 추가한다. 사람이 입력했거나 검수한 항목은 보호한다. 정정본에서 빠진 기존 서류 행의 정리 정책은 팀 합의가 필요하다.

기존 `required_document_item`에 아래 세 열을 추가했다. 이전에 저장된 행은 세 값이 `NULL`로 유지된다.

| 추가 열 | 저장하는 값 |
| --- | --- |
| `source_quote` | 제출 요구가 나온 원문 문장 |
| `source_sha256` | 근거에 사용한 첨부 파일의 SHA-256 해시 |
| `updated_by` | 작성 주체. `PARSER`는 룰, `LLM`은 모델, `USER`는 사람 |

## 전환할 때 합의할 사항

1. 운영 DB가 SQLite인지 다른 DB인지, 실제 스키마와 접근 방식.
2. 공고·첨부 식별자 매핑과 정정 공고의 버전 무효화 방식.
3. `REVIEW` 결과를 화면에 표시할지, 추가 검토 후에만 표시할지.
4. API 호출 대상 선정, 비용 한도, 실패 재시도와 로그 보관 주체.

실행·재처리 방법은 [파이프라인 문서](PIPELINE.md)를 참고한다.
