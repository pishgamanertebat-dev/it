"""Owner-approved maintenance boundary and conservative, journal-proven imports.

Old edit rows remain evidence, not a replay queue. New mine rows may be imported
verbatim. Concurrent branches merge only on disjoint worksheets, after proving
both the Writer UUID/hash chain and unchanged historical cells/worksheet layout.
"""
from contextlib import closing, contextmanager
from copy import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import uuid
from xml.etree.ElementTree import tostring
import zipfile

import openpyxl

from .entry_service import (EntryError, open_audit, publish_candidate, recover_prepared,
                            writer_lock)
from .maintenance_service import CONFIG, RUNTIME, layout

KIND = 'maintenance_mine_sync'
EVENT = 'OWNER_APPROVED_BASELINE'


@contextmanager
def baseline_reader(path):
    """Baseline creation changes no workbook; deny both writes and replacement."""
    if os.name != 'nt':
        with Path(path).open('rb') as stream:
            yield stream
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    handle = api.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    with os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), 'rb') as stream:
        yield stream


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def history(db, source, cutoff):
    return [dict(row) for row in db.execute('SELECT rowid,* FROM edits WHERE source=? COLLATE NOCASE '
                                           'AND rowid<=? ORDER BY rowid', (str(source), cutoff))]


def check_journal(db):
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise EntryError('MAINTENANCE_SYNC_JOURNAL_CORRUPT')


