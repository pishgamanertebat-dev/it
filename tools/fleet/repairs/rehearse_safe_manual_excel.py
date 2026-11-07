"""Acceptance on byte copies of the real workbooks; never publish to E:/Function."""
from copy import copy
import hashlib
import json
from pathlib import Path
import tempfile
import uuid
from xml.etree.ElementTree import tostring

import openpyxl

from . import entry_service as drivers, maintenance_service as repairs
from .maintenance_sync import guard_snapshot, MaintenanceSyncBlocked
from .sync_service import initialize, merge_snapshot

OUT = drivers.ROOT / 'runtime/safe-manual-excel-20261107'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(book):
    def xml(value):
        return tostring(value.to_tree()).decode() if value is not None else None
    return {sheet.title: dict(
        cells={cell.coordinate: (cell.value, cell.data_type, copy(cell._style),
               (cell.comment.text, cell.comment.author) if cell.comment else None,
               cell.hyperlink.target if cell.hyperlink else None)
               for cell in sheet._cells.values()},
        rows={key: (dict(value), copy(value._style)) for key, value in sheet.row_dimensions.items()},
        columns={key: xml(value) for key, value in sheet.column_dimensions.items()},
        merges=sorted(str(value) for value in sheet.merged_cells.ranges),
        settings={key: xml(getattr(sheet, key)) for key in
                  ('page_setup', 'page_margins', 'print_options', 'sheet_properties', 'sheet_format')},
        print_area=str(sheet.print_area), titles=(sheet.print_title_rows, sheet.print_title_cols),
        state=sheet.sheet_state) for sheet in book}


def preserved(before, after, allowed=(), print_changes=(), row_changes=()):
    assert before.keys() == after.keys(), 'Sheet list changed'
    for title, previous in before.items():
        current = after[title]
        for field in ('columns', 'merges', 'settings', 'titles', 'state'):
            assert previous[field] == current[field], (title, field)
        if title not in print_changes:
            assert previous['print_area'] == current['print_area'], (title, 'print_area')
        for row, value in previous['rows'].items():
            if (title, row) not in row_changes:
                assert current['rows'].get(row) == value, (title, 'height', row)
        for coord, value in previous['cells'].items():
            if (title, coord) not in allowed:
                assert current['cells'].get(coord) == value, (title, coord)


def read_snapshot(path):
    book = openpyxl.load_workbook(path)
    try:
        return snapshot(book)
    finally:
        book.close()


