param(
    [Parameter(Mandatory=$true)][string]$SourcePath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$excel = $null
$books = $null
$book = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $excel.AutomationSecurity = 3
    $books = $excel.Workbooks
    $book = $books.Open($SourcePath, 0, $true)
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
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel)
    }
}
