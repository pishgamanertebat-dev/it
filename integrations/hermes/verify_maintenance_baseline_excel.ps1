$ErrorActionPreference = 'Stop'
$base = 'E:\KomatsoAI\runtime\maintenance-baseline-20261107'
$report = Get-Content -LiteralPath (Join-Path $base 'real-copy.json') -Encoding UTF8 -Raw | ConvertFrom-Json
if (-not $report.passed) { throw 'Isolated real Workbook/Journal rehearsal must pass first' }
$application = $null
$results = @()
try {
    $application = New-Object -ComObject Excel.Application
    $application.Visible = $false
    $application.DisplayAlerts = $false
    $application.AskToUpdateLinks = $false
    $application.EnableEvents = $false
    $application.AutomationSecurity = 3
    foreach ($name in $report.native_files) {
        $resolved = [System.IO.Path]::GetFullPath($name)
        if (-not $resolved.StartsWith($base + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'Only isolated copies can be opened for validation'
        }
        $beforeHash = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash
        $book = $null
        try {
            $book = $application.Workbooks.Open($resolved, 0, $true)
            if (-not $book.ReadOnly -or $book.RepairMode) { throw 'Normal read-only open required; repair refused' }
            $areas = @()
            for ($index = 1; $index -le $book.Worksheets.Count; $index++) {
                $sheet = $book.Worksheets.Item($index)
                try {
                    $areas += @{name=$sheet.Name; used_range=$sheet.UsedRange.Address(); print_area=$sheet.PageSetup.PrintArea}
                } finally {
                    [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($sheet)
                }
            }
            $results += @{path=$resolved; sha256=$beforeHash; sheets=$book.Worksheets.Count;
                normal_open=$true; repair_mode=$false; readonly=$true;
                excel_version=$application.Version; sheet_areas=$areas}
        } finally {
            if ($null -ne $book) {
                $book.Close($false)
                [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($book)
            }
        }
        if ((Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash -ne $beforeHash) {
            throw 'Read-only validation changed a copy'
        }
    }
} finally {
    if ($null -ne $application) {
        $application.Quit()
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($application)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
$results | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $base 'native-excel.json') -Encoding UTF8
$acceptancePath = Join-Path $base 'acceptance.json'
$acceptance = Get-Content -LiteralPath $acceptancePath -Encoding UTF8 -Raw | ConvertFrom-Json
$acceptance.native_excel_passed = $true
$acceptance | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $acceptancePath -Encoding UTF8
$results | Select-Object path,sheets,normal_open,repair_mode,readonly,excel_version | ConvertTo-Json
