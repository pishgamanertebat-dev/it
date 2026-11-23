"""Apply the two frozen historical reports after authenticated owner approval."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from . import maintenance_history_scope as scope, maintenance_history_sync as inbound
from . import maintenance_identity as identity, maintenance_service as service
from .entry_service import EntryError, EntryAuthorizationDenied, commit_workbook, source_reader, writer_lock
from .maintenance_baseline import snapshot
from .maintenance_history_bridge import approval_for_event
from .maintenance_permission import approved_identity
from .maintenance_report import KIND, delivery_review, verify_operation
from .maintenance_report_flow import _candidate_cells, _check_item, _complete, _prove_source
from .maintenance_report_parse import parse
from .maintenance_report_policy import CONFIG, enabled, partial_enabled, permission
from .maintenance_report_store import load_workflow, open_workflows, save_workflow


GATEWAY_HOME = Path(r'C:\Users\win-10\AppData\Local\hermes')


def bound_settings(frozen, config, entry_config, runtime):
    entry = json.loads(Path(entry_config).read_text('utf8'))
    target = Path(entry['source']).resolve(strict=True)
    destinations = [str(target), str(runtime/'audit.sqlite3'), str(runtime/'backups')]
    if (str(target) != frozen['destination'] or destinations != frozen['authorized_data_destinations']
            or (runtime/'audit.sqlite3').resolve() != runtime/'audit.sqlite3'
            or (runtime/'backups').resolve() != runtime/'backups'
            or entry.get('sync_policy') != 'upstream_copy'
            or entry['outbound_publication']['enabled'] is not False
            or not enabled(config) or not (runtime/'audit.sqlite3').is_file()):
        raise EntryAuthorizationDenied('HISTORICAL_DESTINATION_OR_POLICY_CHANGED')
    settings = permission(scope.REPORTER, scope.REPORTER, config=config, entry_config=entry_config)
    if not partial_enabled(settings) or settings['source'] != str(target):
        raise EntryAuthorizationDenied('HISTORICAL_REPORTER_PERMISSION_CHANGED')
    return target, settings


def backup(source, runtime):
    """Called under the standard Writer lock before writing the owner intent."""
    parent = runtime/'backups'
    parent.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix='owner-historical-', dir=parent))
    with source_reader(source) as reader, closing(inbound.open_readonly(runtime)) as live:
        data = reader.read()
        with (path/'Function.xlsx').open('xb') as copy:
            copy.write(data)
            copy.flush()
            os.fsync(copy.fileno())
        with closing(sqlite3.connect(path/'audit.sqlite3')) as copy:
            live.backup(copy)
            if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise EntryError('HISTORICAL_BACKUP_JOURNAL_CORRUPT')
        excel_hash = scope.digest(data)
        if scope.digest((path/'Function.xlsx').read_bytes()) != excel_hash:
            raise EntryError('HISTORICAL_BACKUP_EXCEL_CHANGED')
        details = dict(created=datetime.now(timezone.utc).isoformat(), scope_sha256=scope.SCOPE_SHA256,
                       source=str(source), workbook_sha256=excel_hash,
                       journal_sha256=scope.digest((path/'audit.sqlite3').read_bytes()))
        (path/'hashes.json').write_text(scope.encoded(details), encoding='utf8')
    return path


def original(db, spec, settings, approval):
    row = db.execute('''SELECT * FROM maintenance_report_messages
                        WHERE operation=? AND source_id=? AND actor=? AND chat=?''',
                     (spec['report_id'], spec['source_id'], scope.REPORTER, scope.REPORTER)).fetchall()
    if len(row) != 1 or row[0]['status'] != 'needs_review':
        raise EntryError('HISTORICAL_ORIGINAL_RECEIPT_CHANGED')
    receipt = row[0]
    raw = receipt['raw_text']
    if (receipt['raw_hash'] != spec['raw_sha256'] or scope.digest(raw.encode()) != spec['raw_sha256']
            or settings['historical_recovery'].get(spec['source_id']) !=
            {'date': spec['date'], 'raw_sha256': spec['raw_sha256']}):
        raise EntryError('HISTORICAL_ORIGINAL_HASH_CHANGED')
    parsed = parse(raw, current=spec['date'], independent=True)
    if parsed['date'] != spec['date']:
        raise EntryError('HISTORICAL_ORIGINAL_DATE_CHANGED')
    indexed = {item['number']: item for item in spec['approved_items'] + spec['excluded_items']}
    if set(indexed) != set(range(1, len(parsed['items'])+1)):
        raise EntryError('HISTORICAL_ORIGINAL_ITEMS_CHANGED')
    items = []
    for number, item in enumerate(parsed['items'], 1):
        chosen = indexed[number]
        if item['raw'] != chosen['raw'] or item['span'] != chosen['span']:
            raise EntryError('HISTORICAL_ORIGINAL_ITEM_SPAN_CHANGED')
        pending = 'record' not in chosen
        if pending and item['code'] != '600':
            raise EntryError('HISTORICAL_PENDING_LOADER_CHANGED')
        items.append(dict(id=chosen['id'], number=number, parsed=item, status=chosen.get('status', 'VALID'),
                          reason=chosen.get('reason', ''), correction=None))
    request = dict(_kind=KIND, operation=spec['writer_operation'], report_id=spec['report_id'], source_id=spec['source_id'],
                   origin_actor=scope.REPORTER, date=spec['date'], recorder=settings['recorder'], raw_text=raw,
                   alias_version=parsed['alias_version'], owner_approval=approval, approved_items=scope.items(spec))
    return dict(id=spec['report_id'], source_id=spec['source_id'], date=spec['date'], raw=raw, settings=settings,
                items=items, view=None, attempt=request,
                historical_recovery=settings['historical_recovery'][spec['source_id']]), request


def no_other_commit(db, request):
    if db.execute('''SELECT 1 FROM edits
        WHERE operation<>? AND (operation=? OR json_extract(request,'$.source_id')=?
             OR json_extract(request,'$.raw_text')=?) LIMIT 1''',
        (request['operation'], request['report_id'], request['source_id'], request['raw_text'])).fetchone():
        raise EntryError('HISTORICAL_OTHER_WRITER_OPERATION_FOUND')


def retain_approval(db, approval):
    with db:
        db.execute('''CREATE TABLE IF NOT EXISTS maintenance_historical_owner_approval
                      (scope_sha256 TEXT PRIMARY KEY, approval TEXT NOT NULL)''')
        db.execute('INSERT OR IGNORE INTO maintenance_historical_owner_approval VALUES (?,?)',
                   (scope.SCOPE_SHA256, scope.encoded(approval)))
    result = json.loads(db.execute('SELECT approval FROM maintenance_historical_owner_approval WHERE scope_sha256=?',
                                  (scope.SCOPE_SHA256,)).fetchone()[0])
    scope.verify_approval(result)
    return result


def confirmed(db, request):
    return db.execute("SELECT * FROM edits WHERE operation=? AND status='succeeded'", (request['operation'],)).fetchone()


def verify_candidate(data, result):
    _candidate_cells(data, result, require_identity=True)
    with closing(identity.load(data, preserve=True)) as book:
        notes = identity.inventory(book, preserve_notes=True)
        if any(notes.get(item['id']) != item for item in result['row_receipts']):
            raise EntryError('HISTORICAL_WRITER_ROW_NOTE_READBACK_FAILED')


def verify_current(source, runtime, request, result):
    with writer_lock(runtime), source_reader(source) as file, closing(inbound.open_readonly(runtime)) as db:
        db.row_factory = sqlite3.Row
        data = file.read()
        digest = scope.digest(data)
        row = confirmed(db, request)
        if row is None or json.loads(row['request']) != request or json.loads(row['result']) != result:
            raise EntryError('HISTORICAL_WRITER_RECEIPT_MISSING')
        _prove_source(digest, runtime, source)
        service.verify_replay(db, source, digest, row)
        verify_operation(request, result)
        verify_candidate(snapshot(runtime, row['after_hash']), result)
        with closing(identity.load(data, preserve=True)) as book:
            inbound.inventory(book, inbound.receipts(db, source))


def commit_one(source, runtime, source_settings, proposed, request, authorized, ledger):
    backed_up = False

    def authorized_writer():
        nonlocal backed_up
        authorized()
        if not backed_up:
            with closing(inbound.open_readonly(runtime)) as db:
                other = db.execute("""SELECT operation FROM edits WHERE source=? COLLATE NOCASE
                                      AND status='prepared' AND operation NOT IN (?,?) LIMIT 1""",
                                   (str(source), *scope.OPERATIONS)).fetchone()
            if other is not None:
                raise EntryError('HISTORICAL_FOREIGN_PREPARED_OPERATION')
            backup(source, runtime)
            backed_up = True

    def read(data):
        _prove_source(scope.digest(data), runtime, source)
        book = identity.load(data, preserve=True)
        try:
            with closing(inbound.open_readonly(runtime)) as db:
                no_other_commit(db, request)
                inbound.inventory(book, inbound.receipts(db, source))
            return book
        except BaseException:
            book.close()
            raise

    def mutate(book):
        identity.enable(book, historical_scope=True)
        saved = []
        notes = []
        for group, item in enumerate(request['approved_items']):
            parsed = proposed['items'][item['number']-1]['parsed']
            record = _check_item(book, parsed, request['date'], source_settings)
            if record != item['record']:
                raise EntryError('HISTORICAL_APPROVED_RECORD_CHANGED')
            inbound.check_duplicate(book, record)
            row = dict(service.append_record(book, record, recorder=record['recorder'], independent=True), record=record)
            saved.append(row)
            notes.append(identity.attach(book, row, request['operation'], group))
        result = dict(operation=request['operation'], date=request['date'], recorder=source_settings['recorder'],
                      records=saved, item_ids=[item['id'] for item in request['approved_items']], rejected_items={},
                      owner_approval=request['owner_approval'], row_receipts=notes,
                      delivery=delivery_review(request['date'], ledger))
        verify_operation(request, result)
        return result

    def check_date():
        if request['date'] != proposed['date']:
            raise EntryError('HISTORICAL_ORIGINAL_DATE_CHANGED')

    return commit_workbook(scope.OWNER, request, source_settings, mutate, runtime=runtime,
                           command='ثبت نهایی گزارش تاریخی', authorization_check=authorized_writer, date_check=check_date,
                           replay_check=service.verify_replay, verify_candidate=verify_candidate,
                           workbook_reader=read, workbook_dumper=identity.dump)


def execute(event, *, owner_allowed, config=CONFIG, entry_config=service.CONFIG, runtime=service.RUNTIME, ledger=None):
    """Run an already authenticated in-process event."""
    try:
        approval = approval_for_event(event, owner_allowed)
    except PermissionError as exc:
        raise EntryAuthorizationDenied(str(exc)) from exc
    return execute_approved(approval, owner_allowed=owner_allowed, config=config, entry_config=entry_config,
                            runtime=runtime, ledger=ledger)


def execute_approved(approval, *, owner_allowed, config=CONFIG, entry_config=service.CONFIG, runtime=service.RUNTIME,
                     ledger=None):
    scope.verify_approval(approval)
    frozen = scope.load()
    runtime = Path(runtime).resolve()
    source, settings = bound_settings(frozen, config, entry_config, runtime)
    ledger = ledger or service.ROOT/'runtime/scheduler/runs.sqlite3'

    def authorized():
        if (not owner_allowed() or scope.load() != frozen
                or bound_settings(frozen, config, entry_config, runtime) != (source, settings)):
            raise EntryAuthorizationDenied('HISTORICAL_OWNER_OR_SCOPE_REVOKED')
        approved_identity(scope.OWNER)

    results = {}
    authorized()
    with writer_lock(runtime/'maintenance-report-flow'):
        authorized()
        with closing(inbound.open_readonly(runtime)) as existing:
            existing.row_factory = sqlite3.Row
            prepared = [original(existing, spec, settings, approval) for spec in frozen['reports']]
            for _, request in prepared:
                if confirmed(existing, request) is None:
                    no_other_commit(existing, request)
            all_done = all(confirmed(existing, request) is not None for _, request in prepared)
        if not all_done:
            with writer_lock(runtime):
                backup(source, runtime)
        with closing(open_workflows(runtime)) as db:
            approval = retain_approval(db, approval)
            for spec in frozen['reports']:
                authorized()
                proposed, request = original(db, spec, settings, approval)
                old = load_workflow(db, scope.REPORTER, scope.REPORTER, spec['report_id'])
                succeeded = confirmed(db, request)
                if old is not None and old.get('attempt') != request and succeeded is None:
                    raise EntryError('HISTORICAL_OTHER_WORKFLOW_EXISTS')
                if succeeded is None:
                    save_workflow(db, scope.REPORTER, scope.REPORTER, proposed)
                    result = commit_one(source, runtime, settings, proposed, request, authorized, ledger)
                else:
                    result = json.loads(succeeded['result'])
                verify_current(source, runtime, request, result)
                if old is None or old.get('attempt') == request:
                    _complete(proposed, result)
                    save_workflow(db, scope.REPORTER, scope.REPORTER, proposed)
                results[spec['source_id']] = result
    return results


def gateway_pipe_pid():
    """Verify that the approval comes through a pipe opened by the running Gateway.

    Windows' venv redirector can be the OS parent of the Python worker. The
    inherited pipe identifies the actual Gateway on both ends.
    """
    import ctypes
    from ctypes import wintypes
    import msvcrt

    identity_file = json.loads((GATEWAY_HOME/'gateway.pid').read_text('utf8'))
    status = json.loads((GATEWAY_HOME/'gateway_state.json').read_text('utf8'))
    pid = identity_file['pid']
    started = identity_file['start_time']
    if (type(pid) is not int or pid <= 0 or not started or identity_file['kind'] != 'hermes-gateway'
            or status['pid'] != pid or status['start_time'] != started or status['gateway_state'] != 'running'):
        raise EntryAuthorizationDenied('HISTORICAL_LIVE_GATEWAY_REQUIRED')

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pipe = msvcrt.get_osfhandle(sys.stdin.fileno())
    for api in (kernel.GetNamedPipeClientProcessId, kernel.GetNamedPipeServerProcessId):
        api.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
        api.restype = wintypes.BOOL
        found = wintypes.ULONG()
        if not api(pipe, ctypes.byref(found)) or found.value != pid:
            raise EntryAuthorizationDenied('HISTORICAL_LIVE_GATEWAY_PIPE_REQUIRED')

    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    process = kernel.OpenProcess(0x1000, False, pid)
    if not process:
        raise EntryAuthorizationDenied('HISTORICAL_LIVE_GATEWAY_REQUIRED')
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(process, *(ctypes.byref(t) for t in times)):
            raise EntryAuthorizationDenied('HISTORICAL_LIVE_GATEWAY_REQUIRED')
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        if abs(round((ticks - 116444736000000000) / 100000) - started) > 1:
            raise EntryAuthorizationDenied('HISTORICAL_LIVE_GATEWAY_CHANGED')
    finally:
        kernel.CloseHandle(process)
    return pid


def main():
    gateway_pid = gateway_pipe_pid()
    request = json.loads(sys.stdin.readline(4096))
    if not isinstance(request, dict) or set(request) != {'approval'}:
        raise EntryAuthorizationDenied('HISTORICAL_OWNER_REQUEST_INVALID')

    def allowed():
        if gateway_pipe_pid() != gateway_pid:
            return False
        print('{"authorize":true}', flush=True)
        reply = json.loads(sys.stdin.readline(256))
        return isinstance(reply, dict) and set(reply) == {'allowed'} and reply['allowed'] is True

    results = execute_approved(request['approval'], owner_allowed=allowed)
    print(json.dumps({'receipts': {source: row['operation'] for source, row in results.items()}}), flush=True)


if __name__ == '__main__':
    main()
