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
    if ([IO.Path]::GetFileName($SourcePath).StartsWith('OC-')) {
        $originalMapPaperSize = $excel.MapPaperSize
        $excel.MapPaperSize = $false
    }
    $books = $excel.Workbooks
    $book = $books.Open($SourcePath, 0, $true)
    if ([IO.Path]::GetFileName($SourcePath).StartsWith('OC-')) {
        foreach ($sheet in $book.Worksheets) {
            $setup = $sheet.PageSetup
            try {
                $setup.PaperSize = 9 # xlPaperA4
                $setup.Orientation = 1 # xlPortrait
                $setup.Zoom = $false
                $setup.FitToPagesWide = 1
                $setup.FitToPagesTall = 1
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
