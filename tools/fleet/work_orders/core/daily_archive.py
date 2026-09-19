"""Archive delivered orders using Excel's native worksheet copy."""
from pathlib import Path
import subprocess

ARCHIVES = {
    'AIR_FILTER': Path('E:/Function/لیست روزانه هواکش .xlsx'),
    'GREASING': Path('E:/Function/لیست روزانه گریسکاری.xlsx'),
}


class DailyArchiveError(RuntimeError):
    """Delivery succeeded, but its daily workbook still needs updating."""


def archive_delivered_order(order):
    target = ARCHIVES.get(order['work_order_type'])
    if target is None:
        return
    result = subprocess.run([
        'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', str(Path(__file__).with_name('archive_daily.ps1')),
        '-SourcePath', str(order['excel_path']), '-TargetPath', str(target),
        '-OrderNumber', order['work_order_no'],
    ], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise DailyArchiveError('DAILY ARCHIVE FAILED: ' + (result.stderr or result.stdout).strip())
