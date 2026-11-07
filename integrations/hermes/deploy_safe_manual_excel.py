"""Prepare/install only the fail-closed maintenance Sync hook, under existing locks."""
from contextlib import closing
import argparse
import ast
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import uuid

from tools.fleet.repairs.entry_service import source_reader, writer_lock
from tools.fleet.repairs.maintenance_service import CONFIG, RUNTIME

ROOT = Path(__file__).resolve().parents[2]
HOME = Path('C:/Users/win-10/AppData/Local/hermes')
OUT = ROOT / 'runtime/safe-manual-excel-20261107'
LIVE = HOME / 'scripts/mine_file_sync.py'
STAGED = OUT / 'stage/mine_file_sync.py'
PATCH = ROOT / 'integrations/hermes/patches/mine-maintenance-guard-20261107.patch'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_sync(path):
    spec = importlib.util.spec_from_file_location('safe_manual_sync', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guarded_source(original):
    source = original
    replacements = [
        ('FAULT_REPORT = Path("E:/Function/گزارش روزانه رانندگان2.xlsx")',
         'FAULT_REPORT = Path("E:/Function/گزارش روزانه رانندگان2.xlsx")\n'
         'MAINTENANCE_REPORT = Path("E:/Function/تعمیرات 1405.xlsx")'),
        ('def publish_fault_report(staged, destination, pulse=lambda **kw: None):',
         'def publish_fault_report(staged, destination, pulse=lambda **kw: None,\n'
         '                         module="tools.fleet.repairs.sync_service"):'),
        ('\');runpy.run_module("tools.fleet.repairs.sync_service",run_name="__main__")\')',
         '\');runpy.run_module("\' + module + \'",run_name="__main__")\')'),
        ("LOG.info('fault_merge %s', stdout.decode('utf8', errors='replace').strip())",
         "LOG.info('%s %s', 'fault_merge' if module.endswith('.sync_service') else 'maintenance_guard',\n"
         "             stdout.decode('utf8', errors='replace').strip())"),
        ('            else:\n                os.replace(temporary, destination)',
         '            elif destination.resolve() == MAINTENANCE_REPORT.resolve():\n'
         '                publish_fault_report(temporary, destination, pulse=pulse,\n'
         '                                     module="tools.fleet.repairs.maintenance_sync")\n'
         '            else:\n                os.replace(temporary, destination)'),
        ('and dst.resolve() != FAULT_REPORT.resolve()):',
         'and dst.resolve() not in {FAULT_REPORT.resolve(), MAINTENANCE_REPORT.resolve()}):'),
        ('        except OSError as exc:\n            if getattr(exc, "winerror", None) in NETWORK_ERRORS:',
         '        except OSError as exc:\n'
         '            if dst.resolve() == MAINTENANCE_REPORT.resolve():\n'
         '                result["maintenance_guard"] = {"status": "blocked", "error": str(exc)}\n'
         '                LOG.error("maintenance_sync_blocked relative_path=%s exception=%s", relative, exc)\n'
         '            if getattr(exc, "winerror", None) in NETWORK_ERRORS:'),
    ]
    for before, after in replacements:
        if source.count(before) != 1:
            raise RuntimeError('Unexpected Sync code; review instead of patching: ' + before)
        source = source.replace(before, after, 1)
    ast.parse(source)
    return source


def prepare():
    original = (OUT / 'before/hermes/scripts/mine_file_sync.py').read_text(encoding='utf8')
    source = guarded_source(original)
    STAGED.parent.mkdir(exist_ok=True)
    STAGED.write_text(source, encoding='utf8')
    PATCH.write_text(''.join(difflib.unified_diff(original.splitlines(True), source.splitlines(True),
                     fromfile='scripts/mine_file_sync.py', tofile='scripts/mine_file_sync.py')), encoding='utf8')
    print('Prepared maintenance guard; live Sync unchanged')


def install():
    evidence = json.loads((OUT / 'acceptance.json').read_text(encoding='utf8'))
    if not evidence.get('safe_guard_passed') or not evidence.get('native_excel_passed'):
        raise RuntimeError('Isolated safety and native Excel validation are required')
    for name, expected in evidence['tested_code'].items():
        if digest(Path(name).read_bytes()) != expected:
            raise RuntimeError('Code changed after tests: ' + name)
    before = json.loads((OUT / 'before-manifest.json').read_text(encoding='utf8'))
    if digest(LIVE.read_bytes()) != before[str(LIVE)]['sha256']:
        raise RuntimeError('Live Sync changed; review again')
    module = load_sync(LIVE)
    held = []
    try:
        for name in ('mine-file-sync.lock', 'mine-file-sync-worker.lock'):
            lock = module.acquire_lock(HOME / 'state' / name)
            if lock is None:
                raise RuntimeError('A Sync supervisor/worker is active; retry after it exits')
            held.append(lock)
        record = json.loads((HOME / 'state/mine-file-sync-lifecycle.json').read_text(encoding='utf8'))
        if module.prior_worker_status(record) not in ('terminated', 'identity_replaced'):
            raise RuntimeError('Previous Sync worker termination is unverified')
        with writer_lock(RUNTIME):
            source = Path(json.loads(CONFIG.read_text(encoding='utf8'))['source'])
            with source_reader(source) as stream:
                current = digest(stream.read())
                # Backup current journal; never restore or mutate old edit rows.
                with closing(sqlite3.connect((RUNTIME / 'audit.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)) as src, \
                     closing(sqlite3.connect(OUT / 'maintenance-entry-install.sqlite3')) as dst:
                    src.backup(dst)
                backup = OUT / 'mine_file_sync.before-install.py'
                backup.write_bytes(LIVE.read_bytes())
                if digest(backup.read_bytes()) != before[str(LIVE)]['sha256']:
                    raise RuntimeError('Sync changed during installation')
                temporary = LIVE.with_name('.maintenance-install-' + uuid.uuid4().hex + '.tmp')
                try:
                    with temporary.open('xb') as stream:
                        stream.write(STAGED.read_bytes())
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, LIVE)
                finally:
                    temporary.unlink(missing_ok=True)
                if digest(source.read_bytes()) != current or digest(LIVE.read_bytes()) != digest(STAGED.read_bytes()):
                    raise RuntimeError('Installation verification failed')
                result = dict(installed=True, mode='fail_closed_only', sync_sha256=digest(LIVE.read_bytes()),
                              workbook_hash=current, gateway_restart=False, workbooks_written=0,
                              permissions_changed=False, old_worker_terminated=True, lock_names=2)
                (OUT / 'installed.json').write_text(json.dumps(result, indent=2), encoding='utf8')
                print(json.dumps(result))
    finally:
        for lock in reversed(held):
            module.release_lock(lock)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'install'))
    args = parser.parse_args()
    prepare() if args.action == 'prepare' else install()
