# 제출서류 추출 및 평가 빠른 사용법

프로젝트 폴더의 PowerShell에서 실행한다. Python 3.10 이상이 필요하다.

진행 순서는 CSV 적재 → LLM 추출 → LLM 평가 → 사람 검수 → 저장·내보내기다.

## 1. API 키 설정

```powershell
$env:OPENAI_API_KEY = '자신의 API 키'
```

또는 프로젝트 루트의 `.env`에서 `OPENAI_API_KEY=` 뒤에 키를 입력한다. 추출·평가 명령이 자동으로 읽는다. 기존 환경변수가 있으면 그 값이 우선한다. 기본 모델은 `gpt-6-luna`다.

## 2. CSV를 SQLite에 적재

```powershell
python -m ai.document_staging ingest 실제데이터/bidmate_csv_20261121
```

로컬 DB는 `.local/document-staging.sqlite3`에 저장된다.

## API 없이 명시적 적격심사서류 후보 추출

```powershell
python -m ai.rule_documents --split development --output .local/rule-documents-development.jsonl
```

`--split`은 `development`, `validation`, `test` 중 하나다. 공고문·적격심사기준
본문에서 명시적 적격심사 제출 목록을 찾고, 각 후보에 원문 근거를 붙인다. 결과는
모두 `REVIEW`이며 기존 SQLite 후보나 운영 DB를 덮어쓰지 않는다. 복합 대체 관계와
참조 서류는 [25건 재평가 보고서](splits/FINAL_TEST_REPORT.md)를 확인한다.

## 3. 공고 한 건 추출

```powershell
python -m ai.llm_documents --notice E012608220
python -m ai.llm_documents --notice E012608220 --execute
```

첫 명령은 사전 검사, 두 번째는 유료 API 호출이다. 출력된 `review_file` 경로를 복사한다. 추출 항목은 검수 대기 상태인 `REVIEW`로 저장된다.

## 4. LLM으로 추출 결과 평가

```powershell
python -m ai.evaluate_documents judge '복사한 review_file 경로' --execute
```

평가에도 API 비용이 발생한다. 출력된 `report_file`을 열어 누락·추가·조건 오류와 원문 근거를 확인한다.

## 5. 사람이 확인하고 검수 완료 저장

`review_file`의 항목을 원문과 대조해 수정한다. 검토를 마치면 `reviewer`에 검토자 이름, `review_complete`에 `true`를 적는다. 미해결 사항을 처리한 뒤 `issues`를 `[]`로 바꾼다.

파일을 `.local/reviewed-notices.jsonl`로 저장하고 실행한다.

```powershell
python -m ai.document_staging review .local/reviewed-notices.jsonl
python -m ai.document_staging export .local/document-export
```

검수된 결과는 `VERIFIED`로 저장되고, 이관용 파일은 `.local/document-export/required_document_item.verified.csv`에 생성된다.

## 정답 비교 평가

사람이 작성한 정답과 같은 공고·원문 버전의 추출 결과를 비교한다.

```powershell
python -m ai.evaluate_documents gold .local/gold.jsonl .local/predictions.jsonl
```

API 비용 없이 precision·recall·F1과 누락·추가 항목을 계산한다. 결과는 `.local/evaluation/gold-report.json`에 저장된다.

전체 공고 추출은 `python -m ai.llm_documents --all --execute`로 실행한다. 운영 DB 이관과 상세 옵션은 [상세 실행 안내](LLM_DOCUMENTS.md)와 [DB 이관 안내](DOCUMENT_EXTRACTION.md)를 참고한다.
