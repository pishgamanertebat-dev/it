"""Read-only, label-discovered oil balance extraction for one exact Jalali day."""
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
import ast
import hashlib
import io
import math
import re
import unicodedata
from zipfile import BadZipFile

import openpyxl
from openpyxl.formula.tokenizer import Tokenizer
from openpyxl.utils.cell import range_boundaries

from integrations.hermes.function_domain.scoped import ScopedReader
from tools.fleet.maintenance_daily.report import exact_date, zip_safe
from tools.scheduler.report_errors import ReportFailure, classify

SOURCE_NAME = 'موازنه روغن مصرفی.xlsx'
TITLE = 'موازنه روغن مصرفی و وارده (تعویض ها)'
COLUMNS = ('ردیف', 'تاریخ', 'نام دستگاه', 'کد دستگاه', 'روغن موتور', 'روغن 30',
           'روغن هیدرولیک 10W', 'ضدیخ', 'واسگازین 85W140')
STOCK_COLUMNS = COLUMNS[4:8]
STOCK_LABEL = 'موجودی فعلی (لیتر)'
MAX_BYTES = 16 * 1024 * 1024


def key(value):
    text = unicodedata.normalize('NFKC', str(value or '')).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩يك', '01234567890123456789یک'))
    return re.sub(r'[\s\u200c\u200e\u200f\ufeffـ]+', '', text).casefold()


@contextmanager
def source_snapshot(reader=None):
    reader = reader or ScopedReader(allowed_files=[SOURCE_NAME])
    with reader.open(SOURCE_NAME) as (_, stream):
        data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ReportFailure('source_invalid', stage='source')
        digest = hashlib.sha256(data).hexdigest()
        yield data, digest
        stream.seek(0)
        if hashlib.sha256(stream.read(MAX_BYTES + 1)).hexdigest() != digest:
            raise ReportFailure('source_changed', stage='source')


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Finite numeric oil quantity required')
    return value


