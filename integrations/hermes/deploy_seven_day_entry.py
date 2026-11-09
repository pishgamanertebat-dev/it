"""Install the reviewed manual-entry change, commit its exact scope and drain/restart."""
from contextlib import closing, contextmanager
from datetime import datetime
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo

ROOT = Path('E:/KomatsoAI')
OUT = ROOT/'runtime/hedayati-daily7-20261109'
CANDIDATE = OUT/'candidate'
HOME = Path('C:/Users/win-10/AppData/Local/hermes')
REPORT = 'reports/hermes/hedayati-daily7-20261109.md'
FILES = [
    'settings/maintenance_entry.json',
    'tools/fleet/repairs/entry_bale.py', 'tools/fleet/repairs/entry_dates.py',
    'tools/fleet/repairs/entry_service.py', 'tools/fleet/repairs/maintenance_bale.py',
    'tools/fleet/repairs/maintenance_permission.py', 'tools/fleet/repairs/maintenance_service.py',
    'tools/fleet/repairs/rehearse_seven_day_entry.py', 'tools/fleet/repairs/test_seven_day_entry.py',
    'tools/fleet/repairs/test_entry_service.py', 'tools/fleet/repairs/test_entry_bale.py',
    'tools/fleet/repairs/test_department_entry.py', 'tools/fleet/repairs/test_maintenance_bale.py',
    'tools/fleet/repairs/test_manual_forms_offline.py', 'tools/authorization/test_net.py',
    'integrations/hermes/verify_seven_day_entry.py', 'integrations/hermes/verify_seven_day_excel.ps1',
    'integrations/hermes/deploy_seven_day_entry.py', REPORT,
]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text('utf-8-sig'))


def save(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), 'utf8')


def git(*args):
    result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, encoding='utf8')
    if result.returncode:
        raise RuntimeError('Git command failed: ' + ' '.join(args) + '\n' + result.stderr)
    return result.stdout.strip()


def check():
    manifest = load(OUT/'before-manifest.json')
    acceptance = load(OUT/'tests-final/acceptance.json')
    assert acceptance['passed'] and acceptance['tests_run'] >= 289
    assert acceptance['failures'] == acceptance['errors'] == acceptance['skipped'] == 0
    for name, expected in acceptance['tested_code'].items():
        assert digest(CANDIDATE/name) == expected, f'Tested code changed: {name}'
    assert load(OUT/'real-copy-final/evidence.json')['passed']
    native = load(OUT/'real-copy-final/native-excel.json')
    assert len(native) == 2 and all(row['normal_open'] and row['readonly'] and not row['repair_mode'] for row in native)
    assert all(digest(row['path']).upper() == row['sha256'] for row in native)
    assert git('rev-parse', 'HEAD') == manifest['git_head']
    assert not git('diff', '--name-only') and not git('diff', '--cached', '--name-only')
    old = manifest['sha256']
    changed = {}
    for name in FILES:
        candidate, target = CANDIDATE/name, ROOT/name
        assert candidate.is_file()
        if name.endswith('.py'):
            ast.parse(candidate.read_text('utf8'))
        if name in old:
            assert digest(target) == old[name], f'Live code changed: {name}'
        else:
            assert not target.exists(), f'Unexpected existing target: {name}'
        changed[name] = dict(before=old.get(name), after=digest(candidate))
    expected_changed = set(load(OUT/'candidate-files.json'))
    assert expected_changed <= set(FILES)
    # All Sync, work-order, authorization and menu code outside the explicit list
    # must still be the exact baseline. Installed registry plugins are unchanged.
    for name, expected in old.items():
        if name.startswith(('tools/', 'settings/', 'integrations/')) and name not in FILES:
            assert digest(ROOT/name) == expected, f'Unrelated live change: {name}'
            assert digest(CANDIDATE/name) == expected, f'Unrelated candidate change: {name}'
        normalized = name.replace('\\', '/')
        if 'plugins/komatso-bale-registry' in normalized or normalized.endswith('scripts/mine_file_sync.py'):
            assert digest(name) == expected
    config = load(CANDIDATE/'settings/maintenance_entry.json')
    previous = load(OUT/'before/settings/maintenance_entry.json')
    assert set(config['allowed_users']) == set(previous['allowed_users']) | {'641220453'}
    assert config['source'] == previous['source']
    save('reviewed.json', dict(passed=True, reviewed_tehran=datetime.now(ZoneInfo('Asia/Tehran')).isoformat(),
        files=changed, tests=acceptance['tests_run'], native_excel=True, real_copy=True,
        unchanged_sync_and_work_orders=True, prior_sync_conflict_acknowledged=True))
    return changed


