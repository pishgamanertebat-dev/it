"""Prepare private OOXML UAT copies, or compare pre/post native Save without writing them."""
import argparse
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import posixpath
import re
import sys
import uuid
import warnings
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import openpyxl

ROOT = Path('E:/KomatsoAI')
sys.path.insert(0, str(ROOT))
from tools.fleet.repairs import maintenance_baseline as base
from tools.fleet.repairs import maintenance_identity as identity
from tools.fleet.repairs import maintenance_ooxml as ooxml
from tools.fleet.repairs import maintenance_service as service

HERE = ROOT / 'runtime/maintenance-coordinated-candidate/final-acceptance-20261116'
PRIOR = ROOT / 'runtime/maintenance-coordinated-candidate/daily-1230-20261116'
Q = '{' + ooxml.MAIN + '}'
REVISION_UID = '{http://schemas.microsoft.com/office/spreadsheetml/2014/revision}uid'
VML = '{urn:schemas-microsoft-com:vml}'
EXCEL = '{urn:schemas-microsoft-com:office:excel}'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def print_markup(parts):
    def structure(node):
        return node.tag, node.attrib, node.text, [structure(child) for child in node]

    names = ('printOptions', 'pageMargins', 'pageSetup', 'headerFooter', 'rowBreaks', 'colBreaks')
    markup = {}
    for title, path in ooxml.sheet_paths(parts).items():
        tree = ET.fromstring(parts[path])
        markup[title] = {name: structure(node) for name in names if (node := tree.find(Q + name)) is not None}
    return markup


def sheet_parts(parts, kind):
    for title, sheet in ooxml.sheet_paths(parts).items():
        folder, name = posixpath.split(sheet)
        rels = ET.fromstring(parts.get(posixpath.join(folder, '_rels', name + '.rels'), b'<Relationships/>'))
        matches = [r for r in rels if r.get('Type') == ooxml.REL + kind]
        if not matches:
            continue
        if len(matches) != 1 or matches[0].get('TargetMode') == 'External':
            raise ValueError(f'Invalid {kind} relationship in {title}')
        target = ooxml.resolve(sheet, matches[0].attrib['Target'])
        yield title, target, parts[target]


def note_records(parts):
    records = {}
    for title, part, raw in sheet_parts(parts, 'comments'):
        root = ET.fromstring(raw)
        authors = [a.text for a in root.find(Q + 'authors')]
        for node in root.find(Q + 'commentList'):
            key = title, node.attrib['ref']
            author_index = int(node.attrib['authorId'])
            if key in records or not 0 <= author_index < len(authors):
                raise ValueError(f'Invalid or duplicate Note at {title}!{key[1]} in {part}')
            text = node.find(Q + 'text')
            records[key] = (part, authors[author_index],
                            ''.join(n.text or '' for n in text.iter(Q + 't')), node)
    return records


def note_shapes(parts):
    shapes = {}
    for title, part, raw in sheet_parts(parts, 'vmlDrawing'):
        for shape in ET.fromstring(raw).iter(VML + 'shape'):
            for client in shape.findall(EXCEL + 'ClientData'):
                if client.get('ObjectType') != 'Note':
                    continue
                row = int(client.findtext(EXCEL + 'Row')) + 1
                column = int(client.findtext(EXCEL + 'Column')) + 1
                if not (1 <= row <= 1048576 and 1 <= column <= 16384):
                    raise ValueError(f'Invalid Note anchor in {part}')
                address = f'{openpyxl.utils.get_column_letter(column)}{row}'
                key = title, address
                if key in shapes:
                    raise ValueError(f'Duplicate Note shape at {title}!{address} in {part}')
                style = dict(re.findall(r'([\w-]+)\s*:\s*([^;]+)', shape.get('style', '')))
                hidden = (style.get('visibility', '').strip() == 'hidden'
                          and client.find(EXCEL + 'Visible') is None)
                shapes[key] = dict(part=part, hidden=hidden,
                                   move_with_cells=client.find(EXCEL + 'MoveWithCells') is not None,
                                   size_with_cells=client.find(EXCEL + 'SizeWithCells') is not None,
                                   sha256=digest(ET.tostring(shape)))
    return shapes


