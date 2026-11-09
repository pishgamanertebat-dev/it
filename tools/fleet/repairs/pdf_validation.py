"""Fail-closed, cell-level validation of native daily driver PDFs.

The expected inventory comes only from the bytes handed to Excel. Geometry
separates sections, devices, identical descriptions and continuation rows.
Unrecoverable extraction is a validation failure, never a send permission.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import fitz

from tools.scheduler.report_errors import ReportFailure


def canonical(value):
    value = unicodedata.normalize('NFKC', str(value if value is not None else ''))
    value = value.translate(str.maketrans({'\u00ad':'-','\u2010':'-','\u2011':'-'}))
    value = value.translate(str.maketrans('كيىآأإ٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', 'کییااا01234567890123456789'))
    return ''.join(c for c in value if not c.isspace() and
                   unicodedata.category(c) != 'Cf' and c != '\u0640' and
                   not ('\u064b' <= c <= '\u065f')).casefold()


def _line_variants(value):
    # PDF producers can emit logical text, visual RTL runs, or words in visual
    # order. Only exact complete character sequences are accepted.
    parts = re.findall(r'\S+', value)
    rtl = r'[\u0600-\u06ff\ufb50-\ufdff\ufe70-\ufeff]+'
    variants = {value, ' '.join(reversed(parts)), value[::-1]}
    # A continuation cell beginning with a number can use an LTR paragraph
    # base direction. Its leading numeric run then sits after the RTL run in
    # visual extraction; retain that entire run and move it across the boundary.
    trailing = re.fullmatch(r'(.*\S)\s+([\dA-Za-z_]+)',value)
    leading = re.fullmatch(r'([\dA-Za-z_]+)\s+(\S.*)',value)
    if trailing:variants.add(trailing[2]+' '+trailing[1])
    if leading:variants.add(leading[2]+' '+leading[1])
    for candidate in tuple(variants):
        variants.add(re.sub(rtl, lambda m: m[0][::-1], candidate))
    return {canonical(v) for v in variants}


def equivalent(expected, lines):
    expected = canonical(expected)
    positions = {0}
    for line in lines:
        variants = _line_variants(line)
        positions = {p + len(v) for p in positions for v in variants if expected.startswith(v, p)}
        if not positions:
            return False
    return len(expected) in positions


def _lines(page, rect, traces=None):
    # Reconstruct visual glyph clusters, preserving multi-character ligatures.
    # Excel emits lam-alef as one glyph with two Unicode values; reversing
    # Unicode characters would corrupt it into alef-lam. TextTrace retains
    # the actual glyph boundary (continuation glyph ID -1).
    groups = []
    for span in (page.get_texttrace() if traces is None else traces):
        clusters = []
        for char, glyph, origin, bbox in span['chars']:
            if glyph == -1 and clusters:
                clusters[-1][2] += chr(char)
            else:
                clusters.append([origin, fitz.Rect(bbox), chr(char)])
        for origin, bbox, value in clusters:
            if not rect.contains(bbox):
                continue
            value=value.translate(str.maketrans({'\u00ad':'-','\u2010':'-','\u2011':'-'}))
            y = origin[1]
            group = next((g for g in groups if abs(g[0]-y) < 1.3), None)
            if group is None:
                group = [y, []]; groups.append(group)
            group[1].append((origin[0], value, bbox.x1))
    result = []
    for y, items in sorted(groups):
        runs = []
        for x, value, right in sorted(items):
            ascii_run = bool(re.fullmatch(r'[A-Za-z\d]+', value))
            if value in {'_','-','.','/'} and runs and re.search(r'[A-Za-z]',runs[-1][0]):
                ascii_run = True
            if ascii_run and runs and runs[-1][1] and x-runs[-1][2] < 2:
                runs[-1][0] += value; runs[-1][2] = right
            else:
                runs.append([value, ascii_run, right])
        result.append(''.join(r[0] for r in reversed(runs)).strip())
    return result


def _grid(page):
    xs, ys = [], []
    for drawing in page.get_drawings():
        if drawing['color'] is not None and all(c>.97 for c in drawing['color']):
            continue
        for item in drawing['items']:
            if item[0] == 'l':
                a, b = item[1:3]
                if abs(a.x-b.x) < .3 and abs(a.y-b.y) > 5:
                    xs.append(a.x)
                if abs(a.y-b.y) < .3 and abs(a.x-b.x) > 5:
                    ys.append(a.y)
                    xs.extend((min(a.x,b.x),max(a.x,b.x)))
            elif item[0] == 're':
                rect = item[1]
                if drawing['type'] in {'s','fs'} and rect.width > 5 and rect.height > 5:
                    xs.extend((rect.x0, rect.x1)); ys.extend((rect.y0, rect.y1))
    def distinct(values):
        result = []
        for value in sorted(values):
            if not result or value-result[-1] > 1.5:
                result.append(value)
        return result
    xs, ys = distinct(xs), distinct(ys)
    if len(xs) != 5 or len(ys) < 3:
        raise ValueError('table_geometry_uncertain')
    return xs, ys


DEFECTS = {
    'table_geometry_uncertain': 'مرز ستون‌ها یا ردیف‌های PDF قابل تأیید نیست',
    'title_missing': 'عنوان ستون شرح بخش در PDF وجود ندارد یا کامل نیست',
    'identity_mismatch': 'کد، ردیف یا هویت دستگاه با Snapshot یکسان نیست',
    'description_mismatch': 'متن کامل شرح یا انتهای آن در ردیف دستگاه تأیید نشد',
    'section_mismatch': 'عنوان یا شرح بخش دیگر وارد خروجی شده است',
    'inventory_mismatch': 'ردیف دستگاه حذف، اضافه یا جابه‌جا شده است',
    'visibility_uncertain': 'خوانایی یا نمایش کامل متن PDF قابل تأیید نیست',
    'notice_mismatch': 'اعلان نبود شرح با داده Snapshot تطبیق ندارد',
}


class DriverPDFInvalid(ReportFailure):
    def __init__(self, report, section, defect, row=None):
        super().__init__('driver_pdf_content', stage='validation', sheet=report.get('sheet'), row=row,
                         column=report.get('columns', {}).get(section),
                         date=report['date'], section=section,
                         sheet_number=report.get('sheet_number'), defect=defect)


def _validate_driver_pdf_in_process(path, report, section, snapshot_sha256):
    """Require full content for each source row, not a global keyword match."""
    path = Path(path)
    content = report['sections'][section]
    defect, row_number = 'visibility_uncertain', None
    evidence = []
    try:
        if not 0 < path.stat().st_size <= 45*1024*1024:
            raise ValueError(defect)
        with fitz.open(path) as pdf:
            if pdf.is_repaired or pdf.needs_pass or not pdf.page_count:
                raise ValueError(defect)
            page_traces=[]
            for page in pdf:
                page.get_pixmap(matrix=fitz.Matrix(.5, .5), alpha=False)
                traces = page.get_texttrace()
                page_traces.append(traces)
                if not traces or any(s['type'] == 3 or s['opacity'] < .99 for s in traces):
                    raise ValueError(defect)
                for span in traces:
                    if not page.rect.contains(fitz.Rect(span['bbox'])):
                        raise ValueError(defect)
                    if all(c>.97 for c in span['color']):
                        raise ValueError(defect)
                if canonical(report['date']) not in canonical(page.get_text()):
                    raise ValueError(defect)
            if content['status'] != 'ready':
                # A zero description count is established by load_driver from
                # Excel, never inferred from a blank PDF.
                if content['rows'] or content['status'] not in {'no_defects','date_missing'}:
                    raise ValueError('notice_mismatch')
                text = '\n'.join(page.get_text() for page in pdf)
                if not all(canonical(v) in canonical(text) for v in
                           (content['title'], report['date'], content['message'])):
                    raise ValueError('notice_mismatch')
            else:
                inventory = report['inventory']
                expected = {canonical(r['values'][2]): r for r in inventory}
                column = 3 if section == 'mechanical' else 4
                fragments = []
                from integrations.hermes.function_domain.driver_report import SECTIONS
                other = SECTIONS['metalwork' if section == 'mechanical' else 'mechanical']
                for page in pdf:
                    xs, ys = _grid(page)
                    traces = page_traces[page.number]
                    title_seen = False
                    for top, bottom in zip(ys, ys[1:]):
                        cells = [_lines(page, fitz.Rect(left+.4, top+.4, right-.4, bottom-.4), traces)
                                 for left, right in zip(xs, xs[1:])]
                        # Native exporter fixes the table RTL: description,
                        # code, identity, ordinal. An uncertain layout blocks.
                        description, codes, identity, ordinal = cells
                        if equivalent(content['title'], description):
                            title_seen = True
                            continue
                        if equivalent(other, description):
                            raise ValueError('section_mismatch')
                        code = canonical(''.join(codes))
                        if code not in expected:
                            if title_seen and any(canonical(''.join(c)) for c in cells):
                                raise ValueError('inventory_mismatch')
                            continue
                        source = expected[code]; row_number = source['row']
                        if not title_seen:
                            raise ValueError('title_missing')
                        if not equivalent(source['values'][0], ordinal) or not equivalent(source['values'][1], identity):
                            raise ValueError('identity_mismatch')
                        fragments.append((code, description, page.number+1))
                    if not title_seen:
                        raise ValueError('title_missing')
                # Each device may have consecutive continuation rows, each
                # carrying its identity. Repeated descriptions cannot satisfy
                # another device, and a missing duplicate cannot pass.
                groups = []
                for code, lines, page in fragments:
                    if not groups or groups[-1][0] != code:
                        groups.append([code, [], []])
                    groups[-1][1].extend(lines); groups[-1][2].append(page)
                if [g[0] for g in groups] != [canonical(r['values'][2]) for r in inventory]:
                    raise ValueError('inventory_mismatch')
                for source, (code, lines, pages) in zip(inventory, groups):
                    row_number = source['row']
                    if not equivalent(source['values'][column], lines):
                        raise ValueError('description_mismatch')
                    evidence.append({'row': row_number, 'code': str(source['values'][2]),
                                     'pages': sorted(set(pages)), 'description_verified': True,
                                     'description_characters': len(canonical(source['values'][column]))})
            return {'status':'passed', 'section':section, 'date':report['date'],
                    'sheet':report.get('sheet'), 'pages':len(pdf),
                    'nonempty_descriptions':len(content['rows']), 'cells':evidence,
                    'snapshot_sha256':snapshot_sha256,
                    'pdf_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc) in DEFECTS:
            defect = str(exc)
        raise DriverPDFInvalid(report, section, defect, row_number) from exc


def validate_driver_pdf(path, report, section, snapshot_sha256):
    """Isolate the native PDF parser; a crash/timeout never grants permission.

    The installed parser can fail during interpreter teardown after repeated
    glyph tracing. Only a successful child exit AND a complete evidence packet
    are accepted. No global PDF/Excel/Gateway settings or libraries are changed.
    """
    try:
        payload={'path':str(Path(path).resolve()),'report':report,'section':section,
                 'snapshot_sha256':snapshot_sha256}
        result=subprocess.run([sys.executable,'-E','-s','-B','-X','utf8','-m',
            'tools.fleet.repairs.pdf_validation','--worker'],
            input=json.dumps(payload,ensure_ascii=False,default=str),text=True,
            encoding='utf-8',capture_output=True,timeout=120,
            cwd=Path(__file__).resolve().parents[3],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        packet=json.loads(result.stdout)
        if result.returncode==2 and packet.get('status')=='invalid':
            details=packet['details']
            raise DriverPDFInvalid(report,section,details['defect'],details.get('row'))
        if result.returncode or packet.get('status')!='passed':
            raise ValueError('Parser did not finish reliably')
        if (packet['pdf_sha256']!=hashlib.sha256(Path(path).read_bytes()).hexdigest()
                or packet['snapshot_sha256']!=snapshot_sha256
                or packet['date']!=report['date'] or packet['section']!=section):
            raise ValueError('Validation evidence changed')
        return packet
    except DriverPDFInvalid:
        raise
    except Exception as exc:
        raise DriverPDFInvalid(report,section,'visibility_uncertain') from exc


if __name__=='__main__':
    try:
        if sys.argv[1:]!=['--worker']:raise ValueError('Fixed validation worker only')
        payload=json.load(sys.stdin)
        packet=_validate_driver_pdf_in_process(**payload)
    except DriverPDFInvalid as exc:
        print(json.dumps({'status':'invalid','details':exc.details},ensure_ascii=False),flush=True)
        raise SystemExit(2)
    except Exception:
        raise SystemExit(3)
    print(json.dumps(packet,ensure_ascii=False),flush=True)