def native():
    os.environ['HERMES_HOME'] = str(HOME)
    core = HOME/'hermes-agent'
    assert Path(sys.prefix).resolve() == (core/'venv').resolve(), 'Gateway lifecycle requires the installed Hermes interpreter'
    sys.path.extend([str(ROOT), str(core), str(ROOT/'.venv/Lib/site-packages')])
    import tools
    for path in (ROOT/'tools', core/'tools'):
        if str(path) not in tools.__path__:
            tools.__path__.append(str(path))
    from gateway.control_socket import identify_gateway, query_gateway_control
    from hermes_cli import gateway_windows
    return identify_gateway, query_gateway_control, gateway_windows


@contextmanager
def deployment_locks():
    spec = importlib.util.spec_from_file_location('daily7_live_sync_lock', HOME/'scripts/mine_file_sync.py')
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    held = []
    try:
        for name in ('mine-file-sync.lock', 'mine-file-sync-worker.lock'):
            lock = sync.acquire_lock(HOME/'state'/name)
            if lock is None:
                raise RuntimeError('Active Sync; retry after its natural completion')
            held.append(lock)
        lifecycle = load(HOME/'state/mine-file-sync-lifecycle.json')
        assert sync.prior_worker_status(lifecycle) in ('terminated', 'identity_replaced')
        from tools.fleet.repairs.entry_service import writer_lock
        with writer_lock(ROOT/'runtime/repairs-entry'), writer_lock(ROOT/'runtime/maintenance-entry'):
            yield lifecycle
    finally:
        for lock in reversed(held):
            sync.release_lock(lock)


def backup_database(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        assert dst.execute('PRAGMA integrity_check').fetchone() == ('ok',)


def busy(value):
    if isinstance(value, dict):
        return value.get('stage') == 'BUSY' or any(busy(item) for item in value.values())
    return isinstance(value, list) and any(busy(item) for item in value)


def install():
    assert not (OUT/'installed.json').exists(), 'Already installed; use verify'
    changed = check()
    identify, query, windows = native()
    identity, status = identify(HOME, timeout=10), query(HOME, 'status', timeout=10)
    assert identity and status['gateway_state'] == 'running' and not status.get('active_agents')
    for name in ('work_order.json', 'repairs_entry.json', 'maintenance_entry.json'):
        path = ROOT/'runtime/bale_ui'/name
        assert not path.exists() or not busy(load(path)), 'Manual form worker is busy'
    save('gateway-before-activation.json', dict(identity=identity, status=status))
    backup = OUT/'activation-backup'
    if backup.exists():
        backup = OUT/('activation-backup-' + uuid.uuid4().hex[:8])
    backup.mkdir()
    stopped = False
    published = []
    try:
        with deployment_locks() as lifecycle:
            from tools.fleet.repairs.entry_service import source_reader
            workbook_hashes = {}
            for kind in ('repairs', 'maintenance'):
                config = load(ROOT/f'settings/{kind}_entry.json')
                source = Path(config['source'])
                with source_reader(source) as stream:
                    data = stream.read()
                copy = backup/source.name
                copy.write_bytes(data)
                workbook_hashes[str(source)] = hashlib.sha256(data).hexdigest()
                backup_database(ROOT/f'runtime/{kind}-entry/audit.sqlite3', backup/f'{kind}-audit.sqlite3')
            for name in ('reports/telegram_usage/telegram_users.db', 'data/fleet/db/fleet_ops.db'):
                backup_database(ROOT/name, backup/Path(name).name)
            for name in FILES:
                if (ROOT/name).exists():
                    target = backup/name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT/name, target)
            for name in ('work_order.json', 'repairs_entry.json', 'maintenance_entry.json'):
                path = ROOT/'runtime/bale_ui'/name
                if path.exists():
                    shutil.copy2(path, backup/name)
            print('Draining idle Gateway', identity['pid'], flush=True)
            windows.request_stop(HOME)
            stopped = True
            assert windows._drain_gateway_pid(identity['pid'], 30), 'Graceful drain incomplete; no force kill'
            assert windows.wait_stopped(HOME, 10), 'Supervisor still stopping'
            assert not identify(HOME, timeout=2), 'Gateway still present'
            for name in FILES:
                target = ROOT/name
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name('.daily7-' + uuid.uuid4().hex + '.tmp')
                try:
                    with temporary.open('xb') as stream:
                        stream.write((CANDIDATE/name).read_bytes())
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, target)
                    published.append(name)
                finally:
                    temporary.unlink(missing_ok=True)
                assert digest(target) == changed[name]['after']
            assert all(digest(path) == expected for path, expected in workbook_hashes.items())
            git('add', '--', *[name for name in FILES if name != REPORT])
            git('add', '-f', '--', REPORT)  # Repository intentionally ignores *.md.
            assert set(git('diff', '--cached', '--name-only').splitlines()) == set(FILES)
            git('diff', '--cached', '--check')
            commit_output = git('commit', '-m', 'Enable Hedayati repairs and seven-day manual daily entry')
            receipt = dict(success=True, installed_tehran=datetime.now(ZoneInfo('Asia/Tehran')).isoformat(),
                commit=git('rev-parse', 'HEAD'), commit_output=commit_output, code=changed, backup=str(backup),
                workbook_sha256=workbook_hashes, lifecycle=lifecycle, old_pid=identity['pid'],
                production_test_entries=0, test_messages_sent=0, authorization_DB_modified=False,
                sync_script_sha256=digest(HOME/'scripts/mine_file_sync.py'))
            save('installed.json', receipt)
            print('Installed and committed', receipt['commit'], flush=True)
    except BaseException:
        if published and not (OUT/'installed.json').exists():
            for name in reversed(published):
                original = backup/name
                if original.exists():
                    shutil.copy2(original, ROOT/name)
                else:
                    (ROOT/name).unlink(missing_ok=True)
        raise
    finally:
        if stopped:
            windows.prepare_start(HOME)
            spawned = windows._spawn_detached()
            windows._wait_for_gateway_ready(timeout_s=30, confirm_s=2)
            deadline = time.monotonic() + 60
            fresh_identity = None
            while time.monotonic() < deadline:
                fresh_identity = identify(HOME, timeout=2)
                fresh_status = query(HOME, 'status', timeout=2) if fresh_identity else None
                if fresh_status and fresh_status.get('gateway_state') == 'running':
                    break
                time.sleep(1)
            save('gateway-restart.json', dict(ready=bool(fresh_identity), spawned=spawned, identity=fresh_identity))
            assert fresh_identity, 'Gateway restart needs review'
    verify()


