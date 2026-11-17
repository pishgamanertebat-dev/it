"""Prepare private OOXML UAT copies, or compare pre/post native Save without writing them."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import uuid
from zoneinfo import ZoneInfo

ROOT = Path('E:/KomatsoAI')
sys.path.insert(0, str(ROOT))
from tools.fleet.repairs import maintenance_baseline as base
from tools.fleet.repairs import maintenance_identity as identity
from tools.fleet.repairs import maintenance_ooxml as ooxml
from tools.fleet.repairs import maintenance_service as service

HERE = ROOT / 'runtime/maintenance-coordinated-candidate/final-acceptance-20261116'
PRIOR = ROOT / 'runtime/maintenance-coordinated-candidate/daily-1230-20261116'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def compare(before, after):
    olddata, newdata = before.read_bytes(), after.read_bytes()
    result = dict(before=str(before), after=str(after), before_sha256=digest(olddata),
                  after_sha256=digest(newdata), native_human_acceptance='NATIVE_UAT_PENDING')
    oldparts, newparts = ooxml.package(olddata), ooxml.package(newdata)
    old, new = ooxml.read(olddata), ooxml.read(newdata)
    try:
        oldstates, newstates = base.states(old), base.states(new)
        oldids, newids = [identity.inventory(b, preserve_notes=True) for b in (old, new)]
        fields = {}
        for title in oldstates.keys() | newstates.keys():
            a, b = oldstates.get(title, {}), newstates.get(title, {})
            different = [field for field in a.keys() | b.keys() if a.get(field) != b.get(field)]
            if different:
                fields[title] = dict(fields=different,
                    changed_cells=[c for c in a.get('cells', {}).keys() | b.get('cells', {}).keys()
                                   if a.get('cells', {}).get(c) != b.get('cells', {}).get(c)])
        result.update(sheet_order_unchanged=old.sheetnames == new.sheetnames,
                      identities_unchanged=oldids == newids, identity_count_before=len(oldids),
                      identity_count_after=len(newids), state_differences=fields,
                      workbook_controls_unchanged=all(base.xml(getattr(old, f)) == base.xml(getattr(new, f))
                                                     for f in ('security', 'calculation')),
                      defined_names_unchanged=dict(old.defined_names) == dict(new.defined_names))
        result['removed_parts'] = sorted(oldparts.keys() - newparts.keys())
        result['added_parts'] = sorted(newparts.keys() - oldparts.keys())
        result['changed_parts'] = sorted(n for n in oldparts.keys() & newparts.keys() if oldparts[n] != newparts[n])
        result['printer_theme_bytes_unchanged'] = all(newparts.get(n) == raw for n, raw in oldparts.items()
            if n.startswith(('xl/printerSettings/', 'xl/theme/')))
        # Normal Office rewrites must be reviewed too: no blanket normalization.
        result['verdict'] = 'PASS_IDENTICAL_COPY_ONLY' if olddata == newdata else 'REVIEW_REQUIRED'
        return result
    finally:
        old.close()
        new.close()


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
        try:
            result = compare(args.before, args.after)
        except Exception as exc:
            result = dict(verdict='FAIL_OR_UNREADABLE', error_type=type(exc).__name__, error=str(exc))
        args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf8')
        print(json.dumps(result, ensure_ascii=True))
        raise SystemExit(0 if result['verdict'] == 'PASS_IDENTICAL_COPY_ONLY' else 1)
    else:
        parser.error('use --prepare or all of --before --after --result')
