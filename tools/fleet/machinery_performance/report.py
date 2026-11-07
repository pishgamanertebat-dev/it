"""Discover the monthly template and extract exact-day and saved monthly totals.

Dates in row 3 are template labels, never evidence that a day is complete.
The operational year is explicit because numeric month names carry no year.
"""
from contextlib import contextmanager
import ast
import hashlib
import io
import math
import re

import openpyxl
from openpyxl.utils.cell import range_boundaries

from integrations.hermes.function_domain.scoped import ScopedReader
from tools.fleet.maintenance_daily.report import exact_date, mechanism_code, zip_safe
from tools.fleet.oil_balance.report import FormulaValues, display_number, key
from tools.scheduler.report_errors import ReportFailure, classify

SOURCE_NAME = 'عملکرد ماشین آلات.xlsx'
TITLE = 'عملکرد مصرف ماشین آلات'
MAX_BYTES = 16 * 1024 * 1024
DAILY_COLUMNS = ('مورد انتظار', 'جمع کارکرد', 'کارکرد روز', 'کارکرد شب',
                 'تعداد سرویس روز', 'تعداد سرویس شب', 'آماده بکار', 'تحت تعمیر', 'سوخت', 'درصد سوخت')
TOTAL_COLUMNS = ('مجموع مورد انتظار', 'مجموع کارکرد', 'مجموع کارکرد روز', 'مجموع کارکرد شب',
                 'مجموع تعداد سرویس روز', 'مجموع تعداد سرویس شب', 'مجموع آماده بکار',
                 'مجموع تحت تعمیر', 'مجموع سوخت', 'میانگین مصرف سوخت', 'حد مجاز')


class PerformanceFormulaValues(FormulaValues):
    """The same bounded verifier with worksheet dimensions cached once.

    openpyxl recomputes max_row/max_column by walking all cells. The wider
    monthly template must not repeat that walk for every formula reference.
    """
    def __init__(self, sheet, cached):
        super().__init__(sheet, cached)
        self.bounds = sheet.max_column, sheet.max_row

    def value(self, coordinate):
        try:
            return super().value(coordinate)
        except ReportFailure as exc:
            if exc.code == 'source_formula_unverified':
                raise ReportFailure('source_performance_formula_unverified', **exc.details) from exc
            raise

    def evaluate(self, node):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'REF'
                and not node.keywords and len(node.args) == 1 and isinstance(node.args[0], ast.Constant)):
            reference = node.args[0].value
            a,b,c,d = range_boundaries(reference)
            if c > self.bounds[0] or d > self.bounds[1] or (c-a+1)*(d-b+1) > 20000:
                raise ValueError('Formula range exceeds bounds')
            values = [self.value(self.sheet.cell(r,col).coordinate) for r in range(b,d+1) for col in range(a,c+1)]
            return values if ':' in reference else values[0]
        return super().evaluate(node)


@contextmanager
def source_snapshot(reader=None):
    reader = reader or ScopedReader(allowed_files=[SOURCE_NAME])
    try:
        data = reader.stable_snapshot(SOURCE_NAME, maximum=MAX_BYTES)
        digest = hashlib.sha256(data).hexdigest()
        yield data, digest
        if hashlib.sha256(reader.stable_snapshot(SOURCE_NAME, maximum=MAX_BYTES)).hexdigest() != digest:
            raise ReportFailure('source_changed', stage='source')
    except OSError as exc:
        raise classify(exc, 'source') from exc


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Nonnegative finite performance value required')
    return value


def columns(sheet, *, month):
    totals = []
    for label in TOTAL_COLUMNS:
        found = [c.column for c in sheet[1] if key(c.value) == key(label)]
        if len(found) != 1:
            raise ValueError('Missing or repeated summary column')
        totals.append(found[0])
    if totals != list(range(totals[0], totals[0] + len(totals))):
        raise ValueError('Unexpected summary layout')
    days = {}
    for cell in sheet[3]:
        if cell.column < 4 or cell.column >= totals[0] or cell.value is None:
            continue
        raw = str(cell.value).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789'))
        if not re.fullmatch(r'[0-9]{1,2}', raw) or not 1 <= int(raw) <= 31:
            raise ValueError('Ambiguous day header')
        if int(raw) == 31 and month > 6:
            continue  # Inspected legacy template has a partial, inapplicable day 31.
        days.setdefault(int(raw), []).append(cell.column)
    for cols in days.values():
        if len(cols) != 10 or cols != list(range(cols[0], cols[0] + 10)):
            raise ValueError('Each day needs ten adjacent columns')
        if [key(sheet.cell(2, c).value) for c in cols] != [key(v) for v in DAILY_COLUMNS]:
            raise ValueError('Unexpected daily fields')
    if key(sheet['B1'].value) != key('نوع دستگاه') or key(sheet['C1'].value) != key('کد دستگاه'):
        raise ValueError('Machine identity columns changed')
    return totals, days


