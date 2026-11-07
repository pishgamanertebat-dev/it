"""Readable Persian vector PDF containing only the current inventory summary."""
import hashlib
import html
from pathlib import Path

from tools.scheduler.report_errors import ReportFailure
from .report import key, STOCK_LABEL

FONTS = Path('C:/Windows/Fonts')
CSS = '''@font-face{font-family:Oil;src:url(tahoma.ttf)}
@font-face{font-family:Oil;src:url(tahomabd.ttf);font-weight:bold}
body{font-family:Oil;font-size:11px;color:#183348;margin:0}
p{margin:0;line-height:1.5;text-align:center}'''


def pdf_key(value):
    # PDF bidi extraction reverses parentheses while the rendered glyphs are correct.
    return key(value).replace('(', '').replace(')', '').replace('\u00ad', '-')


def render(report, output, *, test_sample=False):
    import fitz
    from tools.fleet.maintenance_daily.report import canonical_unicode_maps
    if not report['rows']:
        raise ReportFailure('pdf_validation', stage='validation')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not all((FONTS / name).is_file() for name in ('tahoma.ttf', 'tahomabd.ttf')):
        raise ReportFailure('report_configuration', stage='configuration')
    doc = fitz.open()
    archive = fitz.Archive(str(FONTS))
    fragments = []
    page = None
    left, right = 28, 814

    def insert(text, rect, *, size=11, bold=False, color='#183348', align='center'):
        style = f'font-size:{size}px;color:{color};text-align:{align};' + ('font-weight:bold;' if bold else '')
        body = '<p dir="auto" style="' + style + '">' + html.escape(text).replace('\n', '<br>') + '</p>'
        spare, scale = page.insert_htmlbox(rect, body, css=CSS, archive=archive, scale_low=1)
        if spare < 0 or scale != 1:
            raise ReportFailure('pdf_layout', stage='layout')

    def row(values, y, height, widths, *, header=False, stock=False, source=None):
        x = right
        rects = []
        for value, width in zip(values, widths):
            x0 = x - (right-left)*width/100
            rect = fitz.Rect(x0, y, x, y+height)
            fill = (.08, .20, .28) if header else ((.90, .96, .94) if stock else (.96, .97, .98))
            page.draw_rect(rect, color=(.76, .82, .85), fill=fill, width=.5)
            box = fitz.Rect(x0+4, y+7, x-4, y+height-5)
            insert(value or '—', box, size=17 if stock and value != STOCK_LABEL else (10.5 if header else 12),
                   bold=header or stock, color='white' if header else '#183348')
            rects.append(list(rect))
            x = x0
        fragments.append({'page': page.number, 'values': list(values), 'rects': rects, 'source_row': source})
        return y + height

    def new_page():
        nonlocal page
        page = doc.new_page(width=842, height=595)
        page.draw_rect(fitz.Rect(0, 0, 842, 9), fill=(.08, .20, .28), color=None)
        insert('گزارش روزانه موازنه روغن', fitz.Rect(left, 23, right, 60), size=20, bold=True, align='right')
        insert('تاریخ گزارش: ' + report['date'], fitz.Rect(left, 66, right, 95), size=15, bold=True, align='right')
        if test_sample:
            insert('نمونه جهت بررسی', fitz.Rect(left, 68, left+185, 94), size=12, color='#9c5422')
        y = row(['نوع', *[v['label'] for v in report['inventory']]], 111, 42, (28,18,18,20,16), header=True)
        y = row([STOCK_LABEL, *[v['display'] for v in report['inventory']]], y, 43, (28,18,18,20,16), stock=True)
        insert('موجودی فعلی مطابق فایل هنگام تهیه گزارش است.', fitz.Rect(left, y+7, right, y+29), size=10, align='right')

    partial = output.with_suffix('.partial.pdf')
    try:
        new_page()
        for page in doc:
            insert(f"{page.number+1} / {doc.page_count}", fitz.Rect(left, 563, right, 587), size=9)
        doc.subset_fonts()
        canonical_unicode_maps(doc)
        doc.save(partial, garbage=4, deflate=True)
        partial.replace(output)
        report['pdf_cells'] = fragments
    except ReportFailure:
        raise
    except Exception as exc:
        raise ReportFailure('pdf_layout', stage='layout') from exc
    finally:
        doc.close()
        partial.unlink(missing_ok=True)
    return output


def validate_pdf(path, report):
    import fitz
    path = Path(path)
    try:
        if not 0 < path.stat().st_size < 10*1024*1024:
            raise ValueError('PDF size exceeds bounds')
        with fitz.open(path) as doc:
            if doc.is_repaired or doc.needs_pass or not 1 <= doc.page_count <= 100:
                raise ValueError('Malformed PDF')
            for page in doc:
                if abs(page.rect.width-842) > 1 or abs(page.rect.height-595) > 1 or page.get_images():
                    raise ValueError('Landscape vector PDF required')
                text = page.get_text()
                if report['date'] not in text or pdf_key(STOCK_LABEL) not in pdf_key(text):
                    raise ValueError('Report date or title missing')
                page.get_pixmap(matrix=fitz.Matrix(.5,.5))
            cells = report.get('pdf_cells')
            expected = [
                ['نوع', *[v['label'] for v in report['inventory']]],
                [STOCK_LABEL, *[v['display'] for v in report['inventory']]],
            ]
            if (doc.page_count != 1 or not cells or len(cells) != len(expected)
                    or [c['values'] for c in cells] != expected
                    or any(c['source_row'] is not None or len(c['rects']) != 5 for c in cells)):
                raise ValueError('Only the inventory summary may be rendered')
            for fragment in cells:
                for value, rect in zip(fragment['values'], fragment['rects']):
                    actual = pdf_key(doc[fragment['page']].get_text(clip=fitz.Rect(rect)))
                    if value and pdf_key(value) not in actual:
                        raise ValueError('Extracted PDF cell differs from source')
            return {'pages': doc.page_count, 'bytes': path.stat().st_size, 'rows': 0, 'inventory_values': len(report['inventory']),
                    'searchable': True, 'pdf_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    except Exception as exc:
        raise ReportFailure('pdf_validation', stage='validation') from exc
