# Verify native Excel under the SAME noninteractive identity as the live
# scheduler. This exports a local PDF only; no Bale message is sent.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $projectRoot
$taskName = 'KomatsoAI Repairs Verification'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Verification task already exists' }
$liveTask = Get-ScheduledTask -TaskName 'KomatsoAI Schedules'
$verifyAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $PSScriptRoot + '\probe_repairs.ps1"') -WorkingDirectory $projectRoot
$verifyTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddHours(1)
$verifySettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $verifyAction -Trigger $verifyTrigger -Settings $verifySettings -Principal $liveTask.Principal | Out-Null
try {
    Start-ScheduledTask -TaskName $taskName
    $deadline = (Get-Date).AddMinutes(10)
    do {
        Start-Sleep -Seconds 2
        $info = Get-ScheduledTaskInfo -TaskName $taskName
        $task = Get-ScheduledTask -TaskName $taskName
    } while (($task.State -eq 'Running' -or $info.LastRunTime.Year -lt 2026) -and (Get-Date) -lt $deadline)
    $info | Select-Object LastRunTime, LastTaskResult | ConvertTo-Json | Set-Content -Encoding UTF8 runtime/scheduler/repairs-service-check.json
    if ($task.State -eq 'Running' -or $info.LastTaskResult -ne 0) { throw 'Service PDF verification failed; inspect repairs-probe.log' }
}
finally {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