def excel_author_rewrite(previous, current):
    """Recognize only the observed Excel wrapper around an exact original Note."""
    if (previous[1] != identity.AUTHOR or not previous[2].startswith(identity.PREFIX)
            or current[1] != 'Author' or current[2] != previous[2]):
        return False
    node = current[3]
    if (set(node.attrib) != {'ref', 'authorId', 'shapeId', REVISION_UID}
            or node.get('shapeId') != '0'
            or not re.fullmatch(r'\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}', node.attrib[REVISION_UID])
            or [child.tag for child in node] != [Q + 'text']):
        return False
    text = node[0]
    if text.attrib or [child.tag for child in text] != [Q + 'r']:
        return False
    run = text[0]
    if run.attrib or [child.tag for child in run] != [Q + 'rPr', Q + 't']:
        return False
    properties, payload = run
    return (not properties.attrib and not payload.attrib and not len(payload)
            and all(not len(prop) for prop in properties)
            and [(p.tag, p.attrib) for p in properties] == [
                (Q + 'sz', {'val': '11'}), (Q + 'color', {'theme': '1'}),
                (Q + 'rFont', {'val': 'Calibri'}), (Q + 'family', {'val': '2'}),
                (Q + 'scheme', {'val': 'minor'})]
            and not any((e.text or '').strip() for e in (node, text, run, properties, *properties))
            and not any((e.tail or '').strip() for e in node.iter() if e is not node))


def note_comparison(old, new, oldparts, newparts):
    before, after = [note_records(parts) for parts in (oldparts, newparts)]
    old_shapes, new_shapes = [note_shapes(parts) for parts in (oldparts, newparts)]
    checks = dict(count_before=len(before), count_after=len(after),
                  representation_changes=[], content_differences=[], shape_differences=[],
                  shape_binding_differences=[], invalid_identity_shapes=[])
    for key in sorted(before.keys() | after.keys() | old_shapes.keys() | new_shapes.keys()):
        title, address = key
        a, b = before.get(key), after.get(key)
        row = dict(sheet=title, cell=address, before_part=a[0] if a else None,
                   after_part=b[0] if b else None)
        for book, record in ((old, a), (new, b)):
            comment = book[title][address].comment if title in book.sheetnames else None
            seen = (comment.author, comment.text) if comment else None
            if seen != (record[1:3] if record else None):
                raise ValueError(f'OOXML and reader disagree at {title}!{address}')
        if a and b and excel_author_rewrite(a, b):
            checks['representation_changes'].append(dict(row, before_author=a[1], after_author=b[1],
                      before_note_sha256=digest(ET.tostring(a[3])), after_note_sha256=digest(ET.tostring(b[3]))))
            # Reader-only view for the existing UUID and row hash checks. The
            # original author change remains recorded above and no file is saved.
            new[title][address].comment.author = a[1]
        elif not a or not b or a[1:3] != b[1:3]:
            checks['content_differences'].append(row)
        if a and b and (a[0] != b[0] or ET.tostring(a[3]) != ET.tostring(b[3])):
            checks.setdefault('xml_differences', []).append(row)
        x, y = old_shapes.get(key), new_shapes.get(key)
        if x != y:
            checks['shape_differences'].append(dict(row, before_shape=x, after_shape=y))
        flags = ('hidden', 'move_with_cells', 'size_with_cells')
        if (tuple(x[k] for k in flags) if x else None) != (tuple(y[k] for k in flags) if y else None):
            checks['shape_binding_differences'].append(row)
        if any(record and record[2].startswith(identity.PREFIX) and
               (not shape or not all(shape[k] for k in flags))
               for record, shape in ((a, x), (b, y))):
            checks['invalid_identity_shapes'].append(row)
    return checks


def compare(before, after):
    olddata, newdata = before.read_bytes(), after.read_bytes()
    result = dict(before=str(before), after=str(after), before_sha256=digest(olddata),
                  after_sha256=digest(newdata), native_human_acceptance='NOT_EVALUATED_BY_COMPARATOR')
    oldparts, newparts = ooxml.package(olddata), ooxml.package(newdata)
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        old = openpyxl.load_workbook(io.BytesIO(olddata))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            new = openpyxl.load_workbook(io.BytesIO(newdata))
        try:
            return compare_books(result, old, new, oldparts, newparts, olddata == newdata)
        finally:
            new.close()
    finally:
        old.close()