def scan(data, target, *, year=1405):
    target = exact_date(target)
    y, month, day = map(int, target.split('/'))
    if type(year) is not int or y != year:
        raise ValueError('Requested year does not match the configured source year')
    book = cached = None
    try:
        zip_safe(data)
        book = openpyxl.load_workbook(io.BytesIO(data), data_only=False, keep_links=False)
        if not 1 <= len(book.sheetnames) <= 24 or sum(w.max_row*w.max_column for w in book) > 1000000:
            raise ValueError('Workbook exceeds bounds')
        selected = [w for w in book if key(w.title) == str(month)]
        result = dict(date=target, year=year, sheet=None, source_sha256=hashlib.sha256(data).hexdigest(),
                      rows=[], machines=[], missing_codes=[], available_dates=[], columns=list(TOTAL_COLUMNS))
        if not selected:
            return result
        if len(selected) != 1:
            raise ValueError('Ambiguous month worksheet')
        sheet = selected[0]
        totals, days = columns(sheet, month=month)
        result['sheet'] = sheet.title
        cached = openpyxl.load_workbook(io.BytesIO(data), data_only=True, keep_links=False)
        saved = cached[sheet.title]
        evaluator = PerformanceFormulaValues(sheet, saved)
        devices = []
        seen = set()
        for r in range(4, sheet.max_row + 1):
            name, raw_code = sheet.cell(r, 2).value, sheet.cell(r, 3).value
            if name is None and raw_code is None:
                continue
            code = mechanism_code(raw_code)
            if (not code or not isinstance(name, str) or not name.strip() or len(name) > 200
                    or (code, key(name)) in seen):
                raise ValueError('Missing, duplicate or invalid machine identity')
            seen.add((code, key(name)))
            devices.append((r, name.strip(), code))
        if not devices or len(devices) > 500:
            raise ValueError('Invalid machine row count')
        result['machines'] = [dict(code=code,name=name,source_row=r) for r,name,code in devices]

        def recorded(r, cols):
            # Explicit zero is a recorded state. Formula/template cells do not count.
            return any(sheet.cell(r, cols[i]).data_type != 'f' and
                       sheet.cell(r, cols[i]).value is not None for i in (2, 3, 6, 7))

        result['available_dates'] = [f'{year:04d}/{month:02d}/{d:02d}' for d, cols in days.items()
                                     if d <= (31 if month <= 6 else 30) and all(recorded(r, cols) for r, _, _ in devices)]
        if day not in days:
            result['missing_codes'] = [c for _, _, c in devices]
            return result
        daily_cols = days[day]
        result['missing_codes'] = [code for r, _, code in devices if not recorded(r, daily_cols)]
        for r, name, code in devices:
            ready = recorded(r, daily_cols)
            if not ready:
                continue
            daily = []
            for i, c in enumerate(daily_cols[:9]):
                cell = sheet.cell(r, c)
                # Missing raw values in a recorded row are Excel's blank/zero, retained as such.
                value = evaluator.value(cell.coordinate) if cell.data_type == 'f' else (0 if cell.value is None else cell.value)
                daily.append(finite(value))
            average = daily[8] / daily[1] if daily[1] else None
            monthly = []
            for i, c in enumerate(totals):
                cell = sheet.cell(r, c)
                if i == 9 and monthly[1] == 0 and saved.cell(r, c).value == '#DIV/0!':
                    # Excel's undefined 0/0 average is neither zero nor an exceedance.
                    value = None
                else:
                    value = finite(evaluator.value(cell.coordinate))
                monthly.append(value)
            result['rows'].append(dict(source_row=r, name=name, code=code, recorded=ready,
                                       daily=[*daily, average, monthly[10]], monthly=monthly,
                                       monthly_formats=[sheet.cell(r, c).number_format for c in totals],
                                       daily_cells=[sheet.cell(r, c).coordinate for c in daily_cols],
                                       summary_cells=[sheet.cell(r, c).coordinate for c in totals]))
        return result
    except ReportFailure:
        raise
    except Exception as exc:
        raise classify(exc, 'source') from exc
    finally:
        if book is not None:
            book.close()
        if cached is not None:
            cached.close()


def load(target, *, reader=None, year=1405):
    with source_snapshot(reader) as (data, _):
        return scan(data, target, year=year)


def select(report, *, mode, comparison, machine_code=None):
    if mode not in {'daily', 'monthly'} or comparison not in {'average_gt_limit', 'limit_gt_average'}:
        raise ValueError('Explicit summary mode and alert comparison required')
    selected = dict(report, mode=mode, comparison=comparison, rows=[], alert_codes=[], alert_devices=[])
    requested = mechanism_code(machine_code) if machine_code is not None else None
    if machine_code is not None and requested is None:
        raise ValueError('Exact machine code required; aliases are never inferred')
    if requested is not None:
        selected['missing_codes'] = [code for code in report['missing_codes'] if code == requested]
    for row in report['rows']:
        if requested is not None and row['code'] != requested:
            continue
        values = row[mode]
        avg, limit = values[9:11]
        alarm = avg is not None and (avg > limit if comparison == 'average_gt_limit' else limit > avg)
        formats = row['monthly_formats'] if mode == 'monthly' else ['General'] * 9 + ['0.00', 'General']
        display = ['—' if v is None else display_number(v, f) for v, f in zip(values, formats)]
        selected['rows'].append(dict(row, values=values, display=display, alarm=alarm))
        if alarm:
            selected['alert_codes'].append(row['code'])
            selected['alert_devices'].append({'code': row['code'], 'name': row['name']})
    if requested and report['machines'] and requested not in {r['code'] for r in report['machines']}:
        raise ValueError('Machine code is absent from the selected worksheet')
    selected['ambiguous_code'] = bool(requested and sum(r['code']==requested for r in report['machines']) > 1)
    return selected


def build(target, directory, *, mode, comparison, reader=None, year=1405, machine_code=None, fuel_only=False):
    from pathlib import Path
    from .render import render, validate_pdf
    report = select(load(target, reader=reader, year=year), mode=mode, comparison=comparison, machine_code=machine_code)
    if not report['rows'] or report['missing_codes']:
        return {'status': 'waiting_for_data', 'report': report}
    path = Path(directory) / ('machinery-performance-' + report['date'].replace('/', '-') + '.pdf')
    render(report, path, fuel_only=fuel_only)
    validation = validate_pdf(path, report)
    return {'status': 'ready', 'report': report, 'pdf': str(path), 'validation': validation}
