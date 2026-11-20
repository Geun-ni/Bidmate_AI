# 매일 예약 실행용: 일일 증분 수집(--mode daily)을 실행하고 로그를 logs/collect_YYYYMMDD.log 에 남긴다.
# 마지막으로 목록을 정상 반영한 날 - 7일 ~ 오늘 공고일 범위를 조회해 신규·변경 공고를 갱신하고,
# 신규·정정·첨부변경·미완료 공고만 상세 수집한다 (건수 제한 없음, 못 끝낸 공고는 다음 실행에서 이어서).
# 최초 1회는 수동으로: python -m backend.collector.collect --mode full
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Log = Join-Path $LogDir ("collect_{0}.log" -f (Get-Date -Format "yyyyMMdd"))

$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false  # python 출력(UTF-8)을 그대로 읽기
"===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 수집 시작" | Out-File -Append -Encoding utf8 $Log
# Windows PowerShell 5.1 은 stderr 를 오류 레코드로 감싸므로 이 구간만 Continue
$ErrorActionPreference = "Continue"
& $Python -m backend.collector.collect --mode daily 2>&1 | ForEach-Object { "$_" } | Out-File -Append -Encoding utf8 $Log
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"
"===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 종료 코드 $code" | Out-File -Append -Encoding utf8 $Log
exit $code