def compare_books(result, old, new, oldparts, newparts, identical):
    result['notes'] = note_comparison(old, new, oldparts, newparts)
    oldstates, newstates = base.states(old), base.states(new)
    inventories = {}
    identity_errors = {}
    for side, book in (('before', old), ('after', new)):
        try:
            inventories[side] = identity.inventory(book, preserve_notes=True)
        except identity.EntryError as exc:
            identity_errors[side] = str(exc)
    oldids, newids = (inventories.get(side) for side in ('before', 'after'))
    fields = {}
    row_differences, data_differences = {}, {}
    print_area_differences, page_setup_differences = {}, {}
    for title in sorted(oldstates.keys() | newstates.keys()):
        a, b = oldstates.get(title, {}), newstates.get(title, {})
        different = sorted(field for field in a.keys() | b.keys() if a.get(field) != b.get(field))
        before_cells, after_cells = a.get('cells', {}), b.get('cells', {})
        cells = sorted(c for c in before_cells.keys() | after_cells.keys()
                       if before_cells.get(c) != after_cells.get(c))
        data = [c for c in cells if before_cells.get(c, (None,))[:2] != after_cells.get(c, (None,))[:2]
                and any(state.get(c, (None,))[0] is not None for state in (before_cells, after_cells))]
        if data:
            data_differences[title] = data
        if 'rows' in different:
            first, second = a.get('rows', {}), b.get('rows', {})
            row_differences[title] = {i: dict(before=first.get(i), after=second.get(i))
                                     for i in sorted(first.keys() | second.keys()) if first.get(i) != second.get(i)}
        if 'print_area' in different:
            print_area_differences[title] = dict(before=a.get('print_area'), after=b.get('print_area'))
        for name in ('page_setup', 'page_margins', 'print_options', 'HeaderFooter'):
            first, second = a.get('properties', {}).get(name), b.get('properties', {}).get(name)
            if first != second:
                page_setup_differences.setdefault(title, {})[name] = dict(before=first, after=second)
        if different:
            fields[title] = dict(fields=different, changed_cells=cells,
                                 property_differences={
                                     name: dict(before=a.get('properties', {}).get(name),
                                                after=b.get('properties', {}).get(name))
                                     for name in sorted(a.get('properties', {}).keys() | b.get('properties', {}).keys())
                                     if a.get('properties', {}).get(name) != b.get('properties', {}).get(name)})
    result.update(sheet_order_unchanged=old.sheetnames == new.sheetnames,
                  sheet_count_before=len(old.sheetnames), sheet_count_after=len(new.sheetnames),
                  identities_unchanged=not identity_errors and oldids == newids,
                  identity_count_before=len(oldids) if oldids is not None else None,
                  identity_count_after=len(newids) if newids is not None else None,
                  identity_errors=identity_errors, state_differences=fields,
                  row_differences=row_differences, data_differences=data_differences,
                  print_area_differences=print_area_differences, page_setup_differences=page_setup_differences,
                  nonempty_cells_before=sum(cell[0] is not None for s in oldstates.values() for cell in s['cells'].values()),
                  nonempty_cells_after=sum(cell[0] is not None for s in newstates.values() for cell in s['cells'].values()),
                  workbook_controls_unchanged=all(base.xml(getattr(old, f)) == base.xml(getattr(new, f))
                                                 for f in ('security', 'calculation')),
                  defined_names_unchanged=dict(old.defined_names) == dict(new.defined_names))
    result['removed_parts'] = sorted(oldparts.keys() - newparts.keys())
    result['added_parts'] = sorted(newparts.keys() - oldparts.keys())
    result['changed_parts'] = sorted(n for n in oldparts.keys() & newparts.keys() if oldparts[n] != newparts[n])
    result['printer_theme_bytes_unchanged'] = all(newparts.get(n) == raw for n, raw in oldparts.items()
        if n.startswith(('xl/printerSettings/', 'xl/theme/')))
    old_printers, new_printers = [
        {s: dict(part=p, sha256=digest(raw)) for s, p, raw in sheet_parts(parts, 'printerSettings')}
        for parts in (oldparts, newparts)]
    result['printer_settings_differences'] = {
        sheet: dict(before=old_printers.get(sheet), after=new_printers.get(sheet))
        for sheet in sorted(old_printers.keys() | new_printers.keys())
        if old_printers.get(sheet) != new_printers.get(sheet)}
    old_print, new_print = map(print_markup, (oldparts, newparts))
    result['raw_print_markup_differences'] = {
        sheet: sorted(name for name in old_print.get(sheet, {}).keys() | new_print.get(sheet, {}).keys()
                      if old_print.get(sheet, {}).get(name) != new_print.get(sheet, {}).get(name))
        for sheet in sorted(old_print.keys() | new_print.keys()) if old_print.get(sheet) != new_print.get(sheet)}
    # Normal Office rewrites must be reviewed too: no blanket normalization.
    if (not result['identities_unchanged'] or not result['sheet_order_unchanged'] or data_differences
            or result['notes']['content_differences'] or result['notes']['invalid_identity_shapes']):
        result['verdict'] = 'FAIL'
    else:
        result['verdict'] = 'PASS_IDENTICAL_COPY_ONLY' if identical else 'REVIEW_REQUIRED'
    return result


