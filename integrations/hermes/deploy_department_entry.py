"""Install the tested one-file sync hook, then gracefully reload the idle gateway."""
from contextlib import closing
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import uuid

from tools.fleet.repairs.entry_service import ROOT, CONFIG, RUNTIME
from tools.fleet.repairs.sync_service import initialize, merge_snapshot

HOME = Path('C:/Users/win-10/AppData/Local/hermes')
OUT = ROOT/'runtime/department-fault-entry-20261010'
STAGED = OUT/'stage/mine_file_sync.py'
SCRIPT = HOME/'scripts/mine_file_sync.py'
CODE = ['tools/authorization/net.py', 'tools/fleet/repairs/entry_service.py',
        'tools/fleet/repairs/entry_bale.py', 'tools/fleet/repairs/sync_service.py',
        'tools/bale_ui/runtime.py', 'settings/repairs_entry.json']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf8')


def load_stage():
    spec = importlib.util.spec_from_file_location('department_sync_install', STAGED)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def install():
    assert 'Ran 132 tests' in (OUT/'deployment-tests.log').read_text(encoding='utf-16')
    assert (OUT/'deployment-tests.log').read_text(encoding='utf-16').rstrip().endswith('OK')
    assert json.loads((OUT/'other-sync-tests.json').read_text())['success']
    assert json.loads((OUT/'real-copy/evidence.json').read_text())['success']
    for name in CODE:
        if name.endswith('.py'):
            ast.parse((ROOT/name).read_text(encoding='utf8'))
    ast.parse(STAGED.read_text(encoding='utf8'))
    if (OUT/'installed.json').exists():
        previous = json.loads((OUT/'installed.json').read_text(encoding='utf8'))
        assert digest(SCRIPT) == previous['sync_sha256']
        changed = [name for name, expected in previous['code'].items() if digest(ROOT/name) != expected]
        if changed:
            assert changed == ['tools/fleet/repairs/entry_service.py'], 'Unexpected deployment change'
            checks = (OUT/'writer-final-tests.log').read_text(encoding='utf-16')
            assert 'Ran 23 tests' in checks and checks.rstrip().endswith('OK')
            previous['worker_revision'] = dict(reason='Preserve mine footer and print columns when appending a missing machine',
                before=previous['code'][changed[0]], after=digest(ROOT/changed[0]),
                tests_passed=23, loading='Fresh worker subprocess per request; no further gateway restart required')
            previous['code'][changed[0]] = digest(ROOT/changed[0])
            save('installed.json', previous)
        print('Already installed; exact hashes verified')
        return
    manifest = json.loads((OUT/'continuation-before/manifest.json').read_text(encoding='utf8'))
    assert digest(SCRIPT) == manifest[str(SCRIPT)]['sha256'], 'Live sync changed since backup'
    module = load_stage()
    lock = module.acquire_lock(HOME/'state/mine-file-sync-worker.lock')
    if lock is None:
        raise RuntimeError('A mine sync worker is active; retry after its bounded run')
    try:
        source = Path(json.loads(CONFIG.read_text(encoding='utf8'))['source'])
        baseline = OUT/'source-copy.xlsx'
        # This exact snapshot was previously compared read-only with the mine.
        expected = json.loads((OUT/'excel-before.json').read_text(encoding='utf8'))['hash']
        assert digest(baseline) == expected == digest(source), 'Baseline changed; do not guess a merge'
        if (RUNTIME/'audit.sqlite3').exists():
            with closing(sqlite3.connect((RUNTIME/'audit.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(OUT/'audit-before-activation.sqlite3')) as dst:
                src.backup(dst)
        result = initialize(baseline)
        temp = SCRIPT.with_name('.mine-fault-install-'+uuid.uuid4().hex+'.tmp')
        try:
            with temp.open('xb') as stream:
                stream.write(STAGED.read_bytes()); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, SCRIPT)
        finally:
            temp.unlink(missing_ok=True)
        assert digest(SCRIPT) == digest(STAGED)
        # Exercise the live merge boundary with an identical trusted snapshot.
        # This must be a no-op and must not modify the production workbook.
        assert merge_snapshot(baseline, source)['status'] == 'unchanged'
        assert digest(source) == expected
        save('installed.json', dict(success=True, sync_sha256=digest(SCRIPT), initialized=result,
            code={name: digest(ROOT/name) for name in CODE}, live_workbook_sha256=expected,
            production_test_entries=0, live_workbook_unchanged=True,
            authorization_core_sha256=digest(HOME/'hermes-agent/gateway/authz_mixin.py')))
        print('Sync installed, baseline initialized, live no-op verified; workbook unchanged')
    finally:
        module.release_lock(lock)


def native():
    os.environ['HERMES_HOME'] = str(HOME)
    core = HOME/'hermes-agent'
    sys.path.extend([str(core), str(core/'venv/Lib/site-packages')])
    import tools
    if str(core/'tools') not in tools.__path__:
        tools.__path__.append(str(core/'tools'))
    from gateway.control_socket import identify_gateway, query_gateway_control
    return identify_gateway, query_gateway_control


def restart():
    installed = json.loads((OUT/'installed.json').read_text(encoding='utf8'))
    assert installed['success'] and digest(SCRIPT) == installed['sync_sha256']
    assert all(digest(ROOT/name) == expected for name, expected in installed['code'].items())
    for name in ['repairs_entry.json', 'maintenance_entry.json', 'work_order.json']:
        path = ROOT/'runtime/bale_ui'/name
        if path.exists():
            assert not any(value.get('stage') == 'BUSY' for _, value in json.loads(path.read_text(encoding='utf8'))), 'A form worker is busy'
    identify, query = native()
    before = identify(HOME, timeout=10)
    status = query(HOME, 'status', timeout=10)
    assert before and status and status['gateway_state'] == 'running'
    prior = OUT/'gateway-before-activation.json'
    if prior.exists():
        old = json.loads(prior.read_text(encoding='utf8'))['identity']['pid']
        if before['pid'] != old:
            # The existing supervisor may already have supplied the replacement.
            save('gateway-restart.json', dict(success=True, old_pid=old, new_pid=before['pid'],
                                             replacement_by_existing_supervisor=True))
            print('Fresh supervisor replacement already active', before['pid'], flush=True)
            return
    assert not status.get('active_agents'), 'Gateway is busy; wait for the active run'
    save('gateway-before-activation.json', dict(identity=before, status=status))
    from hermes_cli import gateway_windows
    print('Gracefully draining gateway', before['pid'], flush=True)
    assert gateway_windows._drain_gateway_pid(before['pid'], 30), 'Drain pending; no duplicate process started'
    replacement = identify(HOME, timeout=3)
    spawned = None
    if not replacement:
        if gateway_windows._wait_for_gateway_absent(timeout_s=10):
            spawned = gateway_windows._spawn_detached()
            print('Detached canonical supervisor started', spawned, flush=True)
        else:
            assert identify(HOME, timeout=5), 'Supervisor replacement is pending; no duplicate process started'
    assert gateway_windows._wait_for_gateway_ready(timeout_s=25, confirm_s=2)
    after = identify(HOME, timeout=10)
    assert after and after['pid'] != before['pid']
    save('gateway-restart.json', dict(success=True, old_pid=before['pid'], new_pid=after['pid'], spawned=spawned))
    print('Fresh gateway control identity', after['pid'], flush=True)


def verify():
    installed = json.loads((OUT/'installed.json').read_text(encoding='utf8'))
    identify, query = native()
    identity = identify(HOME, timeout=10); status = query(HOME, 'status', timeout=10)
    assert identity and status and status['gateway_state'] == 'running'
    assert all(status['platforms'][p]['state'] == 'connected' for p in ['bale', 'telegram'])
    assert {'default', 'admin', 'maintenance', 'net'} <= set(identity['served_profiles'])
    assert digest(SCRIPT) == installed['sync_sha256']
    assert all(digest(ROOT/name) == expected for name, expected in installed['code'].items())
    assert digest(HOME/'hermes-agent/gateway/authz_mixin.py') == installed['authorization_core_sha256']
    from tools.bale_ui.runtime import _main_menu
    from tools.fleet.repairs.entry_bale import sections_for
    from tools.authorization import AuthorizationStore
    before_users = json.loads((OUT/'users-before.json').read_text(encoding='utf8'))
    auth = AuthorizationStore(); users = {}
    for actor, section in [('654806764', 'mechanical'), ('1732374823', 'mechanical'), ('387679249', 'metalwork')]:
        assert sections_for(actor) == (section,)
        menu = _main_menu(actor, bale_approved=True)
        assert menu.command_for('🛠 شرح خرابی') == 'شرح خرابی'
        users[actor] = dict(sections=sections_for(actor), fault_button=True)
    for actor in ['641220453', '1294822197']:
        menu = _main_menu(actor, bale_approved=True)
        assert menu.command_for('🛠 شرح خرابی') is None
        assert menu.command_for('📋 حکم کار') == 'حکم کار'
        users[actor] = dict(sections=sections_for(actor), fault_button=False)
    for actor, expected in before_users.items():
        assert list(auth.roles(actor)) == expected['roles']
        assert auth.resolve_profile(actor, actor) == expected['profile']
    source = Path(json.loads(CONFIG.read_text(encoding='utf8'))['source'])
    assert digest(source) == installed['live_workbook_sha256'], 'Live workbook changed; inspect genuine operational activity'
    with closing(sqlite3.connect((RUNTIME/'audit.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)) as con:
        assert con.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert con.execute('SELECT COUNT(*) FROM fault_sync_sources').fetchone()[0] == 1
    result = dict(success=True, users=users, gateway=identity, status=status,
        pairing_fix_preserved=True, source_unchanged=True, production_test_entries=0,
        code_loaded_by_fresh_gateway=True, sync_sha256=digest(SCRIPT))
    save('final-verification.json', result)
    print(json.dumps(dict(success=True, gateway_pid=identity['pid'], bale='connected', telegram='connected', users=users), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['install', 'restart', 'verify'])
    phase = parser.parse_args().phase
    dict(install=install, restart=restart, verify=verify)[phase]()


if __name__ == '__main__':
    main()
