$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $projectRoot
try {
    foreach ($section in @('mechanical', 'metalwork')) {
        $arguments = '-E -s -B -X utf8 -m tools.fleet.repairs.report --section ' + $section + ' --output-dir artifacts/repairs-service-preview/' + $section
        $probe = Start-Process -FilePath (Join-Path $projectRoot '.venv\Scripts\python.exe') -WindowStyle Hidden -ArgumentList $arguments -WorkingDirectory $projectRoot -RedirectStandardOutput (Join-Path $projectRoot ('runtime/scheduler/repairs-probe-' + $section + '.log')) -RedirectStandardError (Join-Path $projectRoot ('runtime/scheduler/repairs-probe-' + $section + '-error.log')) -PassThru -Wait
        if ($probe.ExitCode -ne 0) { exit $probe.ExitCode }
    }
    exit 0
}
catch {
    $_ | Out-String | Set-Content -Encoding UTF8 runtime/scheduler/repairs-probe-error.log
    exit 1
}
