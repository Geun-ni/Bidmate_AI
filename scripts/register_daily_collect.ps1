# Windows 작업 스케줄러에 '매일 12:00(이 PC 시간대 = 한국시간) 수집'을 등록한다.
#   powershell -ExecutionPolicy Bypass -File scripts\register_daily_collect.ps1
#   해제: Unregister-ScheduledTask -TaskName BidmateDailyCollect -Confirm:$false
# 현재 사용자 계정으로, 로그인돼 있을 때 실행된다. 12:00에 PC가 꺼져 있었으면 켜진 뒤 한 번 실행한다(StartWhenAvailable).
# 배터리 상태에서도 실행한다.
param(
    [string]$TaskName = "BidmateDailyCollect",
    [string]$At = "12:00"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $PSScriptRoot "collect_daily.ps1"
$Python = (Get-Command python -ErrorAction Stop).Source
# Microsoft Store 별칭(WindowsApps\python.exe)은 예약 실행에서 동작하지 않을 수 있어 실제 설치 경로를 우선 사용
$Real = Get-Command python -All | Where-Object { $_.Source -notlike "*WindowsApps*" } | Select-Object -First 1
if ($Real) { $Python = $Real.Source }

if ((Get-TimeZone).Id -ne "Korea Standard Time") {
    Write-Warning "이 PC 시간대가 한국시간이 아닙니다: $((Get-TimeZone).Id). 등록 시각 $At 은 이 PC 시간 기준입니다."
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`" -Python `"$Python`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# 노트북 배터리 상태에서도 실행 (기본값은 배터리면 시작하지 않고 '대기(Queued)'로 남음)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "입찰메이트: 한전 공고 일일 증분 수집 (backend.collector.collect --mode daily)" -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
"등록됨: $($task.TaskName) 상태=$($task.State) 다음 실행=$($info.NextRunTime) python=$Python"
