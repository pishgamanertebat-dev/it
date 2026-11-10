# Native Excel expects these profile directories in noninteractive sessions.
# Create missing empty directories only; no security/registry settings change.
$ErrorActionPreference = 'Stop'
$created = @()
foreach ($desktopPath in @('C:\Windows\System32\config\systemprofile\Desktop', 'C:\Windows\SysWOW64\config\systemprofile\Desktop')) {
    if (-not (Test-Path -LiteralPath $desktopPath)) {
        New-Item -ItemType Directory -Path $desktopPath | Out-Null
        $created += $desktopPath
    }
}
$created | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $PSScriptRoot '..\..\runtime\scheduler\excel-service-directories.json')
& (Join-Path $PSScriptRoot 'verify_repairs_service.ps1')
