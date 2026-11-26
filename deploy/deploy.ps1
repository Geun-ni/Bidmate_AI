# 서버 배포 (PC 의 PowerShell 에서 실행). 자동 배포는 없다 — main 에 머지된 것을 반영할 때 이것을 돌린다.
#
#   .\deploy\deploy.ps1                 # 백엔드 + 화면
#   .\deploy\deploy.ps1 -SkipFrontend   # 백엔드만 (화면이 안 바뀌었을 때)
#
# 하는 일
#   0. PC 가 최신 main 인지 확인 (화면은 PC 에서 빌드하므로, 다른 브랜치면 그 브랜치 화면이 올라간다 → 멈춤)
#   1. 서버 DB 백업 (bidmate.sqlite3.bak-시각)
#   2. 서버 코드를 GitHub main 으로 맞추고 스키마 적용(init_db, 새 테이블만 추가) 후 백엔드 재시작 (+ 원격 MCP 재시작)
#   3. 화면 빌드(frontend-react/dist) → 서버 /var/www/bidmate 에 덮어쓰기 (사이트 폴더를 지우지 않는다)
#   4. 사이트·API 응답 확인
# 배점 룰 재추출·발주부서 채우기 같은 데이터 정리는 필요할 때만 따로 돌린다 (backend/db/README.md).
param([switch]$SkipFrontend)

$ErrorActionPreference = "Stop"
$Key = "$HOME\.ssh\bidmate_ed25519"
$Server = "root@211.233.216.208"
$Site = "https://211-233-216-208.sslip.io"
$Root = Split-Path -Parent $PSScriptRoot

function Invoke-Remote([string]$Command) {
    ssh -i $Key $Server $Command
    if ($LASTEXITCODE -ne 0) { throw "서버 명령 실패 (exit $LASTEXITCODE): $Command" }
}

Set-Location $Root
Write-Host "0. PC 가 최신 main 인지 확인"
git fetch -q origin main
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$head = (git rev-parse HEAD).Trim()
$main = (git rev-parse origin/main).Trim()
if (-not $SkipFrontend) {
    if ($branch -ne "main" -or $head -ne $main) {
        throw "PC 가 최신 main 이 아닙니다 (지금 $branch). 'git switch main; git pull' 후 다시 실행하세요."
    }
    if (git status --porcelain -- frontend-react) { throw "frontend-react 에 커밋 안 한 변경이 있습니다." }
}
Write-Host "   배포할 main: $(git log -1 --format='%h %s' origin/main)"

Write-Host "1. 서버 DB 백업"
Invoke-Remote 'cd /opt/bidmate/app && sudo -u bidmate cp -p bidmate.sqlite3 bidmate.sqlite3.bak-$(date +%Y%m%d%H%M%S) && ls -t bidmate.sqlite3.bak-* | head -1'

Write-Host "2. 서버 코드 main 으로 맞추기 + 스키마 적용 + 재시작"
Invoke-Remote ('cd /opt/bidmate/app && sudo -u bidmate git fetch -q origin main && ' +
               'sudo -u bidmate git checkout -q -B main FETCH_HEAD && sudo -u bidmate git log --oneline -1 && ' +
               'sudo -u bidmate /opt/bidmate/venv/bin/python -m backend.db.init_db && ' +
               'systemctl restart bidmate-backend && sleep 2 && systemctl is-active bidmate-backend')
# 원격 MCP 는 서비스가 등록돼 있을 때만 (deploy/bidmate-mcp.service)
Invoke-Remote ('if [ -f /etc/systemd/system/bidmate-mcp.service ]; then cd /opt/bidmate/app && ' +
               '/opt/bidmate/venv/bin/pip install -q -r bidmate_mcp/requirements.txt && ' +
               'systemctl restart bidmate-mcp && sleep 2 && echo "mcp: $(systemctl is-active bidmate-mcp)"; fi')

if (-not $SkipFrontend) {
    Write-Host "3. 화면 빌드 후 올리기"
    Push-Location frontend-react
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci 실패" }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build 실패" }
    } finally { Pop-Location }
    Invoke-Remote 'rm -rf /tmp/bidmate-dist'
    scp -i $Key -r -q frontend-react/dist "${Server}:/tmp/bidmate-dist"
    if ($LASTEXITCODE -ne 0) { throw "scp 실패" }
    Invoke-Remote 'cp -r /tmp/bidmate-dist/. /var/www/bidmate/ && chmod -R a+rX /var/www/bidmate && rm -rf /tmp/bidmate-dist'
} else {
    Write-Host "3. 화면은 건너뜀 (-SkipFrontend)"
}

Write-Host "4. 확인"
foreach ($path in "/", "/api/notices") {
    $code = (Invoke-WebRequest -Uri "$Site$path" -UseBasicParsing -TimeoutSec 30).StatusCode
    Write-Host "   $path → $code"
}
Write-Host "배포 끝: $Site"
