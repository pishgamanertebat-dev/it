param([Parameter(Mandatory=$true)][string]$RequestPath, [switch]$Cleanup)
$ErrorActionPreference = 'Stop'
$ownerPath = $RequestPath + '.excel.json'
if ($Cleanup) {
    if (Test-Path -LiteralPath $ownerPath) {
        $owner = Get-Content -LiteralPath $ownerPath -Raw | ConvertFrom-Json
        $ownedProcess = Get-Process -Id $owner.pid -ErrorAction SilentlyContinue
        if ($ownedProcess -and $ownedProcess.ProcessName -eq 'EXCEL' -and $ownedProcess.StartTime.ToUniversalTime().Ticks -eq $owner.started) {
            Stop-Process -Id $ownedProcess.Id -Force
        }
    }
    exit 0
}
$request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class ExcelWindowOwner { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId); }'
$exportStarted = [DateTime]::UtcNow
$excel = $null
$books = $null
$book = $null
$sheets = $null
$sheet = $null
try {
    $excel = New-Object -ComObject Excel.Application
    [uint32]$excelProcessId = 0
    [void][ExcelWindowOwner]::GetWindowThreadProcessId([IntPtr]$excel.Hwnd, [ref]$excelProcessId)
    $excelProcess = Get-Process -Id $excelProcessId
    if ($excelProcess.StartTime.ToUniversalTime() -ge $exportStarted) {
        @{pid=$excelProcessId; started=$excelProcess.StartTime.ToUniversalTime().Ticks} | ConvertTo-Json | Set-Content -LiteralPath $ownerPath -Encoding UTF8
    }
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $excel.AutomationSecurity = 3
    $excel.EnableEvents = $false
    $books = $excel.Workbooks
    $book = $books.Open($request.source, 0, $true)
    $sheets = $book.Worksheets
    $sheet = $sheets.Item($request.sheet)
    # Ungroup worksheets so export includes this report only.
    $sheet.Select($true)
    # Operate on the temporary read-only workbook, never on the source file.
    # Hiding every non-selected column also excludes it from the PDF output.
    if (@($request.columns).Count -ne 4) { throw 'Exactly four report columns are required' }
    $allColumns = $sheet.Columns
    try {
        $allColumns.Hidden = $true
        foreach ($columnNumber in $request.columns) {
            $column = $allColumns.Item([int]$columnNumber)
            try { $column.Hidden = $false }
            finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($column) }
        }
    }
    finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($allColumns) }
    # Only the selected sheet of the private read-only copy is changed.
    # Reflow bounded continuation rows so Excel's row-height ceiling cannot
    # silently clip a long description. Process bottom-up to keep source indices.
    foreach ($part in @($request.continuations | Sort-Object row -Descending)) {
        $rowNumber = [int]$part.row
        for ($index = $part.chunks.Count - 1; $index -ge 1; $index--) {
            [void]$sheet.Rows.Item($rowNumber + 1).Insert()
            foreach ($columnNumber in @($request.columns)[0..2]) {
                $sheet.Cells.Item($rowNumber + 1, [int]$columnNumber).Value2 = [string]$sheet.Cells.Item($rowNumber, [int]$columnNumber).Value2
            }
            $sheet.Cells.Item($rowNumber + 1, [int]$request.columns[3]).Value2 = [string]$part.chunks[$index]
        }
        $sheet.Cells.Item($rowNumber, [int]$request.columns[3]).Value2 = [string]$part.chunks[0]
    }
    $lastRow = [int]$request.last_row
    foreach ($part in @($request.continuations)) { $lastRow += $part.chunks.Count - 1 }
    $lastColumn = [int](@($request.columns | Measure-Object -Maximum).Maximum)
    # Hidden text must not influence Excel AutoFit (a long description in the
    # other section can otherwise inflate every row after continuation inserts).
    for ($c = 1; $c -le [int]$request.source_columns; $c++) {
        if (@($request.columns) -notcontains $c) {
            [void]$sheet.Range($sheet.Cells.Item(1, $c), $sheet.Cells.Item($lastRow, $c)).ClearContents()
        }
    }
    $range = $sheet.Range($sheet.Cells.Item(1, 1), $sheet.Cells.Item($lastRow, $lastColumn))
    # Fonts in the source vary up to 36pt and relied on an 18% print scale.
    # A readable fixed font and a width-only fit allow unlimited vertical pages.
    $range.Font.Name = 'Tahoma'
    $range.Font.Size = 11
    $range.WrapText = $true
    $range.ShrinkToFit = $false
    $range.VerticalAlignment = -4160 # xlTop
    $range.Borders.LineStyle = 1
    $range.EntireRow.Hidden = $false
    $widths = @(6, 26, 14, 90)
    for ($index = 0; $index -lt 4; $index++) {
        $sheet.Columns.Item([int]$request.columns[$index]).ColumnWidth = $widths[$index]
    }
    # Merged report cells cannot be AutoFit safely. Fail closed for unfamiliar
    # templates instead of exporting clipped content.
    if ($range.MergeCells -ne $false) { throw 'Merged report cells require layout review' }
    $range.RowHeight = 22
    [void]$range.Rows.AutoFit()
    for ($rowNumber = 1; $rowNumber -le $lastRow; $rowNumber++) {
        $row = $sheet.Rows.Item($rowNumber)
        if ($row.RowHeight -ge 400) {
            $sizes = foreach ($c in $request.columns) { [string]$sheet.Cells.Item($rowNumber, [int]$c).Text.Length + ':' + [string]$sheet.Columns.Item([int]$c).ColumnWidth }
            throw "Report row $rowNumber exceeds safe Excel height ($($row.RowHeight)); lengths/widths $sizes"
        }
        $row.RowHeight = [Math]::Max(22, $row.RowHeight + 6)
    }
    $sheet.DisplayRightToLeft = $true
    $sheet.ResetAllPageBreaks()
    $setup = $sheet.PageSetup
    $setup.PrintArea = $range.Address()
    $setup.PrintTitleRows = '$1:$2'
    $setup.PrintTitleColumns = ''
    $setup.PaperSize = 9 # A4
    $setup.Orientation = 2 # landscape
    $setup.Zoom = $false
    $setup.FitToPagesWide = 1
    $setup.FitToPagesTall = $false
    $setup.LeftMargin = 18
    $setup.RightMargin = 18
    $setup.TopMargin = 18
    $setup.BottomMargin = 18
    $setup.CenterHorizontally = $true
    $sheet.ExportAsFixedFormat(0, $request.output, 0, $true, $false)
}
catch {
    [Console]::Error.WriteLine($_.ScriptStackTrace)
    throw
}
finally {
    try {
        if ($null -ne $book) { $book.Close($false) }
    }
    finally {
        if ($null -ne $excel) { $excel.Quit() }
        foreach ($item in @($sheet, $sheets, $book, $books, $excel)) {
            if ($null -ne $item) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($item) }
        }
    }
}
