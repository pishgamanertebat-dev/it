"""Exercise the approved boundary on isolated copies of the real XLSX and Journal."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

import openpyxl

from . import maintenance_service as service
from .maintenance_baseline import initialize
from .maintenance_sync import guard_snapshot, MaintenanceSyncBlocked
from .rehearse_safe_manual_excel import read_snapshot, preserved

ROOT = service.ROOT
OUT = ROOT / 'runtime/maintenance-baseline-20261107'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run():
    folder = OUT / 'real-copy'
    folder.mkdir(exist_ok=True)
    target, mine = folder / 'server.xlsx', folder / 'mine.xlsx'
    for path, origin in ((target, 'server'), (mine, 'mine')):
        path.write_bytes((OUT / 'before' / (origin + '.xlsx')).read_bytes())
    runtime = folder / 'journal'
    runtime.mkdir(exist_ok=True)
    with closing(sqlite3.connect(OUT / 'before/audit.sqlite3')) as src, \
         closing(sqlite3.connect(runtime / 'audit.sqlite3')) as dst:
        src.backup(dst)
        # Fixture containment only: map the recorded target to the isolated file.
        dst.execute('UPDATE edits SET source=?', (str(target.resolve()),))
        dst.commit()
    config = folder / 'config.json'
    config.write_text(json.dumps(dict(source=str(target), allowed_users=['455740857', '654806764'])), 'utf8')
    kw = dict(config=config, runtime=runtime)
    def edits():
        with closing(sqlite3.connect(runtime / 'audit.sqlite3')) as db:
            return db.execute('SELECT rowid,* FROM edits ORDER BY rowid').fetchall()
    original = target.read_bytes()
    historic = edits()
    result = initialize(mine, expected_hash=sha(mine), approved_by='project-owner',
                        decision='Isolated rehearsal of explicit owner acceptance; never replay history', **kw)
    assert result['historical_presence_proven'] is False
    assert original == target.read_bytes() == mine.read_bytes() and edits() == historic
    assert guard_snapshot(mine, target, **kw)['status'] == 'unchanged'
    assert guard_snapshot(mine, target, **kw)['status'] == 'unchanged'
    # The real historic operation remains succeeded, and is never re-appended.
    assert edits() == historic and historic[0][-3] == 'succeeded'
    before = read_snapshot(target)
    def request(code, description):
        return dict(service.preview('455740857', code, config=config, day='1405/08/16'),
                    mechanic='ISOLATED TEST', description=description, parts='No parts (test)',
                    operation=uuid.uuid4().hex)
    mine_request = request('HD707', 'MINE NEW EVENT')
    def append_mine(req):
        book = openpyxl.load_workbook(mine)
        try:
            value = service.append_record(book, req)
            book.save(mine)
            return value
        finally:
            book.close()
    mine_result = append_mine(mine_request)
    assert guard_snapshot(mine, target, **kw)['status'] == 'imported'
    assert target.read_bytes() == mine.read_bytes()
    rows = edits()
    assert guard_snapshot(mine, target, **kw)['status'] == 'unchanged' and edits() == rows
    imported = folder / 'mine-only-import.xlsx'
    imported.write_bytes(target.read_bytes())
    bale_request = request('EX1252', 'BALE PROTECTED EVENT')
    bale_result = service.commit('455740857', bale_request, day='1405/08/16', **kw)
    local_hash = sha(target)
    assert bale_request['operation'] in guard_snapshot(mine, target, **kw)['protected_operations']
    assert sha(target) == local_hash
    second_mine = request('HD707', 'SECOND MINE EVENT')
    second_result = append_mine(second_mine)
    merged = guard_snapshot(mine, target, **kw)
    assert merged['status'] == 'merged' and bale_request['operation'] in merged['protected_operations']
    assert service.commit('455740857', bale_request, day='1405/08/16', **kw) == bale_result
    assert guard_snapshot(mine, target, **kw)['status'] == 'unchanged'
    book = openpyxl.load_workbook(target)
    try:
        allowed = set()
        for record in (mine_result, second_result, bale_result):
            _, columns = service.layout(book[record['sheet']])
            allowed.update((record['sheet'], f'{openpyxl.utils.get_column_letter(c)}{record["row"]}')
                           for c in columns.values())
    finally:
        book.close()
    changed = (mine_result, second_result, bale_result)
    preserved(before, read_snapshot(target), allowed, tuple(v['sheet'] for v in changed),
              tuple((v['sheet'], v['row']) for v in changed))
    merged_file = folder / 'disjoint-merge.xlsx'
    merged_file.write_bytes(target.read_bytes())
    # Independent same-sheet mine row cannot overwrite/deduplicate the Bale UUID.
    append_mine(dict(bale_request, operation=uuid.uuid4().hex, mechanic='MINE INDEPENDENT'))
    local_bytes, mine_bytes = target.read_bytes(), mine.read_bytes()
    try:
        guard_snapshot(mine, target, **kw)
        raise AssertionError('Ambiguous branch was published')
    except MaintenanceSyncBlocked as exc:
        assert 'AMBIGUOUS_BRANCHES' in str(exc)
    assert local_bytes == target.read_bytes() and mine_bytes == mine.read_bytes()
    assert edits()[0] == historic[0]
    output = dict(passed=True, sheets=len(before), historic_operation=historic[0][1],
                  historical_row_unchanged=True, baseline_excel_unchanged=True,
                  mine_import_exact_bytes=True, disjoint_merge_preserves_all_historical_cells=True,
                  no_duplicate_on_retry=True, bale_uuid=bale_request['operation'],
                  ambiguous_branch_preserves_both=True,
                  tested_code={str(ROOT / name): sha(ROOT / name) for name in (
                      'tools/fleet/repairs/maintenance_baseline.py',
                      'tools/fleet/repairs/maintenance_sync.py',
                      'tools/fleet/repairs/maintenance_service.py',
                      'tools/fleet/repairs/rehearse_maintenance_baseline.py')},
                  native_files=[str(OUT / 'before/mine.xlsx'), str(OUT / 'before/server.xlsx'),
                                str(imported), str(merged_file)])
    (OUT / 'real-copy.json').write_text(json.dumps(output, indent=2), 'utf8')
    print(json.dumps(output))


if __name__ == '__main__':
    run()