def validate_workbook(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.testzip() is not None:
            raise EntryError('MAINTENANCE_SYNC_INVALID_WORKBOOK')
        # openpyxl cannot promise preservation of these package objects.
        if any(name.startswith(('xl/drawings/', 'xl/charts/', 'xl/pivot', 'xl/externalLinks/'))
               or name.endswith('vbaProject.bin') for name in archive.namelist()):
            raise EntryError('MAINTENANCE_SYNC_UNSUPPORTED_PACKAGE: manual review required')
    book = openpyxl.load_workbook(io.BytesIO(data))
    try:
        for sheet in book:
            layout(sheet)
        return book
    except BaseException:
        book.close()
        raise


def schema(db):
    with db:
        db.execute('''CREATE TABLE IF NOT EXISTS maintenance_baseline_events (
            event_id TEXT PRIMARY KEY, source TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
            payload TEXT NOT NULL, sha256 TEXT NOT NULL, created TEXT NOT NULL)''')
        for action in ('UPDATE', 'DELETE'):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS maintenance_baseline_no_{action.lower()}
                BEFORE {action} ON maintenance_baseline_events
                BEGIN SELECT RAISE(ABORT, 'immutable owner baseline'); END''')


def initialize(upstream, *, expected_hash, decision, approved_by, config=CONFIG, runtime=RUNTIME):
    """Administrative deployment only; no Bale callback or free-text route calls this."""
    with writer_lock(Path(runtime)):
        return initialize_locked(upstream, expected_hash=expected_hash, decision=decision,
                                 approved_by=approved_by, config=config, runtime=runtime)


def initialize_locked(upstream, *, expected_hash, decision, approved_by, config=CONFIG, runtime=RUNTIME):
    """Deployment entrypoint; caller MUST hold the same runtime Writer lock."""
    if approved_by != 'project-owner' or len(expected_hash) != 64 or not decision.strip():
        raise PermissionError('MAINTENANCE_BASELINE_OWNER_APPROVAL_REQUIRED')
    from .maintenance_sync import retain_snapshot
    runtime = Path(runtime)
    settings = json.loads(Path(config).read_text('utf8'))
    source = Path(settings['source']).resolve(strict=True)
    if not (runtime / 'audit.sqlite3').is_file():
        raise EntryError('MAINTENANCE_SYNC_JOURNAL_MISSING')
    with closing(open_audit(runtime)) as db, baseline_reader(source) as local, baseline_reader(upstream) as mine:
        check_journal(db)
        original, incoming = local.read(), mine.read()
        if digest(original) != expected_hash or digest(incoming) != expected_hash:
            raise EntryError('MAINTENANCE_BASELINE_CHANGED: approved mine/server bytes required')
        book = validate_workbook(original)
        book.close()
        unresolved = db.execute("SELECT operation FROM edits WHERE source=? COLLATE NOCASE "
                                "AND (status IS NULL OR status NOT IN ('succeeded','not_applied'))",
                                (str(source),)).fetchall()
        if unresolved:
            raise EntryError('MAINTENANCE_BASELINE_UNRESOLVED_OPERATION')
        schema(db)
        existing = baseline(db, source, runtime)
        if existing:
            if existing['initial_hash'] != expected_hash or existing['decision'] != decision:
                raise EntryError('MAINTENANCE_BASELINE_ALREADY_EXISTS')
            return dict(status='already_enabled', **existing)
        retained = retain_snapshot(runtime, original)
        cutoff = db.execute('SELECT COALESCE(MAX(rowid),0) FROM edits').fetchone()[0]
        historical = history(db, source, cutoff)
        payload = dict(event_id=uuid.uuid4().hex, kind=EVENT, source=str(source),
                       upstream=str(Path(upstream)), initial_hash=expected_hash,
                       mine_hash=expected_hash, cutoff=cutoff, snapshot=str(retained),
                       history_sha256=digest(encoded(historical).encode()),
                       historical_operations=historical, decision=decision, approved_by=approved_by,
                       created=datetime.now(timezone.utc).isoformat(),
                       historical_presence_proven=False)
        # One durable transaction: no partially enabled state after interruption.
        with db:
            db.execute('INSERT INTO maintenance_baseline_events VALUES (?,?,?,?,?,?)',
                       (payload['event_id'], str(source), EVENT, encoded(payload),
                        digest(encoded(payload).encode()), payload['created']))
        return dict(status='enabled', **payload)


def baseline(db, source, runtime):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                      "AND name='maintenance_baseline_events'").fetchone():
        return None
    row = db.execute('SELECT * FROM maintenance_baseline_events WHERE source=? COLLATE NOCASE',
                     (str(source),)).fetchone()
    if row is None:
        return None
    if row['kind'] != EVENT or digest(row['payload'].encode()) != row['sha256']:
        raise EntryError('MAINTENANCE_SYNC_BASELINE_CORRUPT')
    payload = json.loads(row['payload'])
    if (payload['source'].casefold() != str(source).casefold()
            or payload['history_sha256'] != digest(encoded(history(db, source, payload['cutoff'])).encode())):
        raise EntryError('MAINTENANCE_SYNC_HISTORICAL_JOURNAL_CHANGED')
    snapshot(runtime, payload['initial_hash'])
    return payload


def snapshot(runtime, expected):
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise EntryError('MAINTENANCE_SYNC_INVALID_JOURNAL_HASH')
    path = Path(runtime) / 'backups' / (expected + '.xlsx')
    if not path.is_file() or digest(path.read_bytes()) != expected:
        raise EntryError('MAINTENANCE_SYNC_SNAPSHOT_CORRUPT: ' + expected)
    return path.read_bytes()


def xml(value):
    return tostring(value.to_tree()).decode() if value is not None else None


def cell_state(cell):
    return (cell.value, cell.data_type, *(xml(getattr(cell, field)) for field in
            ('font', 'fill', 'border', 'alignment', 'protection')), cell.number_format,
            (cell.comment.text, cell.comment.author) if cell.comment else None,
            (cell.hyperlink.target, cell.hyperlink.location, cell.hyperlink.tooltip,
             cell.hyperlink.display) if cell.hyperlink else None)


def sheet_state(sheet):
    return dict(cells={c.coordinate: cell_state(c) for c in sheet._cells.values()
                       if c.value is not None or c.has_style or c.comment or c.hyperlink},
                rows={r: (dict(d), tuple(d._style) if d.has_style else None)
                      for r, d in sheet.row_dimensions.items()},
                columns={c: xml(d) for c, d in sheet.column_dimensions.items()},
                merges=sorted(map(str, sheet.merged_cells.ranges)),
                properties={name: xml(getattr(sheet, name)) for name in (
                    'sheet_properties', 'sheet_format', 'page_margins', 'page_setup',
                    'print_options', 'HeaderFooter', 'views', 'protection')
                    if hasattr(sheet, name)},
                tables=[xml(t) for t in sheet.tables.values()],
                validations=xml(sheet.data_validations),
                conditional=[(str(k), [xml(r) for r in v]) for k,v in sheet.conditional_formatting._cf_rules.items()],
                filter=xml(sheet.auto_filter), state=sheet.sheet_state,
                print_area=str(sheet.print_area), titles=(sheet.print_title_rows, sheet.print_title_cols))


def states(book):
    return {s.title: sheet_state(s) for s in book}


def additions(base, mine):
    """Accept only appended content: never infer identity or authorize historic edits."""
    if base.sheetnames != mine.sheetnames or dict(base.defined_names) != dict(mine.defined_names):
        raise EntryError('MAINTENANCE_SYNC_STRUCTURE_CONFLICT: sheet list or defined names')
    if xml(base.security) != xml(mine.security) or xml(base.calculation) != xml(mine.calculation):
        raise EntryError('MAINTENANCE_SYNC_STRUCTURE_CONFLICT: workbook security or calculation')
    before, after = states(base), states(mine)
    changed = []
    for title, previous in before.items():
        current = after[title]
        if current == previous:
            continue
        for field in previous.keys() - {'cells', 'rows', 'print_area'}:
            if previous[field] != current[field]:
                raise EntryError(f'MAINTENANCE_SYNC_STRUCTURE_CONFLICT: sheet={title} field={field}')
        last = max((c.row for c in base[title]._cells.values() if c.value is not None), default=0)
        for coordinate in previous['cells'].keys() | current['cells'].keys():
            row = openpyxl.utils.cell.coordinate_from_string(coordinate)[1]
            if previous['cells'].get(coordinate) != current['cells'].get(coordinate) and row <= last:
                raise EntryError(f'MAINTENANCE_SYNC_HISTORICAL_CELL_CONFLICT: sheet={title} cell={coordinate}')
        for row in previous['rows'].keys() | current['rows'].keys():
            if row <= last and previous['rows'].get(row) != current['rows'].get(row):
                raise EntryError(f'MAINTENANCE_SYNC_STRUCTURE_CONFLICT: sheet={title} row={row}')
        # Print area may extend down, but cannot discard existing coverage.
        old_ranges = list(openpyxl.worksheet.print_settings.PrintArea.from_string(previous['print_area']).ranges)
        new_ranges = list(openpyxl.worksheet.print_settings.PrintArea.from_string(current['print_area']).ranges)
        if len(old_ranges) != len(new_ranges) or any(not any(
                (n.min_col, n.max_col, n.min_row) == (o.min_col, o.max_col, o.min_row)
                and n.max_row >= o.max_row for n in new_ranges) for o in old_ranges):
            raise EntryError(f'MAINTENANCE_SYNC_STRUCTURE_CONFLICT: sheet={title} print_area')
        changed.append(title)
    return changed


def combine(base_data, local_data, incoming_data):
    base, local, mine = [validate_workbook(v) for v in (base_data, local_data, incoming_data)]
    try:
        mine_sheets = additions(base, mine)
        if local.sheetnames != base.sheetnames:
            raise EntryError('MAINTENANCE_SYNC_LOCAL_STRUCTURE_CONFLICT')
        original, upstream_base, incoming = states(local), states(base), states(mine)
        local_sheets = [title for title in original if original[title] != upstream_base[title]]
        overlap = sorted(set(mine_sheets) & set(local_sheets))
        if overlap:
            raise EntryError('MAINTENANCE_SYNC_AMBIGUOUS_BRANCHES: sheets=' + ','.join(overlap))
        for title in mine_sheets:
            target, source = local[title], mine[title]
            # Only the proven new upstream rows are copied. No Bale append/replay.
            for coordinate in original[title]['cells'].keys() | incoming[title]['cells'].keys():
                if original[title]['cells'].get(coordinate) == incoming[title]['cells'].get(coordinate):
                    continue
                old, new = source[coordinate], target[coordinate]
                new.value, new.data_type = old.value, old.data_type
                for field in ('font', 'fill', 'border', 'alignment', 'protection'):
                    setattr(new, field, copy(getattr(old, field)))
                new.number_format = old.number_format
                new.comment, new._hyperlink = copy(old.comment), copy(old.hyperlink)
            for row, dim in source.row_dimensions.items():
                if original[title]['rows'].get(row) != incoming[title]['rows'].get(row):
                    target.row_dimensions[row] = copy(dim)
            target.print_area = str(source.print_area)
        output = io.BytesIO()
        local.save(output)
        candidate = output.getvalue()
        check = validate_workbook(candidate)
        try:
            expected = dict(original)
            expected.update({title: incoming[title] for title in mine_sheets})
            if states(check) != expected:
                raise EntryError('MAINTENANCE_SYNC_MERGE_VALIDATION_FAILED')
        finally:
            check.close()
        return candidate, mine_sheets
    finally:
        for book in (base, local, mine):
            book.close()


def sync_from_baseline(db, source, original, incoming, runtime):
    """Caller holds the existing Writer lock and deny-write source handle."""
    state = baseline(db, source, runtime)
    if state is None:
        return None
    check_journal(db)
    current_hash, incoming_hash = digest(original), digest(incoming)
    recover_prepared(db, source, current_hash)
    latest, mine_hash = state['initial_hash'], state['mine_hash']
    protected = []
    rows = db.execute('SELECT rowid,* FROM edits WHERE source=? COLLATE NOCASE AND rowid>? '
                      'ORDER BY rowid', (str(source), state['cutoff'])).fetchall()
    for row in rows:
        if row['status'] == 'not_applied':
            continue
        if row['status'] != 'succeeded' or row['before_hash'] != latest:
            raise EntryError('MAINTENANCE_SYNC_JOURNAL_CHAIN_CONFLICT: operation=' + row['operation'])
        snapshot(runtime, row['before_hash'])
        snapshot(runtime, row['after_hash'])
        request, result = json.loads(row['request']), json.loads(row['result'])
        fingerprint = digest(json.dumps([str(row['actor']), str(source), request],
                                        sort_keys=True, ensure_ascii=False).encode())
        if (not re.fullmatch('[0-9a-f]{32}', row['operation']) or request.get('operation') != row['operation']
                or row['fingerprint'] != fingerprint):
            raise EntryError('MAINTENANCE_SYNC_INVALID_JOURNAL_FINGERPRINT: ' + str(row['operation']))
        if request.get('_kind') == KIND:
            mine_hash = result['mine_hash']
            snapshot(runtime, mine_hash)
        else:
            if (not row['operation'] or result.get('sheet') != request.get('sheet')
                    or not all(field in request for field in ('canonical', 'mechanic', 'description', 'parts'))):
                raise EntryError('MAINTENANCE_SYNC_INVALID_WRITER_OPERATION: ' + row['operation'])
            protected.append(row['operation'])
        latest = row['after_hash']
    if current_hash != latest:
        raise EntryError('MAINTENANCE_SYNC_UNJOURNALED_LOCAL_CHANGE')
    if incoming_hash == mine_hash:
        return dict(status='unchanged', hash=current_hash, mine_hash=mine_hash,
                    protected_operations=protected, baseline=state['event_id'])
    from .maintenance_sync import retain_snapshot
    retain_snapshot(runtime, incoming)
    if incoming_hash == current_hash:
        # Equality preserves the published file, but cannot establish that a
        # matching mine row is the same event. Keep the old upstream base;
        # later overlapping mine changes still require explicit review.
        return dict(status='unchanged', hash=current_hash, mine_hash=mine_hash,
                    protected_operations=protected, baseline=state['event_id'],
                    upstream_identity_unacknowledged=True)
    base_data = snapshot(runtime, mine_hash)
    if current_hash == mine_hash:
        base, mine = validate_workbook(base_data), validate_workbook(incoming)
        try:
            changed = additions(base, mine)
        finally:
            base.close()
            mine.close()
        candidate = incoming  # Preserve exact upstream bytes when no local branch exists.
    else:
        candidate, changed = combine(base_data, original, incoming)
    result = dict(status='imported' if current_hash == mine_hash else 'merged', mine_hash=incoming_hash,
                  changed_sheets=changed, protected_operations=protected, baseline=state['event_id'])
    return publish_candidate(source, original, candidate, db, 'mine-file-sync',
                             dict(_kind=KIND, operation=uuid.uuid4().hex), result, runtime=runtime)
