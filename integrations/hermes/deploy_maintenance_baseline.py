"""Hash-pinned owner baseline deployment; workbook inputs are never edited here."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import importlib.util
import argparse
import ast
import difflib
import io
import json
from pathlib import Path
import os
import sqlite3
import uuid
import zipfile

import openpyxl

from tools.fleet.repairs.entry_service import writer_lock
from tools.fleet.repairs.maintenance_service import RUNTIME
from tools.fleet.repairs.maintenance_sync import digest
from tools.fleet.repairs.maintenance_baseline import baseline_reader

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'runtime/maintenance-baseline-20261107'
HOME = Path('C:/Users/win-10/AppData/Local/hermes')
LIVE = HOME / 'scripts/mine_file_sync.py'
MINE = Path(r'\\192.168.20.251\share\تعمیرات 1405.xlsx')
LOCAL = Path('E:/Function/تعمیرات 1405.xlsx')
APPROVED = '841da662ce91f2973cef091a922a7caa74ee82c62502a9c2aae8f1c1e6e20e13'
DECISION = ('Owner explicitly accepts the mine and server workbook content observed '
            'on 2026-11-07 around 08:03 Asia/Tehran as the final correct maintenance '
            'state through this point. Preserve historical audit evidence, including '
            'the unverified succeeded operation; do not restore or replay old entries. '
            'This acceptance does not prove presence of any historical operation.')


@contextmanager
def deployment_locks():
    spec = importlib.util.spec_from_file_location('baseline_live_sync', LIVE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    held = []
    try:
        for name in ('mine-file-sync.lock', 'mine-file-sync-worker.lock'):
            lock = module.acquire_lock(HOME / 'state' / name)
            if lock is None:
                raise RuntimeError('BLOCKED: active Sync; retry after normal completion')
            held.append(lock)
        lifecycle = json.loads((HOME / 'state/mine-file-sync-lifecycle.json').read_text('utf8'))
        if module.prior_worker_status(lifecycle) not in ('terminated', 'identity_replaced'):
            raise RuntimeError('BLOCKED: previous worker termination unverified')
        with writer_lock(RUNTIME):
            yield lifecycle
    finally:
        for lock in reversed(held):
            module.release_lock(lock)


def inspect(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('BLOCKED: damaged XLSX archive')
    book = openpyxl.load_workbook(io.BytesIO(data))
    try:
        from tools.fleet.repairs.maintenance_service import layout
        return [dict(name=s.title, rows=s.max_row, columns=s.max_column,
                     header=layout(s), merges=list(map(str, s.merged_cells.ranges)),
                     print_area=str(s.print_area)) for s in book]
    finally:
        book.close()


def preflight():
    OUT.mkdir(parents=True, exist_ok=True)
    before = OUT / 'before'
    before.mkdir(exist_ok=True)
    if (OUT / 'preflight.json').exists():
        raise RuntimeError('Preflight already exists; preserve the original evidence')
    with deployment_locks() as lifecycle, baseline_reader(MINE) as mine, baseline_reader(LOCAL) as local:
        files = []
        for label, path, stream in [('mine', MINE, mine), ('server', LOCAL, local)]:
            stat = path.stat()
            data = stream.read()
            if digest(data) != APPROVED:
                raise RuntimeError('BLOCKED: owner-approved hash changed: ' + str(path))
            backup = before / (label + '.xlsx')
            with backup.open('xb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            if digest(backup.read_bytes()) != APPROVED:
                raise RuntimeError('BLOCKED: invalid independent backup')
            files.append(dict(path=str(path), sha256=digest(data), size=len(data),
                              mtime_ns=stat.st_mtime_ns,
                              modified_utc=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                              backup=str(backup), sheets=inspect(data)))
        journal = RUNTIME / 'audit.sqlite3'
        with closing(sqlite3.connect(journal.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('BLOCKED: invalid Journal')
            operations = db.execute('SELECT rowid,* FROM edits').fetchall()
            if any(row[-3] not in ('succeeded', 'not_applied') for row in operations):
                raise RuntimeError('BLOCKED: unresolved Writer operation')
            with closing(sqlite3.connect(before / 'audit.sqlite3')) as dest:
                db.backup(dest)
        for name in ('maintenance_sync.py', 'maintenance_service.py'):
            (before / name).write_bytes((ROOT / 'tools/fleet/repairs' / name).read_bytes())
        (before / 'mine_file_sync.py').write_bytes(LIVE.read_bytes())
        manifest = dict(checked_at=datetime.now(timezone.utc).isoformat(), files=files,
                        equal=True, approved_hash=APPROVED, decision=DECISION,
                        lifecycle=lifecycle, operations=operations,
                        journal_backup_sha256=digest((before / 'audit.sqlite3').read_bytes()),
                        sync_sha256=digest(LIVE.read_bytes()), workbooks_written=0)
        (OUT / 'preflight.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf8')
        print(json.dumps(dict(equal=True, hash=APPROVED, sheets=[len(f['sheets']) for f in files],
                              operations=len(operations), backup=str(before))))


def prepare():
    original = (OUT / 'before/mine_file_sync.py').read_text('utf8')
    code = original
    replacements = [
        ("             stdout.decode('utf8', errors='replace').strip())\n",
         "             stdout.decode('utf8', errors='replace').strip())\n"
         "    return json.loads(stdout.decode('utf8'))\n"),
        ('    temporary = destination.parent / (".mine-sync-" + uuid.uuid4().hex + ".partial")\n',
         '    publication = None\n'
         '    temporary = destination.parent / (".mine-sync-" + uuid.uuid4().hex + ".partial")\n'),
        ('                publish_fault_report(temporary, destination, pulse=pulse,\n'
         '                                     module="tools.fleet.repairs.maintenance_sync")',
         '                publication = publish_fault_report(temporary, destination, pulse=pulse,\n'
         '                                                   module="tools.fleet.repairs.maintenance_sync")'),
        ('\n\ndef empty_result():', '\n    return publication\n\n\ndef empty_result():'),
        ('                atomic_copy(src, dst, before, pulse)\n            result[kind] += 1',
         '                publication = atomic_copy(src, dst, before, pulse)\n'
         '                if dst.resolve() == MAINTENANCE_REPORT.resolve() and isinstance(publication, dict):\n'
         '                    result["maintenance_guard"] = publication\n'
         '                    if publication.get("status") in ("unchanged", "identical_noop"):\n'
         '                        kind = "skipped"\n'
         '            result[kind] += 1'),
    ]
    for old, new in replacements:
        if code.count(old) != 1:
            raise RuntimeError('Unexpected Sync source: ' + old)
        code = code.replace(old, new, 1)
    ast.parse(code)
    (OUT / 'stage').mkdir(exist_ok=True)
    (OUT / 'stage/mine_file_sync.py').write_text(code, 'utf8')
    (ROOT / 'integrations/hermes/patches/mine-maintenance-baseline-20261107.patch').write_text(
        ''.join(difflib.unified_diff(original.splitlines(True), code.splitlines(True),
                                    fromfile='scripts/mine_file_sync.py', tofile='scripts/mine_file_sync.py')), 'utf8')
    print('Prepared runtime status propagation; no live script installation')


def install():
    from tools.fleet.repairs.maintenance_baseline import initialize_locked
    evidence = json.loads((OUT / 'acceptance.json').read_text('utf-8-sig'))
    if not evidence['passed'] or not evidence['native_excel_passed']:
        raise RuntimeError('BLOCKED: all isolated checks and native Excel required')
    for path, expected in evidence['tested_code'].items():
        if digest(Path(path).read_bytes()) != expected:
            raise RuntimeError('BLOCKED: code changed after testing: ' + path)
    real = json.loads((OUT / 'real-copy.json').read_text('utf8'))
    if not real['passed']:
        raise RuntimeError('BLOCKED: real Workbook/Journal rehearsal failed')
    for path, expected in real['tested_code'].items():
        if digest(Path(path).read_bytes()) != expected:
            raise RuntimeError('BLOCKED: real-copy code changed after testing: ' + path)
    native = json.loads((OUT / 'native-excel.json').read_text('utf-8-sig'))
    if len(native) != 4 or any(not row['normal_open'] or row['repair_mode'] or not row['readonly']
                               or digest(Path(row['path']).read_bytes()).upper() != row['sha256']
                               for row in native):
        raise RuntimeError('BLOCKED: native Excel evidence invalid or files changed')
    preflight = json.loads((OUT / 'preflight.json').read_text('utf8'))
    with deployment_locks() as lifecycle, baseline_reader(MINE) as mine, baseline_reader(LOCAL) as local:
        # Re-pin both inputs immediately before activation. No source is preferred.
        if digest(mine.read()) != APPROVED or digest(local.read()) != APPROVED:
            raise RuntimeError('BLOCKED: approved workbook changed since preflight')
        staged = OUT / 'stage/mine_file_sync.py'
        if digest(LIVE.read_bytes()) not in (preflight['sync_sha256'], digest(staged.read_bytes())):
            raise RuntimeError('BLOCKED: installed Sync changed since preflight')
        if not (OUT / 'audit-before-activation.sqlite3').exists():
            with closing(sqlite3.connect((RUNTIME / 'audit.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)) as src, \
                 closing(sqlite3.connect(OUT / 'audit-before-activation.sqlite3')) as dest:
                src.backup(dest)
        result = initialize_locked(MINE, expected_hash=APPROVED, decision=DECISION,
                                   approved_by='project-owner')
        temporary = LIVE.with_name('.baseline-install-' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(staged.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, LIVE)
        finally:
            temporary.unlink(missing_ok=True)
        if digest(LIVE.read_bytes()) != digest(staged.read_bytes()):
            raise RuntimeError('Installation hash check failed')
        result = dict(baseline=result, sync_sha256=digest(LIVE.read_bytes()),
                      old_worker_terminated=True, lifecycle=lifecycle,
                      workbooks_written=0, gateway_restart=False,
                      installed_at=datetime.now(timezone.utc).isoformat())
        (OUT / 'installed.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf8')
        print(json.dumps(dict(status='installed', baseline=result['baseline']['event_id'],
                              hash=APPROVED, workbooks_written=0, gateway_restart=False)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='preflight', choices=('preflight', 'prepare', 'install'))
    args = parser.parse_args()
    globals()[args.action]()
