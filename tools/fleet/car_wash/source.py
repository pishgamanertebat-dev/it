"""Read explicit blue wash events and recorded shift hours from one snapshot."""
from collections import Counter
from io import BytesIO
import hashlib
from pathlib import Path
from zipfile import BadZipFile

from openpyxl import load_workbook

from tools.fleet.greasing.source import (
    SOURCE, SHEET, clean, code_text, completed_cutoff, date_columns, format_shift,
)
from tools.fleet.oil_change.source import FormulaReader, number


def is_blue(cell):
    fill = cell.fill
    return (fill.patternType == 'solid' and fill.fgColor.type == 'rgb'
            and fill.fgColor.tint == 0
            and str(fill.fgColor.rgb)[-6:].upper() == '00B0F0')


def read_source(path=None, as_of=None):
    try:
        payload = Path(path or SOURCE).read_bytes()
        book = load_workbook(BytesIO(payload), data_only=False)
    except (OSError, BadZipFile) as exc:
        raise ValueError('فایل ساعت‌کار قابل خواندن نیست؛ پس از کامل شدن ذخیره یا همگام‌سازی، دوباره «کارواش» را بنویسید.') from exc
    limit = tuple(as_of or completed_cutoff())
    try:
        ws = book[SHEET]
        columns = [(stamp, col) for stamp, col in date_columns(ws) if stamp <= limit]
        reader = FormulaReader(ws)
        machines, populated = [], []
        for row in range(4, ws.max_row + 1):
            name = clean(ws.cell(row, 1).value)
            if not name:
                continue
            code = code_text(ws.cell(row, 2).value)
            raw_code = code
            old = code_text(ws.cell(row, 3).value)
            # These two legacy rows have distinct verified identities.
            if code == 'D155' and old in {'151', '152'}:
                code = 'D' + old
            entries = []
            for stamp, col in columns:
                cell = ws.cell(row, col)
                try:
                    value = reader.value(cell.coordinate)
                except ValueError:
                    value = cell.value
                blue = is_blue(cell)
                if blue or clean(value) not in {'', '-'}:
                    populated.append(stamp)
                entries.append({'stamp': stamp, 'hours': value,
                                'blue': blue, 'cell': cell.coordinate})
            machines.append({'row': row, 'code': code, 'name': name,
                             'raw_code': raw_code, 'legacy_code': old, 'entries': entries})
        counts = Counter(m['code'] for m in machines if m['code'])
        for machine in machines:
            machine['duplicate'] = counts[machine['code']] > 1 if machine['code'] else False
        cutoff = max(populated) if populated else None
        warnings = []
        if cutoff is None:
            warnings.append('در سال عملیاتی، کارکرد یا کارواش ثبت‌شده‌ای پیدا نشد.')
        elif cutoff < limit:
            warnings.append(f'آخرین دادهٔ ثبت‌شده: {format_shift(cutoff)}؛ محاسبه تا همین داده است.')
        return {'machines': machines, 'sha256': hashlib.sha256(payload).hexdigest(),
                'as_of': limit, 'cutoff': cutoff, 'warnings': warnings}
    finally:
        book.close()
