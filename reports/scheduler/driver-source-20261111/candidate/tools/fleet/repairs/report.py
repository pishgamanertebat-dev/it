from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

import fitz
import openpyxl

from tools.fleet.overflow.report import normalize, validate_date
from tools.scheduler.report_errors import ReportFailure

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = Path(r'E:\Function\گزارش روزانه رانندگان2.xlsx')
SECTIONS = {'mechanical': 'شرح معایب مکانیکی', 'metalwork': 'شرح معایب آهنگری'}


def section_columns(source, sheet_name, section):
    """Only equipment identity and the requested defect column may be printed."""
    if section not in SECTIONS:
        raise ValueError('Unknown repairs section')
    def key(value):
        return normalize(value).replace('آ', 'ا').replace(' ', '')
    required = ['ردیف', 'نوع دستگاه', 'کد جدید', SECTIONS[section]]
    book = openpyxl.load_workbook(source, read_only=True, data_only=True)
    try:
        sheet = book[sheet_name]
        header = next(sheet.iter_rows(min_row=2, max_row=2, values_only=True), ())
        columns = []
        for label in required:
            matches = [index for index, value in enumerate(header, 1) if key(value) == key(label)]
            if len(matches) != 1:
                raise ValueError(f'Missing or ambiguous repairs column: {label}')
            columns.append(matches[0])
        return columns
    finally:
        book.close()


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


def export_pdf(source, sheet, output, work_dir, columns):
    # The plan and the renderer read the same private snapshot. Never trust the
    # saved PrintArea (older and newly copied daily sheets can both omit E).
    book = openpyxl.load_workbook(source, read_only=True, data_only=True, keep_links=False)
    try:
        ws = book[sheet]
        if ws.max_row > 10000 or ws.max_column > 128:
            raise ValueError('Driver sheet dimensions exceed safe reporting limit')
        last_row = 2
        source_columns = ws.max_column
        parts = []
        for number, row in enumerate(ws.iter_rows(min_row=3, values_only=True), 3):
            values = [row[c-1] if c <= len(row) else None for c in columns]
            if not any(normalize(v) for v in values):
                continue
            for index,field in ((1,'type'),(2,'code')):
                if not normalize(values[index]):
                    raise ReportFailure(f'source_driver_{field}_missing',stage='source',
                        sheet=ws.title,row=number,column=columns[index],
                        sheet_number=book.sheetnames.index(ws.title)+1)
            last_row = number
            # Excel cannot grow a row beyond 409 points. Split large cells in
            # the private copy into bounded continuation rows before AutoFit.
            text = str(values[3] if values[3] is not None else '')
            chunks = []
            while text:
                end = min(len(text), 360)
                newline = [m.end() for m in re.finditer('\n', text[:end])]
                if len(newline) > 6:
                    end = newline[5]
                elif end < len(text):
                    space = text.rfind(' ', 0, end)
                    if space > end // 2:
                        end = space + 1
                chunks.append(text[:end]); text = text[end:]
            if len(chunks) > 1:
                parts.append({'row': number, 'chunks': chunks})
    finally:
        book.close()
    request = Path(work_dir) / 'export.json'
    request.write_text(json.dumps(dict(source=str(source), sheet=sheet, output=str(output),
        columns=columns, last_row=last_row, source_columns=source_columns,
        continuations=parts), ensure_ascii=False), encoding='utf-8')
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
