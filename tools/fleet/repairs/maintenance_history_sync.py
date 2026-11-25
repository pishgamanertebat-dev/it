"""Preserve only journal-proven historical owner Notes during inbound Sync."""
from contextlib import closing, ExitStack
from io import BytesIO
import json
from pathlib import Path
import sqlite3

import openpyxl

from . import entry_dates, maintenance_identity as identity, maintenance_service as service
from .entry_service import EntryError
from .maintenance_baseline import sheet_state, states
from .maintenance_history_scope import OPERATIONS, NEW_OPERATIONS, OWNER, REPORTER
from .maintenance_report import apply_records, verify_operation


def receipts(db, source):
    notes = {}
    auto = db.execute("SELECT operation FROM edits WHERE source=? COLLATE NOCASE AND actor=? AND status='succeeded' "
                      "AND json_extract(request,'$.confirmation_mode')='auto_validated'",(str(source),REPORTER))
    auto_ops = {row[0] for row in auto}
    for operation in (*OPERATIONS, *NEW_OPERATIONS, *auto_ops):
        row = db.execute("SELECT actor,source,request,result FROM edits WHERE operation=? AND status='succeeded'",
                         (operation,)).fetchone()
        if row is None or Path(row[1]) != source:
            continue
        request, result = json.loads(row[2]), json.loads(row[3])
        if operation not in auto_ops and (row[0] != OWNER or not request.get('owner_approval')):
            raise EntryError('HISTORICAL_WRITER_RECEIPT_AUTHORITY_CHANGED')
        verify_operation(request, result)
        notes[operation] = result['row_receipts']
    return notes


def inventory(book, batches):
    known = {note['id']: note for notes in batches.values() for note in notes}
    current = identity.inventory(book, preserve_notes=True)
    if current != known or bool(known) != identity.historical(book):
        raise EntryError('HISTORICAL_NOTES_JOURNAL_MISMATCH')
    return current


def open_readonly(runtime):
    return sqlite3.connect((runtime/'audit.sqlite3').resolve().as_uri()+'?mode=ro', uri=True, timeout=5)


def writer_codec(source, runtime):
    """Reuse the preserving OOXML Writer after the scoped marker appears."""
    source, runtime = Path(source).resolve(), Path(runtime)

    def read(data):
        with closing(open_readonly(runtime)) as db:
            expected = receipts(db, source)
        if not expected:
            book = openpyxl.load_workbook(BytesIO(data))
            try:
                if identity.historical(book):
                    raise EntryError('HISTORICAL_NOTES_JOURNAL_MISMATCH')
                if identity.active(book):
                    with closing(identity.load(data)):
                        pass
                return book
            except BaseException:
                book.close()
                raise
        book = identity.load(data, preserve=True)
        try:
            inventory(book, expected)
            return book
        except BaseException:
            book.close()
            raise

    def dump(book):
        if identity.historical(book):
            return identity.dump(book)
        destination = BytesIO()
        book.save(destination)
        return destination.getvalue()

    return dict(workbook_reader=read, workbook_dumper=dump)


def check_duplicate(book, record, *, allowed_rows=()):
    sheet = book[record['sheet']]
    header, columns = service.record_columns(sheet)
    normalized = lambda value: ''.join(c for c in service.key(value) if c.isalnum())
    description = normalized(record['description'])
    fragments = [normalized(line) for line in record['description'].splitlines()]
    for row in range(header+1, sheet.max_row+1):
        if row in allowed_rows:
            continue
        try:
            date = entry_dates.formatted(entry_dates.jalali_date(sheet.cell(row, columns['date']).value))
        except (TypeError, ValueError):
            continue
        if date != record['date']:
            continue
        previous = normalized(sheet.cell(row, columns['description']).value)
        if previous and (description == previous or (len(description) >= 15 and description in previous)
                         or (len(previous) >= 15 and previous in description)
                         or any(len(part) >= 15 and part in previous for part in fragments)):
            raise EntryError(f"HISTORICAL_DUPLICATE_REVIEW_REQUIRED: {sheet.title}!{row}")


def check_acknowledged_duplicates(book):
    for sheet in book:
        notes = [c for c in sheet._cells.values() if c.comment and c.comment.text.startswith(identity.PREFIX)]
        if not notes:
            continue
        _, columns = service.record_columns(sheet)
        for anchor in notes:
            check_duplicate(book, dict(sheet=sheet.title, date=anchor.value,
                                       description=sheet.cell(anchor.row, columns['description']).value),
                            allowed_rows=(anchor.row,))


def append_batches(book, batches, expected, *, incoming=False):
    positions = {}
    for operation, saved in batches:
        records = [item['record'] for item in saved]
        notes = expected.get(operation)
        if notes and incoming:
            for record in records:
                check_duplicate(book, record)
        saved = apply_records(book, records, operation=operation)
        if notes:
            identity.enable(book, historical_scope=True)
            actual = [identity.attach(book, row, operation, group) for group, row in enumerate(saved)]
            if actual != notes:
                raise EntryError('HISTORICAL_SYNC_NOTE_REPLAY_CHANGED')
        positions[operation] = saved
    return positions


def reconcile(base_data, local_data, incoming_data, batches, expected):
    """Rebuild the complete local branch before applying it to a new mine snapshot."""
    with ExitStack() as stack:
        base, local, mine = [stack.enter_context(closing(identity.load(data, preserve=True)))
                             for data in (base_data, local_data, incoming_data)]
        all_notes = inventory(local, expected)
        acknowledged = identity.inventory(base, preserve_notes=True)
        if (base.sheetnames != local.sheetnames or any(all_notes.get(k) != v for k, v in acknowledged.items())
                or identity.inventory(mine, preserve_notes=True) != acknowledged
                or identity.historical(base) != identity.historical(mine)):
            raise EntryError('HISTORICAL_SYNC_INCOMING_NOTES_UNPROVEN')
        if acknowledged:
            check_acknowledged_duplicates(mine)
        changed = [s.title for s in mine if s.title not in base.sheetnames or sheet_state(base[s.title]) != sheet_state(s)]
        append_batches(base, batches, expected)
        inventory(base, expected)
        if states(base) != states(local) or dict(base.defined_names) != dict(local.defined_names):
            raise EntryError('MAINTENANCE_SYNC_REPORT_REPLAY_NOT_EXACT: historical Notes')
        positions = append_batches(mine, batches, expected, incoming=True)
        inventory(mine, expected)
        candidate = identity.dump(mine) if batches else incoming_data
        return candidate, changed, positions
