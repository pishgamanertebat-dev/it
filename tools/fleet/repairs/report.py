from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import fitz
import openpyxl

from tools.fleet.overflow.report import normalize, validate_date
from tools.fleet.report_caption import report_caption

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = Path(r'E:\Function\گزارش روزانه رانندگان2.xlsx')


def latest_sheet(source):
    workbook = openpyxl.load_workbook(source, read_only=True, data_only=True)
    try:
        candidates = []
        for sheet in workbook.worksheets:
            dates = set()
            for row in sheet.iter_rows(max_row=1, max_col=30, values_only=True):
                for cell in row:
                    for raw in re.findall(r'(?<!\d)1[34]\d{2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{1,2}(?!\d)', normalize(cell)):
                        dates.add(validate_date(re.sub(r'\s+', '', raw)))
            if len(dates) > 1:
                raise ValueError(f'Ambiguous header dates in sheet: {sheet.title}')
            if dates:
                candidates.append((dates.pop(), sheet.title))
        if not candidates:
            raise ValueError('No dated daily sheet found')
        newest = max(date for date, _ in candidates)
        matches = [name for date, name in candidates if date == newest]
        if len(matches) != 1:
            raise ValueError('Multiple sheets have the latest report date')
        return {'date': newest, 'sheet': matches[0]}
    finally:
        workbook.close()


def export_pdf(source, sheet, output, work_dir):
    request = Path(work_dir) / 'export.json'
    request.write_text(json.dumps(dict(source=str(source), sheet=sheet, output=str(output)), ensure_ascii=False), encoding='utf-8')
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = [str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
               '-File', str(Path(__file__).with_name('export_pdf.ps1')), '-RequestPath', str(request)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        result = subprocess.run(command, capture_output=True, timeout=240, creationflags=flags)
    finally:
        # Only terminate the exact Excel process created by this export, with
        # PID + creation-time verification; never terminate other Excel sessions.
        subprocess.run(command + ['-Cleanup'], capture_output=True, timeout=20, creationflags=flags)
    if result.returncode or not output.exists():
        detail = result.stderr.decode('utf-8', errors='replace')[-5000:]
        raise RuntimeError('Native Excel PDF export failed: ' + detail)
    with fitz.open(output) as pdf:
        if pdf.page_count < 1 or not any(page.get_text().strip() for page in pdf):
            raise RuntimeError('Excel produced an empty PDF')


def build_report(output_dir, source=None):
    source = Path(source or os.environ.get('FLEET_REPAIRS_SOURCE', DEFAULT_SOURCE)).resolve(strict=True)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    # Byte-for-byte snapshot: preserve all Excel formatting, drawings and print
    # settings. Selection and export see exactly the same saved workbook.
    with tempfile.TemporaryDirectory(prefix='excel-', dir=output_dir) as directory:
        snapshot = Path(directory) / 'source.xlsx'
        before = source.stat()
        shutil.copyfile(source, snapshot)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError('Workbook changed while copying; retry on a stable source')
        report = latest_sheet(snapshot)
        output = output_dir / f"repairs-{report['date'].replace('/', '-')}.pdf"
        export_pdf(snapshot, report['sheet'], output, directory)
    return {**report, 'pdf': str(output),
            'caption': report_caption('گزارش روزانه تعمیرات', report['date'], latest=True)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build_report(args.output_dir, args.source), ensure_ascii=False))


if __name__ == '__main__':
    main()
