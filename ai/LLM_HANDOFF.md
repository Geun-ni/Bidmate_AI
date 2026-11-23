# LLM 담당 ↔ DB 담당 연동 기준

LLM 담당은 **공고 원문을 읽어 근거 있는 추출 결과를 만들고 검증**한다. DB 연결 정보, 운영 DB 계정, 마이그레이션 실행은 DB·백엔드 담당과 협의한다. 현재 코드는 SQLite로 동작하지만, 아래 결과 형식은 DB 종류와 무관하게 유지한다.

## 입력

| 값 | 의미 |
| --- | --- |
| `notice_no` | 공고번호 |
| `attachment_id`, `filename`, `doc_kind` | 공고문 또는 적격심사기준 첨부 식별 |
| `content`, `read_status`, `sha256` | 전처리 본문·읽기 상태·원본 버전 |
| `rule_extracted`, `evaluation_rule` | 산식 규칙 파서 결과와 `REVIEW` 행 |

원문이 읽히지 않으면 LLM 호출 전에 재파싱·OCR이 필요하다. 첨부 버전이 바뀐 결과는 저장하지 않는다.

## 산식·배점 결과

`python -m ai.llm_evaluation --notice 공고번호 --execute`의 `review_file` JSON을 전달한다. `mode=FULL`은 평가 행이 없는 공고의 전체 항목, `mode=REVIEW`는 기존 `REVIEW` 항목의 보정이다. 핵심 필드는 `notice_no`, `version`, `mode`, `result.rows`, `result.issues`다.

각 행에는 `category`, `item_name`, `rule_type`, `max_score`, `formula`, `parameters`(JSON 문자열), `attachment_id`, `source_location`, `source_quote`가 있다. `REVIEW` 모드의 `row_id`는 수정할 기존 행 ID이고, `FULL` 모드의 `parent_position`은 상위 항목 순서다. DB 담당이 `evaluation_rule`에 저장할 때 `updated_by=LLM`, `verification_status=REVIEW`로 둔다. 원문 인용 일치, 대상 행·첨부 버전, 배점 합계·산식 변수·반올림은 저장 전후에 검증한다.

## 제출서류 결과

`python -m ai.llm_documents --notice 공고번호 --execute`의 `review_file` JSONL을 전달한다. 핵심 필드는 `notice_no`, `document_version`, `items`, `issues`, `usage`다. 각 서류에는 `stage`, `submit_to`, `name`, `requirement_type`, `alternative_group`, `condition_note`, `validity_note`, `source_attachment_id`, `source_location`, `source_quote`가 있다. `position`은 저장 시 단계별 표시 순서로 생성한다.

DB 담당이 `required_document_item`에 저장할 때 원문 인용과 첨부 버전을 다시 확인하고 `verification_status=REVIEW`로 둔다. `review_complete=false`인 LLM 출력은 검수 완료를 뜻하지 않는다. 같은 공고의 기존 항목과 충돌하면 자동으로 덮어쓰지 않는다.

## 전환할 때 합의할 사항

1. 운영 DB가 SQLite인지 다른 DB인지, 실제 스키마와 접근 방식.
2. 공고·첨부 식별자 매핑과 정정 공고의 버전 무효화 방식.
3. `REVIEW` 결과를 화면에 표시할지, 추가 검토 후에만 표시할지.
4. API 호출 대상 선정, 비용 한도, 실패 재시도와 로그 보관 주체.

현재 기본 운영 DB 파일은 이 저장소에 없어서 실제 운영 DB 연결 검증은 아직 할 수 없다. LLM API 실호출도 별도 `--execute` 명령을 실행해야 한다.
