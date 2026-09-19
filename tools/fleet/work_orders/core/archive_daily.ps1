param(
    [Parameter(Mandatory=$true)][string]$SourcePath,
    [Parameter(Mandatory=$true)][string]$TargetPath,
    [Parameter(Mandatory=$true)][string]$OrderNumber
)
$ErrorActionPreference = 'Stop'
$mutex = New-Object System.Threading.Mutex($false, 'Local\KomatsoDailyWorkOrderArchive')
$locked = $false
$excel = $null
$target = $null
$source = $null
try {
    try { $locked = $mutex.WaitOne(120000) }
    catch [System.Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Daily archive is busy; retry.' }
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $excel.AutomationSecurity = 3
    $excel.EnableEvents = $false
    $target = $excel.Workbooks.Open($TargetPath, 0, $false)
    if ($target.ReadOnly) { throw 'Daily workbook is locked or read-only; close it and retry.' }
    foreach ($sheet in $target.Worksheets) {
        foreach ($property in $sheet.CustomProperties) {
            if ($property.Name -eq 'KomatsoWorkOrder' -and $property.Value -eq $OrderNumber) {
                return
            }
        }
    }
    $last = $target.Sheets.Item($target.Sheets.Count)
    $highest = -1
    $prefix = $null
    foreach ($sheet in $target.Worksheets) {
        if ($sheet.Name -match '^(Sheet1\s*\()(\d+)(\))$') {
            if ([int]$Matches[2] -gt $highest) {
                $highest = [int]$Matches[2]
                $prefix = $Matches[1]
            }
        }
    }
    if ($highest -lt 0) { throw 'No numbered Sheet1 worksheet found.' }
    $nextName = $prefix + ($highest + 1) + ')'
    foreach ($sheet in $target.Sheets) {
        if ($sheet.Name -eq $nextName) { throw 'Next worksheet name already exists.' }
    }
    $source = $excel.Workbooks.Open($SourcePath, 0, $true)
    if ($source.Worksheets.Count -ne 1) { throw 'Expected a single approved worksheet.' }
    # Keep a recoverable original before changing the daily workbook.
    $backupDir = Join-Path ([IO.Path]::GetDirectoryName($TargetPath)) 'work_order_backups'
    [void][IO.Directory]::CreateDirectory($backupDir)
    $backupName = [IO.Path]::GetFileNameWithoutExtension($TargetPath) + '-' + [guid]::NewGuid().ToString('N') + '.xlsx'
    $target.SaveCopyAs((Join-Path $backupDir $backupName))
    # Native copy retains fonts, merged cells, dimensions, drawings and print setup.
    $source.Worksheets.Item(1).Copy([Type]::Missing, $last)
    $added = $target.Sheets.Item($target.Sheets.Count)
    $added.Name = $nextName
    [void]$added.CustomProperties.Add('KomatsoWorkOrder', $OrderNumber)
    $target.Save()
}
finally {
    if ($null -ne $source) { $source.Close($false) }
    if ($null -ne $target) { $target.Close($false) }
    if ($null -ne $excel) {
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel)
    }
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