class FormulaValues:
    """Verify saved Excel results for the bounded arithmetic used by this workbook.

    No eval, external links, Excel execution, or writes to the source. An unknown
    formula or missing/stale saved result stops delivery instead of inventing data.
    """
    def __init__(self, sheet, cached):
        self.sheet, self.cached = sheet, cached
        self.memo, self.active = {}, set()

    def value(self, coordinate):
        if coordinate in self.memo:
            return self.memo[coordinate]
        cell = self.sheet[coordinate]
        if cell.data_type != 'f':
            if cell.data_type == 'e':
                raise ValueError('Excel error')
            return 0 if cell.value is None else number(cell.value)
        if coordinate in self.active or len(self.active) >= 64 or len(self.memo) >= 20000:
            raise ValueError('Formula dependency bounds exceeded')
        self.active.add(coordinate)
        try:
            expression = []
            for token in Tokenizer(cell.value).items:
                if token.type == 'OPERAND' and token.subtype == 'RANGE':
                    if not re.fullmatch(r'\$?[A-Z]{1,3}\$?[1-9][0-9]*(?::\$?[A-Z]{1,3}\$?[1-9][0-9]*)?', token.value):
                        raise ValueError('Only local cell references are allowed')
                    expression.append('REF(' + repr(token.value) + ')')
                elif token.type == 'FUNC' and token.subtype == 'OPEN' and token.value.upper() == 'SUM(':
                    expression.append('SUM(')
                elif (token.type in {'PAREN', 'WSPACE'} or token.type == 'FUNC' and token.subtype == 'CLOSE'
                      or token.type == 'OPERAND' and token.subtype == 'NUMBER'
                      or token.type in {'OPERATOR-INFIX', 'OPERATOR-PREFIX'} and token.value in {'+', '-', '*', '/'}
                      or token.type == 'SEP' and token.subtype == 'ARG'):
                    expression.append(token.value)
                else:
                    raise ValueError('Uninspected formula')
            computed = number(self.evaluate(ast.parse(''.join(expression).strip(), mode='eval').body))
            saved = number(self.cached[coordinate].value)
            if not math.isclose(computed, saved, rel_tol=1e-10, abs_tol=1e-8):
                raise ValueError('Saved Excel result is stale')
            self.memo[coordinate] = saved
            return saved
        except (ValueError, TypeError, SyntaxError, ZeroDivisionError, OverflowError) as exc:
            raise ReportFailure('source_formula_unverified', stage='source', sheet=self.sheet.title,
                                row=cell.row, column=cell.column) from exc
        finally:
            self.active.remove(coordinate)

    def evaluate(self, node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return number(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = self.evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left, right = self.evaluate(node.left), self.evaluate(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(node.op, ast.Div): return left / right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            if node.func.id == 'REF' and len(node.args) == 1 and isinstance(node.args[0], ast.Constant):
                reference = node.args[0].value
                a, b, c, d = range_boundaries(reference)
                if c > self.sheet.max_column or d > self.sheet.max_row or (c-a+1)*(d-b+1) > 20000:
                    raise ValueError('Formula range exceeds bounds')
                values = [self.value(self.sheet.cell(r, col).coordinate) for r in range(b, d+1) for col in range(a, c+1)]
                return values if ':' in reference else values[0]
            if node.func.id == 'SUM':
                values = [self.evaluate(arg) for arg in node.args]
                return sum(v for item in values for v in (item if isinstance(item, list) else [item]))
        raise ValueError('Unsupported formula expression')


def display_number(value, number_format):
    # The inspected workbook uses #,##0; retain its visible rounding (342.85 -> 343).
    fmt = number_format.split(';')[0]
    if fmt == 'General':
        return format(value, '.12g')
    if not re.fullmatch(r'[#0,]+(?:\.[#0]+)?', fmt):
        raise ValueError('Uninspected numeric display format')
    decimals = len(fmt.split('.')[1]) if '.' in fmt else 0
    rounded = Decimal(str(value)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return format(rounded, (',' if ',' in fmt else '') + f'.{decimals}f')


def find_header(sheet, labels, start, end):
    wanted = {key(label) for label in labels}
    matches = []
    for row in sheet.iter_rows(min_row=start, max_row=end):
        columns = {}
        for cell in row:
            label = key(cell.value)
            if label in wanted:
                if label in columns:
                    raise ReportFailure('source_section_missing', stage='source', sheet=sheet.title, row=cell.row)
                columns[label] = cell.column
        if set(columns) == wanted:
            matches.append((row[0].row, [columns[key(label)] for label in labels]))
    if len(matches) != 1:
        raise ReportFailure('source_section_missing', stage='source', sheet=sheet.title)
    return matches[0]


def scan(data, target):
    target = exact_date(target)
    book = None
    cached = None
    try:
        zip_safe(data)
        book = openpyxl.load_workbook(io.BytesIO(data), data_only=False, keep_links=False)
        if not 1 <= len(book.sheetnames) <= 32:
            raise ValueError('Sheet count exceeds bounds')
        if sum(s.max_row*s.max_column for s in book) > 1000000:
            raise ValueError('Workbook cell count exceeds bounds')
        titles = []
        for sheet in book:
            if sheet.max_row > 10000 or sheet.max_column > 64:
                raise ValueError('Worksheet exceeds bounds')
            titles.extend((sheet.title, cell.row) for row in sheet for cell in row if key(cell.value) == key(TITLE))
        if len(titles) != 1:
            raise ReportFailure('source_section_missing', stage='source')
        name, title_row = titles[0]
        sheet = book[name]
        header, columns = find_header(sheet, COLUMNS, title_row+1, min(title_row+100, sheet.max_row))
        ends = [cell.row for row in sheet.iter_rows(min_row=header+1) for cell in row
                if key(cell.value) in {key('جمع کل تعویض'), key('جمع سرریز روغن')}]
        if not ends:
            raise ReportFailure('source_section_missing', stage='source', sheet=name)
        end = min(ends)
        stock = [cell for row in sheet for cell in row if key(cell.value) == key(STOCK_LABEL)]
        if len(stock) != 1:
            raise ReportFailure('source_section_missing', stage='source', sheet=name)
        stock_row = stock[0].row
        stock_header, stock_columns = find_header(sheet, ('نوع', *STOCK_COLUMNS), end, stock_row-1)
        if stock_header >= stock_row:
            raise ReportFailure('source_section_missing', stage='source', sheet=name)
        cached = openpyxl.load_workbook(io.BytesIO(data), data_only=True, keep_links=False)
        values = FormulaValues(sheet, cached[name])
        inventory = []
        for label, col in zip(STOCK_COLUMNS, stock_columns[1:]):
            cell = sheet.cell(stock_row, col)
            if cell.value is None:
                raise ValueError('Missing inventory value')
            amount = values.value(cell.coordinate)
            inventory.append({'label': label, 'value': amount, 'display': display_number(amount, cell.number_format), 'cell': cell.coordinate})
        rows, dates = [], set()
        for r in range(header+1, end):
            cells = [sheet.cell(r, c) for c in columns]
            if all(c.value is None for c in cells[1:]):
                continue  # Pre-numbered empty template rows do not establish readiness.
            if [key(c.value) for c in cells] == [key(v) for v in COLUMNS]:
                continue
            try:
                if cells[1].data_type in {'f', 'e'}:
                    raise ValueError('Literal date required')
                day = exact_date(cells[1].value)
            except ValueError as exc:
                raise ReportFailure('source_invalid', stage='source', sheet=name, row=r, column=columns[1]) from exc
            activity = any(c.value is not None for c in cells[4:])
            if not activity:
                if any(c.value is not None for c in cells[2:4]):
                    raise ReportFailure('source_invalid', stage='source', sheet=name, row=r)
                continue  # A date alone is not a recorded oil change.
            if any(c.value is None or c.data_type in {'f', 'e'} for c in cells[:4]):
                raise ReportFailure('source_invalid', stage='source', sheet=name, row=r)
            dates.add(day)
            if day != target:
                continue
            shown = [str(c.value) for c in cells[:4]]
            shown[1] = day
            for cell in cells[4:]:
                shown.append('' if cell.value is None else display_number(values.value(cell.coordinate), cell.number_format))
            if any(len(v) > 200 for v in shown):
                raise ValueError('Oversized cell')
            rows.append({'source_row': r, 'values': shown})
        return {'date': target, 'title': TITLE, 'columns': list(COLUMNS), 'sheet': name,
                'inventory': inventory, 'rows': rows, 'rows_matched': len(rows),
                'available_dates': sorted(dates), 'source_sha256': hashlib.sha256(data).hexdigest(),
                'source_locations': {'title_row': title_row, 'header_row': header, 'inventory_row': stock_row}}
    except ReportFailure:
        raise
    except BadZipFile as exc:
        raise ReportFailure('source_invalid', stage='source') from exc
    except Exception as exc:
        raise classify(exc, 'source') from exc
    finally:
        if book is not None:
            book.close()
        if cached is not None:
            cached.close()


def load(target, reader=None):
    try:
        with source_snapshot(reader) as (data, _):
            return scan(data, target)
    except ReportFailure:
        raise
    except Exception as exc:
        raise classify(exc, 'source') from exc


def build(target, directory):
    from pathlib import Path
    from .render import render, validate_pdf
    report = load(target)
    if not report['rows']:
        return {'status': 'waiting_for_data', 'reason': 'date_missing', 'report': report}
    path = Path(directory) / ('oil-balance-' + target.replace('/', '-') + '.pdf')
    render(report, path)
    validation = validate_pdf(path, report)
    return {'status': 'ready', 'report': report, 'pdf': str(path), 'validation': validation}
