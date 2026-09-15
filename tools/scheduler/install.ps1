param([switch]$Replace)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $pythonPath -X utf8 -m tools.scheduler.runner --check
if ($LASTEXITCODE -ne 0) { throw 'Schedule validation failed' }
$taskName = 'KomatsoAI Schedules'
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask -and -not $Replace) { throw 'Task already exists. Inspect it before using -Replace.' }
if ($existingTask) {
    $backupDir = Join-Path $projectRoot 'backup\scheduler'
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    Export-ScheduledTask -TaskName $taskName | Set-Content -Encoding Unicode -LiteralPath (Join-Path $backupDir ('task-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.xml'))
}
$scheduleAction = New-ScheduledTaskAction -Execute $pythonPath -Argument '-E -s -B -X utf8 -m tools.scheduler.runner --tick' -WorkingDirectory $projectRoot
$startMinute = (Get-Date).Date.AddMinutes([Math]::Floor((Get-Date).TimeOfDay.TotalMinutes) + 1)
# No RepetitionDuration means indefinite repetition, including after reboot.
$scheduleTrigger = New-ScheduledTaskTrigger -Once -At $startMinute -RepetitionInterval (New-TimeSpan -Minutes 1)
$scheduleSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
# S4U runs without an interactive login or stored password. All source/state
# paths are local; HTTPS Bale requests use the bot token, not Windows credentials.
$scheduleUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$schedulePrincipal = New-ScheduledTaskPrincipal -UserId $scheduleUser -LogonType S4U -RunLevel Limited
$taskParams = @{
    TaskName = $taskName
    Action = $scheduleAction
    Trigger = $scheduleTrigger
    Settings = $scheduleSettings
    Principal = $schedulePrincipal
    Description = 'KomatsoAI YAML schedules; server-local clock; persistent execution ledger'
}
Register-ScheduledTask @taskParams -Force:$Replace | Select-Object TaskName, State
Start-ScheduledTask -TaskName $taskName
Get-ScheduledTaskInfo -TaskName $taskName | Select-Object NextRunTime, LastTaskResult
