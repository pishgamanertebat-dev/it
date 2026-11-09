param([Parameter(Mandatory=$true)][string]$EvidenceDirectory)
$ErrorActionPreference = 'Stop'
$base = [System.IO.Path]::GetFullPath($EvidenceDirectory)
$workspace = 'E:\KomatsoAI\runtime\ai-daily-entry-20261109\'
if (-not $base.StartsWith($workspace, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Only Phase 4 copies may be opened' }
$report = Get-Content -LiteralPath (Join-Path $base 'evidence.json') -Encoding UTF8 -Raw | ConvertFrom-Json
if (-not $report.passed) { throw 'Rehearsal must pass first' }
$resolved = [System.IO.Path]::GetFullPath($report.output)
if (-not $resolved.StartsWith($workspace, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Operational workbook forbidden' }
$beforeHash = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash
$application = $null
$book = $null
try {
    $application = New-Object -ComObject Excel.Application
    $application.Visible = $false
    $application.DisplayAlerts = $false
    $application.AskToUpdateLinks = $false
    $application.EnableEvents = $false
    $application.AutomationSecurity = 3
    $book = $application.Workbooks.Open($resolved, 0, $true)
    if (-not $book.ReadOnly -or $book.RepairMode) { throw 'Normal read-only open required' }
    $result = @{path=$resolved; sha256=$beforeHash; sheets=$book.Worksheets.Count; normal_open=$true; repair_mode=$false; readonly=$true; excel_version=$application.Version}
} finally {
    if ($null -ne $book) { $book.Close($false); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($book) }
    if ($null -ne $application) { $application.Quit(); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
if ((Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash -ne $beforeHash) { throw 'Copy changed' }
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $base 'native-excel.json') -Encoding UTF8
$result | ConvertTo-Json -Depth 6
