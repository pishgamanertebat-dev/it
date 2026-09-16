$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $projectRoot
try {
    $probe = Start-Process -FilePath (Join-Path $projectRoot '.venv\Scripts\python.exe') -WindowStyle Hidden -ArgumentList '-E -s -B -X utf8 -m tools.fleet.repairs.report --output-dir artifacts/repairs-service-preview' -WorkingDirectory $projectRoot -RedirectStandardOutput (Join-Path $projectRoot 'runtime/scheduler/repairs-probe.log') -RedirectStandardError (Join-Path $projectRoot 'runtime/scheduler/repairs-probe-error.log') -PassThru -Wait
    exit $probe.ExitCode
}
catch {
    $_ | Out-String | Set-Content -Encoding UTF8 runtime/scheduler/repairs-probe-error.log
    exit 1
}