def verify():
    installed = load(OUT/'installed.json')
    assert installed['success'] and git('rev-parse', 'HEAD') == installed['commit']
    for name, hashes in installed['code'].items():
        assert digest(ROOT/name) == hashes['after']
    assert digest(HOME/'scripts/mine_file_sync.py') == installed['sync_script_sha256']
    identify, query, _ = native()
    identity, status = identify(HOME, timeout=10), query(HOME, 'status', timeout=10)
    assert identity and identity['pid'] != installed['old_pid']
    assert status['gateway_state'] == 'running'
    assert {'default', 'admin', 'maintenance', 'net'} <= set(identity['served_profiles'])
    assert status['platforms']['bale']['state'] == 'connected'
    from tools.authorization import AuthorizationStore
    from tools.authorization.net import repairs_sections
    from tools.fleet.repairs.maintenance_permission import permission
    auth = AuthorizationStore()
    before = load(OUT/'live-before.json')
    for actor, expected in before['people'].items():
        assert list(auth.roles(actor)) == expected['roles']
        assert auth.resolve_profile(actor, actor) == expected['profile']
        assert list(repairs_sections(actor, load(ROOT/'settings/repairs_entry.json'))) == expected['sections']
    with closing(auth._connect()) as db:
        for table in ('auth_user_roles', 'auth_domain_authority', 'auth_role_capabilities', 'auth_capabilities', 'auth_role_profiles'):
            rows = db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
            assert hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest() == before[table+'_sha256']
    permission('641220453')
    for actor in ('1732374823', '387679249'):
        try:
            permission(actor)
        except PermissionError:
            pass
        else:
            raise AssertionError('Unauthorized maintenance access')
    current_sync = load(HOME/'state/mine-file-sync.json')
    save('final-verification.json', dict(passed=True, verified_tehran=datetime.now(ZoneInfo('Asia/Tehran')).isoformat(),
        gateway=identity, status=status, prior_authorization_unchanged=True, hedayati_maintenance=True,
        code_hashes_verified=True, sync=current_sync, production_test_entries=0, test_messages_sent=0))
    print(json.dumps(dict(passed=True, commit=installed['commit'], gateway_pid=identity['pid'],
        bale=status['platforms']['bale']['state'], served_profiles=identity['served_profiles']), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['check', 'install', 'verify'])
    args = parser.parse_args()
    if args.phase == 'check':
        check()
        print('Exact candidate, 289 tests, native Excel and unchanged protected scope verified')
    elif args.phase == 'install':
        install()
    else:
        verify()
