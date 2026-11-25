param(
    [Parameter(Mandatory=$true)][string]$SourcePath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$excel = $null
$books = $null
$book = $null
$originalMapPaperSize = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $excel.AutomationSecurity = 3
    # OC is the oil-change order prefix. Prevent A4-to-Letter substitution only here.
    if ([IO.Path]::GetFileName($SourcePath) -match '^(OC|GR|AF|CW)-') {
        $originalMapPaperSize = $excel.MapPaperSize
        $excel.MapPaperSize = $false
    }
    $books = $excel.Workbooks
    $book = $books.Open($SourcePath, 0, $true)
    if ([IO.Path]::GetFileName($SourcePath).StartsWith('OC-')) {
        foreach ($sheet in $book.Worksheets) {
            $setup = $sheet.PageSetup
            try {
                # Restrict print area strictly to real content columns A..J and hide empty
                # trailing columns K:M so merged header/footer rows (A1:K1, etc.) stay centered.
                $used = $sheet.UsedRange
                $maxRow = [Math]::Max(1, [int]$used.Row + [int]$used.Rows.Count - 1)
                $lastRow = 1
                for ($r = $maxRow; $r -ge 1; $r--) {
                    $hasContent = $false
                    for ($c = 1; $c -le 10; $c++) {
                        $val = $sheet.Cells.Item($r, $c).Value2
                        if ($null -ne $val -and ([string]$val).Trim() -ne '') {
                            $hasContent = $true
                            break
                        }
                    }
                    if ($hasContent) {
                        $lastRow = $r
                        break
                    }
                }
                # Preserve the table's outer left border on column J (in RTL, xlEdgeRight=10
                # is the visual left edge shared with K) before hiding trailing columns K:M.
                if ($lastRow -gt 2) {
                    $outerEdge = $sheet.Range($sheet.Cells.Item(2, 10), $sheet.Cells.Item($lastRow - 1, 10)).Borders.Item(10)
                    $outerEdge.LineStyle = 1 # xlContinuous
                    $outerEdge.Weight = -4138 # xlMedium
                }
                $sheet.Columns.Item('K:M').Hidden = $true
                # Render the rotated row-index header (A7) horizontally so it never clips.
                $a7 = $sheet.Cells.Item(7, 1)
                if ([int]$a7.Orientation -ne 0) {
                    $a7.Orientation = 0
                    $a7.WrapText = $false
                    if ($sheet.Columns.Item('A').ColumnWidth -lt 6.2) {
                        $sheet.Columns.Item('A').ColumnWidth = 6.2
                    }
                }
                # Wrap work-order number cell J2 cleanly onto two lines ('شماره حکم:' / number)
                # so it never clips at the table's outer edge.
                $j2 = $sheet.Cells.Item(2, 10)
                $j2.WrapText = $true
                if ($sheet.Rows.Item(2).RowHeight -lt 34) {
                    $sheet.Rows.Item(2).RowHeight = 34
                }
                # Give two-line revision/reissue titles breathing room above the table border.
                if (([string]$sheet.Cells.Item(1, 1).Value2) -match "`n") {
                    if ($sheet.Rows.Item(1).RowHeight -lt 48) {
                        $sheet.Rows.Item(1).RowHeight = 48
                    }
                }
                # Trim only excess blank padding in I and J while preserving checkbox spacing.
                if ($sheet.Columns.Item('I').ColumnWidth -gt 16) {
                    $sheet.Columns.Item('I').ColumnWidth = 16
                }
                if ($sheet.Columns.Item('J').ColumnWidth -gt 29.5) {
                    $sheet.Columns.Item('J').ColumnWidth = 29.5
                }
                $setup.PrintArea = '$A$1:$J$' + $lastRow
                $setup.PaperSize = 9 # xlPaperA4
                $setup.Orientation = 1 # xlPortrait
                $setup.Zoom = $false
                $setup.FitToPagesWide = 1
                $setup.FitToPagesTall = 1
                $setup.LeftMargin = 28.35 # 10 mm
                $setup.RightMargin = 28.35 # 10 mm
                $setup.TopMargin = 28.35 # 10 mm
                $setup.BottomMargin = 28.35 # 10 mm
                $setup.HeaderMargin = 0
                $setup.FooterMargin = 0
                $setup.CenterHorizontally = $true
                $setup.CenterVertically = $false
            }
            finally {
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($setup)
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($sheet)
            }
        }
    }
    if ([IO.Path]::GetFileName($SourcePath) -match '^(GR|AF|CW)-') {
        foreach ($sheet in $book.Worksheets) {
            $setup = $sheet.PageSetup
            try {
                $setup.PaperSize = 9
                $setup.Orientation = 1
                $setup.Zoom = $false
                $setup.FitToPagesWide = 1
                $setup.FitToPagesTall = $false
                if ([IO.Path]::GetFileName($SourcePath) -match '^(GR|AF)-') {
                    $setup.CenterHorizontally = $true
                    $setup.CenterVertically = $false
                }
            }
            finally {
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($setup)
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($sheet)
            }
        }
    }
    # Use the workbook's own print areas, page layout and fonts.
    $book.ExportAsFixedFormat(0, $OutputPath, 0, $true, $false)
}
finally {
    if ($null -ne $book) {
        $book.Close($false)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($book)
    }
    if ($null -ne $books) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($books)
    }
    if ($null -ne $excel) {
        if ($null -ne $originalMapPaperSize) {
            $excel.MapPaperSize = $originalMapPaperSize
        }
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel)
    }
}
