"""Persian searchable PDF, with repeated identities/headers and visible fuel alerts."""
import hashlib
import html
from pathlib import Path

from tools.fleet.maintenance_daily.report import canonical_unicode_maps
from tools.fleet.oil_balance.render import pdf_key
from tools.scheduler.report_errors import ReportFailure
from .report import TITLE

FONTS = Path('C:/Windows/Fonts')
PAGE_WIDTH, PAGE_HEIGHT = 1191, 842
WIDTHS = (15, 7, *([78/11] * 11))
CSS = '''@font-face{font-family:Perf;src:url(tahoma.ttf)}
@font-face{font-family:Perf;src:url(tahomabd.ttf);font-weight:bold}
body{font-family:Perf;color:#183348;margin:0}p{margin:0;line-height:1.4;text-align:center}'''


def caption(report):
    codes = '، '.join(report['alert_codes'])
    if report['comparison'] == 'average_gt_limit':
        warning = 'دستگاه‌های ' + codes + ' از حد مجاز مصرف سوخت عبور کرده‌اند.' if codes else 'موردی از عبور میانگین مصرف سوخت از حد مجاز دیده نشد.'
    else:
        warning = 'هشدار شرط انتخاب‌شده برای دستگاه‌های ' + codes if codes else 'موردی مشمول شرط هشدار انتخاب‌شده نشد.'
    return TITLE + '\nتاریخ: ' + report['date'] + '\n' + warning


def render(report, output, *, test_sample=False):
    import fitz
    if not report['rows'] or len(report['rows']) > 500:
        raise ReportFailure('pdf_validation', stage='validation')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    archive = fitz.Archive(str(FONTS))
    doc = fitz.open()
    page = None
    cells = []
    left, right = 24, PAGE_WIDTH - 24

    def insert(text, rect, size=13, color='#183348', bold=False):
        style = f'font-size:{size}px;color:{color};' + ('font-weight:bold' if bold else '')
        body = '<p dir="auto" style="' + style + '">' + html.escape(text).replace('\n', '<br>') + '</p>'
        spare, scale = page.insert_htmlbox(rect, body, css=CSS, archive=archive, scale_low=1)
        if spare < 0 or scale != 1:
            raise ReportFailure('pdf_layout', stage='layout')

    def row(values, y, height, *, header=False, source=None, alarm=False):
        x = right
        rects = []
        for value, width in zip(values, WIDTHS):
            x0 = x - (right-left)*width/100
            rect = fitz.Rect(x0, y, x, y+height)
            fill = (.08,.20,.28) if header else ((1,.88,.88) if alarm else (.96,.97,.98))
            page.draw_rect(rect, color=(.75,.80,.84), fill=fill, width=.5)
            rects.append(list(rect))
            x = x0
        color = 'white' if header else ('#9b1515' if alarm else '#183348')
        size = 12 if header else 13
        style = f'font-size:{size}px;color:{color};' + ('font-weight:bold;' if header or alarm else '')
        contents = ''.join(f'<td style="width:{(right-left)*width/100}px"><p dir="auto">'+html.escape(value)+'</p></td>'
                           for value, width in reversed(list(zip(values, WIDTHS))))
        body = '<table style="'+style+'"><tr>'+contents+'</tr></table>'
        spare, scale = page.insert_htmlbox(fitz.Rect(left, y+4, right, y+height-2), body,
                                          css=CSS+'table{width:100%;table-layout:fixed;border-collapse:collapse}td{padding:0;vertical-align:top}td p{padding:0 3px}',
                                          archive=archive, scale_low=1)
        if spare < 0 or scale != 1:
            raise ReportFailure('pdf_layout', stage='layout')
        cells.append(dict(page=page.number, values=values, rects=rects, source_row=source))
        return y+height

    partial = output.with_suffix('.partial.pdf')
    try:
        for offset in range(0, len(report['rows']), 14):
            page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
            page.draw_rect(fitz.Rect(0,0,PAGE_WIDTH,8), fill=(.08,.20,.28), color=None)
            insert(TITLE, fitz.Rect(left,20,right,57), size=23, bold=True)
            insert('تاریخ گزارش: ' + report['date'], fitz.Rect(left,64,right,93), size=17, bold=True)
            label = 'عملکرد همان روز' if report['mode'] == 'daily' else 'جمع ماه در زمان تهیه گزارش'
            if test_sample:
                label = 'نمونه جهت بررسی — ' + label
            insert(label, fitz.Rect(left,99,right,128), size=14)
            y = row(['نوع دستگاه','کد دستگاه',*report['columns']], 140, 75, header=True)
            for record in report['rows'][offset:offset+14]:
                y = row([record['name'],record['code'],*record['display']], y, 38,
                        source=record['source_row'], alarm=record['alarm'])
            alert = 'ردیف‌های قرمز مشمول هشدار مصرف سوخت هستند.' if report['alert_codes'] else 'عبور از حد مجاز مشاهده نشد.'
            insert(alert, fitz.Rect(left,765,right,790), size=12, color='#9b1515')
        for page in doc:
            insert(f'{page.number+1} / {doc.page_count}', fitz.Rect(left,803,right,829), size=11)
        doc.subset_fonts()
        canonical_unicode_maps(doc)
        doc.save(partial, garbage=4, deflate=True)
        partial.replace(output)
        report['pdf_cells'] = cells
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
            raise ValueError('PDF outside resource bounds')
        with fitz.open(path) as doc:
            if doc.is_repaired or doc.needs_pass or not 1 <= len(doc) <= 40:
                raise ValueError('Malformed report')
            for page in doc:
                if abs(page.rect.width-PAGE_WIDTH) > 1 or abs(page.rect.height-PAGE_HEIGHT) > 1 or page.get_images():
                    raise ValueError('Expected searchable landscape PDF')
                if report['date'] not in page.get_text() or pdf_key(TITLE) not in pdf_key(page.get_text()):
                    raise ValueError('Missing date/title')
                page.get_pixmap(matrix=fitz.Matrix(.4,.4))
            if not report.get('pdf_cells'):
                raise ValueError('Missing cell inventory')
            # Index glyphs once per page. Re-extracting/shaping a whole page for
            # every cell makes this wide monthly table take minutes to verify.
            glyphs = []
            for page in doc:
                chars = []
                for block in page.get_text('rawdict')['blocks']:
                    for line in block.get('lines', []):
                        for span in line['spans']:
                            for char in span['chars']:
                                x0,y0,x1,y1 = char['bbox']
                                chars.append(((x0+x1)/2,(y0+y1)/2,char['c']))
                glyphs.append(chars)
            seen = []
            for fragment in report['pdf_cells']:
                if fragment['source_row'] is not None:
                    seen.append(fragment['source_row'])
                for value, rect in zip(fragment['values'], fragment['rects']):
                    x0,y0,x1,y1 = rect
                    actual = pdf_key(''.join(c for x,y,c in glyphs[fragment['page']] if x0<=x<=x1 and y0<=y<=y1))
                    if value and pdf_key(value) not in actual:
                        raise ValueError('PDF cell differs from source')
            if seen != [r['source_row'] for r in report['rows']]:
                raise ValueError('Lost or repeated machine')
            return dict(pages=len(doc), rows=len(seen), bytes=path.stat().st_size,
                        pdf_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), searchable=True)
    except Exception as exc:
        raise ReportFailure('pdf_validation', stage='validation') from exc
