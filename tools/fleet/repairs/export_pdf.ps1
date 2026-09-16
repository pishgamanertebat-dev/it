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
    # Preserve the saved print area, scaling, paper, fonts and page breaks.
    $sheet.ExportAsFixedFormat(0, $request.output, 0, $true, $false)
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
