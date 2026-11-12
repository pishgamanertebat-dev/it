$ErrorActionPreference = 'Stop'
$base = 'E:\KomatsoAI\runtime\sync-repair-20261112'
$target = Join-Path $base 'recovered-candidate.xlsx'
$before = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
$excel = $null
$book = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $excel.EnableEvents = $false
    $excel.AutomationSecurity = 3
    # Read/write open of a private copy verifies the owner's Read-only symptom.
    # Never save it and never open the live workbook through Excel.
    $book = $excel.Workbooks.Open($target, 0, $false)
    $result = @{read_only=[bool]$book.ReadOnly; repair_mode=[bool]$book.RepairMode;
        sheets=$book.Worksheets.Count; version=$excel.Version; sha256=$before}
    if ($book.ReadOnly -or $book.RepairMode -or $book.Worksheets.Count -ne 63) {
        throw 'Native Excel acceptance failed'
    }
} finally {
    if ($null -ne $book) {
        $book.Close($false)
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($book)
    }
    if ($null -ne $excel) {
        $excel.Quit()
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $before) {
    throw 'Validation changed candidate bytes'
}
$result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $base 'native-excel.json') -Encoding UTF8
$result | ConvertTo-Json
