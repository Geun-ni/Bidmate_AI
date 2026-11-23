# OpenAI 제출서류 추출 및 평가

Python 표준 라이브러리로 OpenAI Responses API의 Structured Outputs를 호출한다. 기본 모델은 `gpt-6-luna`이며 환경변수 `OPENAI_MODEL` 또는 `--model`로 변경할 수 있다. 모델은 [공식 가격표](https://developers.openai.com/api/docs/pricing)와 [모델 문서](https://developers.openai.com/api/docs/models/gpt-6-luna)를 확인해 저가 모델로 선택했다. 실제 비용은 토큰 사용량과 처리 방식에 따라 달라지며, 추출과 LLM 평가는 별도 호출이다.

## 원문 준비와 추출

저장소 루트의 PowerShell에서 실행한다. 루트의 `.env`에 OPENAI_API_KEY를 입력하면 추출·평가 CLI가 자동으로 읽는다. 이미 설정된 환경변수가 우선한다. `.env.example`은 설정 예시다.

```powershell
$env:OPENAI_API_KEY = '자신의 API 키'
python -m ai.document_staging ingest 실제데이터/bidmate_csv_20261121
python -m ai.llm_documents --notice E012608220
python -m ai.llm_documents --notice E012608220 --execute
```

`--execute`가 없으면 원문 누락, 공고문·기준 문서 확보, 입력 크기를 검사하고 호출 계획만 출력한다. `--all --execute`로 전체 공고를 순차 처리할 수 있다. 일부 공고가 실패해도 다른 공고를 처리하며, 실패가 있으면 종료 코드 1을 반환한다.

추출 입력에는 해당 공고의 전체 원문이 들어간다. 정규식 후보는 입력에 포함하지 않는다. 입력이 `--max-chars` 한도를 넘으면 중단한다. 본문을 자르지 않으며 긴 문서는 모델 문맥 한도를 확인한 뒤 한도를 조정한다.

LLM 결과는 원문 인용, 첨부 ID, 모든 첨부의 검토 범위, 단계·부서 일치, 조건부 서류의 조건, 택일 그룹, 중복을 검사한다. 이 검사는 내용의 완전성이나 정확성을 보장하지 않는다. 일부 항목의 검증이 실패하면 해당 공고의 기존 결과를 유지한다.

정상 결과는 SQLite `required_document_item`에 `REVIEW`로 저장하며, 기존 정규식 후보를 공고 단위로 대체한다. 이미 사람이 검수해 `DONE`이 된 공고는 보존한다. `.local/llm-documents/`에 API 원본 응답과 검토용 `*.review.jsonl`을 저장한다. 동일 입력·모델·프롬프트·출력 설정의 응답은 캐시를 재사용한다. 실패하거나 잘린 응답도 기록되므로 같은 요청을 유료로 다시 시도하려면 해당 응답 파일을 별도 보관한 후 제거한다.

출력의 usage에서 실제 입력·출력 토큰을 확인한다. 429와 일시적인 서버 오류만 최대 세 번 시도한다. 응답 수신 여부가 모호한 네트워크 오류는 자동 재호출하지 않는다.

## LLM 검토

추출 명령이 출력한 review_file 경로를 인자로 넣는다. 필요하면 여러 공고의 JSONL 줄을 하나의 파일로 모아 평가한다.

```powershell
python -m ai.evaluate_documents judge .local/llm-documents/요청해시.review.jsonl
python -m ai.evaluate_documents judge .local/llm-documents/요청해시.review.jsonl --execute
```

평가 LLM은 전체 원문과 추출 항목을 새 요청에서 대조한다. 누락, 과다 추출, 단계·부서·조건·발급 시점·택일 오류, 중복, 불확실성을 보고한다. 각 지적에는 실제 원문 인용을 요구하고 코드가 다시 대조한다. 모든 첨부를 검토하지 않았거나 인용문이 실제 원문에 없으면 보고서를 거부한다.

결과는 `.local/evaluation/judge/*.audit.json`에 저장한다. `PASS`는 모델의 검토 의견이며, SQLite 검수 상태는 바꾸지 않는다. 같은 저가 모델로 추출과 평가하면 같은 오류를 반복할 수 있다. 평가만 `--model`로 다른 모델을 지정할 수 있다.

## 사람의 정답과 비교

공사유형·추정가격 구간별 대표 공고를 선정하고 사람이 전체 원문에서 정답을 작성한다. 추출 결과를 그대로 정답으로 사용하지 않는다. 정답 JSONL은 검토용 파일과 같은 notice_no, document_version, items를 포함하며, reviewer와 review_complete를 명시한다. 아래 예시의 공고와 버전은 예시 값이다.

```json
{"notice_no":"EXAMPLE","document_version":"실제 입력 버전","reviewer":"검토자","review_complete":true,"items":[{"name":"사업자등록증","aliases":["사업자등록증 사본"],"stage":"BASIC","submit_to":"CONTRACT_DEPT","requirement_type":"REQUIRED","condition_note":null,"validity_note":null,"alternative_group":null}]}
```

```powershell
python -m ai.evaluate_documents gold .local/gold.jsonl .local/predictions.jsonl
```

`.local/evaluation/gold-report.json`에 precision, recall, F1, 누락·추가 항목, 필드 차이가 저장된다. 이름은 공백·가운데점을 정규화하고 정답에 명시한 aliases만 허용하며 단계까지 일치해야 맞춘 항목이다. 중복 예측은 추가 항목으로 센다. 공통된 정답이 없는 예측 공고는 별도 표시하고 점수에 포함하지 않는다.

조건 문구와 유효기간은 문자열 차이로 보고하므로 뜻이 같은 표현은 사람이 확인한다. 이름·단계 점수가 높더라도 조건·택일·발급시점의 차이는 별도로 검토한다. 예측이 없는 정답 공고는 누락으로 센다. 같은 원문 버전끼리만 비교한다.

## 검토 후 승인 및 이관

추출의 issues와 평가 지적을 원문에서 확인하고 검토용 JSONL을 수정한다. 미해결 사항이 있으면 검토를 계속한다. 해결한 issues는 빈 배열로 바꾸고 reviewer와 review_complete를 작성한다.

```powershell
python -m ai.document_staging review .local/reviewed-notices.jsonl
python -m ai.document_staging export .local/document-export
```

운영 DB 이관은 [SQLite 이관 안내](DOCUMENT_EXTRACTION.md)를 따른다. API 모델 평가 결과만으로 VERIFIED나 운영 DB 반영 상태를 변경하지 않는다.

## 로컬 Ollama로 실행

`.env`에 `LLM_PROVIDER=ollama`, `OLLAMA_MODEL=qwen3.5:4b`를 지정하거나 명령에서 provider를 선택한다.

```powershell
python -m ai.llm_documents --provider ollama --model qwen3.5:4b --notice E012608220 --execute
```

Ollama의 JSON Schema 출력을 기존 원문 검증과 SQLite 저장 흐름에 연결한다. 기본 로컬 주소는 `http://localhost:11434`이며 `OLLAMA_BASE_URL`로 변경할 수 있다. 문맥 길이는 65,536토큰이다. 메모리가 부족하거나 입력이 이를 초과하면 원문 전체가 처리됐는지 별도 확인해야 한다. 결과와 실행 시간·토큰 사용량은 응답 파일에 보존한다.

OpenAI로 실행하려면 `--provider openai --model gpt-6-luna`를 지정한다. LLM 평가의 `judge` 명령은 현재 OpenAI를 사용한다.

## 코드 검증

```powershell
python -m unittest ai.test_document_staging ai.test_llm_documents -v
```

테스트는 가상 응답을 사용해 실제 요금을 발생시키지 않는다. 실제 공고의 정확도는 API 실행 결과와 사람이 작성한 정답이 준비된 뒤 측정한다.
