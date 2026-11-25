# 제출서류 추출과 DB 이관

제공한 두 CSV를 공고별로 연결해 로컬 SQLite에 원문과 서류 후보를 저장한다. 제출서류는 공유 스키마의 13개 컬럼을 가진 `required_document_item`을 따른다. 현재 저장소의 예전 `backend/db/schema.sql`과 공유 스키마는 달라, 별도 로컬 DB에서 작업한다. 실제 업체별 점수 입력인 `company_evaluation_draft`에는 서류를 적재하지 않는다.

## 권장 작업 흐름

1. CSV를 원문 그대로 보존하고 `bid_attachment.id = bid_document_content.attachment_id`로 연결한다.
2. `text_1`, `text_2`, `text_3`을 구분자 없이 순서대로 합친다. 문서별 파일 해시와 합친 텍스트 해시를 저장한다.
3. 공고문과 적격심사기준을 함께 읽어 서류명·단계·제출부서·조건·기간을 구조화한다. 정규식 후보는 탐색 보조로 사용하고, 문맥은 LLM으로 추출한 뒤 원문을 검수한다.
4. 검수 결과를 로컬 테이블에 저장하고, 운영 DB가 준비되면 공고·첨부를 먼저 적재한 뒤 검수된 서류만 이관한다.

## 실행

Python 3.10 이상과 표준 라이브러리만 필요하다. 저장소 루트에서 실행한다.

```bash
python -m ai.document_staging ingest 실제데이터/bidmate_csv_20261121
python -m ai.document_staging export .local/document-export
```

기본 DB는 `.local/document-staging.sqlite3`다. 다른 경로는 하위 명령 앞에 `--db 경로`로 지정한다. 입력 CSV는 수정하지 않는다. 로컬 DB와 내보낸 파일은 `.local/`에 있으며 Git에서 제외된다.

| 로컬 테이블 | 용도 |
| --- | --- |
| staging_notice | 공고번호만 가진 연결용 테이블. 운영 bid_notice의 대체용 임시 키 집합 |
| staging_source | 파일명·공고번호·첨부 ID·파일 해시·텍스트 해시·원문·수집 메타데이터 |
| required_document_item | 공유 스키마와 같은 제출서류 13개 컬럼 |
| staging_evidence | 항목별 원문 인용, 주변 문맥, 해시, 추출기 버전 |
| notice_analysis_cache | 이 로컬 제출서류 작업의 진행 상태와 입력 버전 |

초기 추출은 서류명이 나타나는 문장을 통째로 보존하며 모든 항목을 `REVIEW`로 둔다. 반복 인용·단순 언급도 포함될 수 있고, 서류명을 표현하지 않은 요구사항은 놓칠 수 있다. 단계·제출부서·조건의 기본 분류도 검수 대상이다. `alternative_group`과 `validity_note`는 초기 후보에서 확정하지 않는다.

같은 입력을 다시 적재하면 기존 결과를 유지한다. 같은 공고의 첨부 원문이나 메타데이터가 변경되면 해당 공고의 이전 결과를 다시 생성하고 검수를 무효화한다. 기존 CSV에서 사라진 첨부는 자동 삭제하지 않는다. 첨부 목록 자체가 변경된 새 스냅샷은 새로운 `--db` 경로에 적재한다. 로컬 첨부 ID가 다른 공고·파일에 재사용되면 오류로 중단한다.

## 추출과 검수

내보낸 `llm-input.jsonl`은 공고 한 건당 한 줄로 전체 원문과 입력 버전을 담는다. `required_documents_prompt.txt`와 공고별 입력을 함께 사용한다. 자동 OpenAI 추출과 평가 명령은 [LLM 실행 안내](LLM_DOCUMENTS.md)를 따른다.

`review.jsonl`에는 원문 근거를 포함한 초기 후보가 있다. 공고별 항목을 수정·추가·삭제하고, 중복과 제출 의무를 확인한다. 한 줄은 한 공고의 전체 결과이므로 필요한 공고 줄만 따로 복사해 검수할 수 있다.

- 기본서류, 참가자격, 적격심사, 감독부서의 제출 단계를 구분한다.
- 가점 필요시 서류와 필수 서류, 실적 경로와 인력·시공인증 경로를 구분한다.
- 다중 서류가 필요한 경로는 `condition_note`에 AND/OR 관계를 보존한다. 하나만 내면 되는 서류끼리만 같은 `alternative_group`을 부여한다.
- 원본대조필, 과거 실적 기간, 발급 시점은 해당 공고 원문에서 확인한다.
- 붙여넣은 대표 공고·서류 예시는 검토 참고 자료로 활용한다. 다른 공고의 확정 제출 목록으로 복사하지 않는다.

전체 원문과 대조한 검토자가 `reviewer`를 적고 `review_complete`를 `true`로 바꿔 별도 JSONL에 저장한다. `source_quote`는 원문에 존재하는 연속 구절이어야 한다. 빈 items는 검토자가 실제로 서류가 없음을 확인한 경우에만 사용한다.

```bash
python -m ai.document_staging review .local/reviewed-notices.jsonl
python -m ai.document_staging export .local/document-export
```

원문 버전이 달라졌거나, 공고의 첨부에 본문이 누락됐거나, 인용문이 원문에 없으면 저장을 거부한다. 여러 공고 중 하나가 실패해도 검수 저장 전체가 롤백된다. 인용문 일치 검사는 의미상 정확성을 보장하지 않으므로 사람의 원문 대조가 필요하다.

검수 후 `required_document_item.verified.csv`에 승인된 행이 나온다. 출력에는 12개 대상 컬럼과 별도 출처 매핑용 컬럼이 있고, 로컬 자동증가 `id`는 포함하지 않는다. 대상 DB가 `id`를 새로 발급하도록 한다. `report.json`에서 원문 누락과 상태별 건수를 확인한다.

## 운영 DB로 이관

운영 DB 종류가 정해지면 13개 컬럼의 타입·제약을 실제 DDL과 다시 비교한다. SQLite 대상에는 먼저 검증 모드로 실행할 수 있다.

```bash
python -m ai.document_staging migrate 운영DB.sqlite3
python -m ai.document_staging migrate 운영DB.sqlite3 --apply
```

검증 모드는 트랜잭션을 롤백하고 예정 건수만 출력한다. `--apply`는 검증된 항목만 삽입한다. 운영 DB에는 공유 스키마의 `bid_notice`, `bid_attachment`, `bid_document_content`, `required_document_item`이 미리 있어야 한다.

공고번호·파일명·파일 해시로 대상 첨부를 찾아 `source_attachment_id`를 재매핑하고 대상 원문에서도 인용문을 확인한다. 같은 공고·단계·순서의 기존 행이 동일하면 건너뛰고, 내용이 다르면 전체 작업을 롤백한다. 기존 항목을 교체해야 한다면 백엔드와 함께 `company_document_check.item_id` 참조를 보존하는 마이그레이션을 설계한다.

PostgreSQL 등으로 옮길 때도 검수 CSV와 출처 증거를 사용해 동일한 매핑·충돌 검사를 수행하는 어댑터를 추가한다. SQLite의 자동증가 ID를 그대로 복사하지 않는다. 운영 `notice_analysis_cache`는 다른 분석도 공유하므로 로컬 상태를 그대로 덮어쓰지 않는다.