def run():
    directory = OUT / 'real-copy'
    directory.mkdir(exist_ok=True)
    tempfile.tempdir = str(directory)
    live = [Path(json.loads(path.read_text(encoding='utf8'))['source'])
            for path in (repairs.CONFIG, drivers.CONFIG)]
    live_before = {str(path): digest(path) for path in live}
    target = directory / 'maintenance-local.xlsx'
    target.write_bytes(live[0].read_bytes())
    incoming = directory / 'maintenance-mine.xlsx'
    incoming.write_bytes(target.read_bytes())
    config = directory / 'maintenance.json'
    config.write_text(json.dumps(dict(source=str(target), allowed_users=['455740857', '654806764'])))
    runtime = directory / 'maintenance-journal'
    before = read_snapshot(target)
    request = dict(repairs.preview('654806764', 'HD707', config=config, day='1405/08/16'),
                   mechanic='ISOLATED TEST', description='SAFE MANUAL TEST\nSecond line',
                   parts='No parts (test)', operation=uuid.uuid4().hex)
    result = repairs.commit('654806764', request, config=config, runtime=runtime, day='1405/08/16')
    historical_book = openpyxl.load_workbook(target)
    try:
        _, columns = repairs.layout(historical_book[result['sheet']])
    finally:
        historical_book.close()
    allowed = {(result['sheet'], f'{openpyxl.utils.get_column_letter(col)}{result["row"]}')
               for col in columns.values()}
    preserved(before, read_snapshot(target), allowed, (result['sheet'],), ((result['sheet'], result['row']),))
    committed = digest(target)
    for _ in range(2):
        try:
            guard_snapshot(incoming, target, config=config, runtime=runtime)
            raise AssertionError('Differing mine snapshot was accepted')
        except MaintenanceSyncBlocked:
            pass
    assert digest(target) == committed
    assert repairs.commit('654806764', request, config=config, runtime=runtime, day='1405/08/16') == result
    assert digest(target) == committed
    # Real mine copy receives an independent event with the SAME text.
    book = openpyxl.load_workbook(incoming)
    try:
        repairs.append_record(book, dict(request, operation=uuid.uuid4().hex, mechanic='MINE TEST'))
        book.save(incoming)
    finally:
        book.close()
    mine_hash = digest(incoming)
    try:
        guard_snapshot(incoming, target, config=config, runtime=runtime)
        raise AssertionError('Concurrent mine event was incorrectly merged')
    except MaintenanceSyncBlocked:
        pass
    assert digest(incoming) == mine_hash and digest(target) == committed
    # Every real maintenance sheet is exercised, with existing cells preserved.
    all_sheets = directory / 'maintenance-all-sheets.xlsx'
    book = openpyxl.load_workbook(live[0])
    all_results = []
    try:
        for selected in repairs.machines(book):
            all_results.append(repairs.append_record(book, dict(selected, date='1405/08/16',
                mechanic='ISOLATED TEST', description='SAFE TEST\nSecond line', parts='No parts (test)')))
        book.save(all_sheets)
    finally:
        book.close()
    all_allowed, all_rows = set(), set()
    book = openpyxl.load_workbook(all_sheets)
    try:
        for saved in all_results:
            _, cols = repairs.layout(book[saved['sheet']])
            all_allowed.update((saved['sheet'], f'{openpyxl.utils.get_column_letter(col)}{saved["row"]}')
                               for col in cols.values())
            all_rows.add((saved['sheet'], saved['row']))
    finally:
        book.close()
    preserved(before, read_snapshot(all_sheets), all_allowed,
              tuple(value['sheet'] for value in all_results), all_rows)

    # Existing Drivers2 merge, unchanged, using its own temporary journal.
    driver_target = directory / 'drivers-local.xlsx'
    driver_mine = directory / 'drivers-mine.xlsx'
    driver_target.write_bytes(live[1].read_bytes())
    driver_mine.write_bytes(driver_target.read_bytes())
    settings = json.loads(drivers.CONFIG.read_text(encoding='utf8'))
    settings['source'] = str(driver_target)
    template = directory / 'driver-template.xlsx'
    template.write_bytes(Path(settings['template']).read_bytes())
    settings['template'] = str(template)
    driver_config = directory / 'drivers.json'
    driver_config.write_text(json.dumps(settings))
    driver_runtime = directory / 'drivers-journal'
    kw = dict(config=driver_config, runtime=driver_runtime)
    initialize(driver_mine, **kw)
    before_driver = read_snapshot(driver_target)
    book = openpyxl.load_workbook(driver_target)
    try:
        day, sheet = drivers.dated_sheets(book)[0]
        title = sheet.title
        cols = drivers.layout(sheet)
        occupied = drivers.machine_rows(sheet, cols)
        assert occupied, 'Latest real report has no machine row to exercise'
        row = occupied[0]
        code = drivers.machine(book, str(sheet.cell(row, cols['code']).value))['code']
    finally:
        book.close()
    requests = []
    for section in ('mechanical', 'metalwork'):
        selected = drivers.preview('455740857', code, section, config=driver_config, day=day)
        requests.append(dict(selected, description='ISOLATED '+section, operation=uuid.uuid4().hex))
    drivers.commit('455740857', requests[0], day=day, **kw)
    after_mechanical = read_snapshot(driver_target)
    other_coord = f'{openpyxl.utils.get_column_letter(cols["metalwork"])}{row}'
    assert before_driver[title]['cells'][other_coord] == after_mechanical[title]['cells'][other_coord]
    drivers.commit('455740857', requests[1], day=day, **kw)
    addressed = {(title, f'{openpyxl.utils.get_column_letter(cols[section])}{row}')
                 for section in ('mechanical', 'metalwork')}
    preserved(before_driver, read_snapshot(driver_target), addressed, row_changes=((title, row),))
    # Simulated nonconflicting manual mine content in an unrelated column.
    book = openpyxl.load_workbook(driver_mine)
    try:
        book[title]['H3'] = 'ISOLATED MINE UPDATE'
        book.save(driver_mine)
    finally:
        book.close()
    merged = merge_snapshot(driver_mine, driver_target, **kw)
    assert merged['protected_cells'] == 2
    candidate_hash = digest(driver_target)
    assert merge_snapshot(driver_mine, driver_target, **kw)['status'] == 'unchanged'
    assert digest(driver_target) == candidate_hash
    book = openpyxl.load_workbook(driver_target)
    try:
        assert book[title]['H3'].value == 'ISOLATED MINE UPDATE'
        for request in requests:
            assert book[title].cell(row, cols[request['section']]).value == request['description']
    finally:
        book.close()
    preserved(before_driver, read_snapshot(driver_target), addressed | {(title, 'H3')},
              row_changes=((title, row),))
    book = openpyxl.load_workbook(driver_mine)
    try:
        book[title].cell(row, cols['mechanical'], 'ACTUAL CONFLICT')
        book.save(driver_mine)
    finally:
        book.close()
    try:
        merge_snapshot(driver_mine, driver_target, **kw)
        raise AssertionError('True Drivers2 conflict accepted')
    except drivers.EntryError as exc:
        assert 'CONFLICT' in str(exc)
    assert digest(driver_target) == candidate_hash
    assert all(digest(Path(path)) == value for path, value in live_before.items())
    result = dict(safe_guard_passed=True, live_workbooks_unchanged=True, historical_cells_styles_formulas_preserved=True,
                  maintenance_sheets_checked=len(all_results), drivers_sheets_checked=len(before_driver),
                  maintenance_merge='BLOCKED: both snapshots retained, manual reconciliation required',
                  drivers_merge='PASS', native_excel_passed=False,
                  native_files=[str(target), str(all_sheets), str(driver_target)], live_hashes=live_before)
    (OUT / 'real-copy.json').write_text(json.dumps(result, indent=2), encoding='utf8')
    print(json.dumps(result))


if __name__ == '__main__':
    run()