def prepare():
    HERE.mkdir(parents=True, exist_ok=True)
    evidence = json.loads((PRIOR / 'native-package-result.json').read_text('utf8'))
    original = (PRIOR / 'native-history/backups' / (evidence['source_sha256'] + '.xlsx')).read_bytes()
    migrated = Path(evidence['artifact']).read_bytes()
    assert digest(original) == evidence['source_sha256']
    assert digest(migrated) == evidence['output_sha256']
    book = ooxml.read(migrated)
    beforeids = identity.inventory(book, preserve_notes=True)
    assert len(book.sheetnames) == 63 and len(beforeids) == 25
    saved = []
    for name, marker in [('710', 'UAT-MACHINE-ONLY'), ('متفرقه', 'UAT-MISC-ONLY')]:
        selected = service.machine(book, name)
        request = dict(selected, date='1405/08/25', mechanic='آزمون پذیرش',
                       description=marker, parts='', department='آزمون')
        item = service.append_record(book, request, recorder='آزمون پذیرش')
        record = identity.attach(book, item, uuid.uuid4().hex, 0)
        saved.append(dict(sheet=item['sheet'], row=item['row'], marker=marker, identity=record))
    output = ooxml.dump(book)
    book.close()
    checked = ooxml.read(output)
    migratedbook = ooxml.read(migrated)
    originalbook = ooxml.read(original)
    try:
        ids = identity.inventory(checked, preserve_notes=True)
        assert len(ids) == 27 and all(ids.get(k) == v for k, v in beforeids.items())
        # All history before the two new test rows must match, including styles/notes.
        oldstates, newstates = base.states(migratedbook), base.states(checked)
        appended = {(row['sheet'], row['row']) for row in saved}
        for title, state in oldstates.items():
            for coordinate, value in state['cells'].items():
                if newstates[title]['cells'].get(coordinate) != value:
                    previous = migratedbook[title][coordinate]
                    assert ((title, previous.row) in appended and previous.value is None
                            and previous.comment is None and previous.hyperlink is None), (title, coordinate)
        oldcells = newcells = 0
        for sheet in originalbook:
            for cell in sheet._cells.values():
                if cell.value is not None:
                    oldcells += 1
                    other = checked[sheet.title][cell.coordinate]
                    assert (cell.value, cell.data_type) == (other.value, other.data_type), (sheet.title, cell.coordinate)
                    newcells += 1
        a, b = ooxml.package(migrated), ooxml.package(output)
        assert a.keys() <= b.keys()
        assert all(b[n] == raw for n, raw in a.items() if n.startswith(('xl/printerSettings/', 'xl/theme/')))
        path = HERE / 'native-uat-before.xlsx'
        if path.exists():
            raise FileExistsError('UAT input is immutable; do not regenerate after owner starts')
        path.write_bytes(output)
        # Working copy only. Owner may Save As from here to native-uat-after.xlsx.
        (HERE / 'native-uat-working.xlsx').write_bytes(output)
        manifest = dict(time_tehran=datetime.now(ZoneInfo('Asia/Tehran')).isoformat(),
            input=str(path), input_sha256=digest(output), migrated_sha256=digest(migrated),
            source_sha256=digest(original), sheets=checked.sheetnames,
            original_nonempty_cells_preserved=oldcells == newcells, original_nonempty_cell_count=oldcells,
            migrated_records_retained=25, total_test_record_identities=27, added_test_rows=saved,
            identities=ids, original_parts_retained=True, printer_theme_bytes_preserved=True,
            native_status='NATIVE_UAT_PENDING', machine_status='PASS_PACKAGE_AND_READER_ONLY')
        (HERE / 'native-uat-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf8')
        print(json.dumps({k: v for k, v in manifest.items() if k not in ('sheets', 'identities')}, ensure_ascii=True))
    finally:
        for item in (checked, migratedbook, originalbook):
            item.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--before', type=Path)
    parser.add_argument('--after', type=Path)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    if args.prepare:
        prepare()
    elif args.before and args.after and args.result:
        if any(args.result.resolve() == path.resolve() or
               (args.result.exists() and path.exists() and args.result.samefile(path))
               for path in (args.before, args.after)):
            parser.error('--result must be separate from both input workbooks')
        try:
            result = compare(args.before, args.after)
        except Exception as exc:
            result = dict(verdict='FAIL', error_type=type(exc).__name__, error=str(exc))
        args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf8')
        print(json.dumps(result, ensure_ascii=True))
        raise SystemExit(0 if result['verdict'] == 'PASS_IDENTICAL_COPY_ONLY' else 1)
    else:
        parser.error('use --prepare or all of --before --after --result')
