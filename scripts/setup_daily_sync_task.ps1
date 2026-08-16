# Registers a Windows Scheduled Task that runs `sync daily-sync` once a day -- the same
# garmin_connect incremental sync + staleness check the `worker` container's APScheduler job
# does (see src/sporthealth/worker/main.py), but via a plain OS scheduler instead of a
# container. Podman on Windows is known to corrupt this project's SQLite WAL file (see
# CLAUDE.md), so the `worker` container is a NAS-deployment thing, not a Windows-dev thing --
# this is the Windows-native equivalent, following the same proven pattern as the sibling
# eufy-health-sync project's own setup_scheduler.ps1.
#
# Run this script once (no admin rights required for a per-user scheduled task).

$repoRoot = Split-Path -Parent $PSScriptRoot
$syncExe = Join-Path $repoRoot ".venv\Scripts\sync.exe"

if (-not (Test-Path $syncExe)) {
    throw "sync.exe not found at $syncExe -- run 'uv sync' in $repoRoot first."
}

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable

# Matches the worker container's own default schedule (04:15 local, see config.py's
# schedule_hour/schedule_minute) -- change the trigger time here if you've overridden those.
$action = New-ScheduledTaskAction -Execute $syncExe -Argument "daily-sync" -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At "04:15"

Register-ScheduledTask `
    -TaskName "SportHealthDailySync" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "sporthealth: daily garmin_connect incremental sync + staleness check" `
    -Force

Write-Host "Task 'SportHealthDailySync' registered (daily at 04:15)." -ForegroundColor Green
Write-Host "To run it manually right now: Start-ScheduledTask -TaskName SportHealthDailySync"
Write-Host "To check its last result: Get-ScheduledTaskInfo -TaskName SportHealthDailySync"
