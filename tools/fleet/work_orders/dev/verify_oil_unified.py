"""Run isolated oil UI and OC/GR/AF regressions without production writes/sends."""
import argparse
import hashlib
from contextlib import closing
import io
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.authorization.store import AuthorizationStore
from tools.fleet.work_orders.dev import test_permissions

# Shared integration modules are unchanged; every patched tools module is
# present in the isolated stage and resolves there before this fallback.
sys.path.append('E:/KomatsoAI')

AUDIT = [
    'tools.fleet.work_orders.dev.test_inline_menus.InlineMenusTests',
    'tools.fleet.work_orders.dev.test_oil_service_edit.OilServiceEditTests',
    'tools.fleet.oil_change.test_proposal',
    'tools.fleet.work_orders.dev.test_permissions.PermissionTests',
]
REGRESSION = [
    'test_oil_change', 'test_oil_p1_safety', 'test_oil_p2_print',
    'test_greasing_safety', 'test_air_filter_safety', 'test_manager_review',
    'test_staff_dispatch', 'test_final_copy', 'test_proposal_form',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=['audit', 'unified', 'regression', 'smoke'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base=Path(__file__).resolve().parents[4]
    release_files=['tools/fleet/work_orders/channels/bale/'+name+'.py' for name in
                   ('oil_editor','keyboards','proposal_form','message_handler')]
    release_files+=['tools/fleet/work_orders/dev/'+name+'.py' for name in
                    ('test_inline_menus','test_oil_unified','verify_oil_unified','test_oil_change')]
    def hashes():
        return {name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in release_files}
    tested_hashes=hashes()
    logging.basicConfig(stream=io.StringIO(), level=logging.WARNING)
    original_connect = socket.socket.connect

    def offline_connect(sock, address):
        # Windows asyncio needs its local self-pipe socketpair.
        if isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
            return original_connect(sock, address)
        raise AssertionError('External transport is forbidden in this verifier')

    smoke=['test_sequential_devices_same_page_no_extra_messages_and_restart',
           'test_legacy_batch_sessions_drop_only_pending_edits_and_reject_old_callbacks',
           'test_real_excel_pdf_review_and_revision_from_net_menu',
           'test_multiple_inline_devices_create_independent_native_excel_pdf']
    names = (['tools.fleet.work_orders.dev.test_oil_unified.OilUnifiedTests.'+name for name in smoke]
             if args.group == 'smoke' else AUDIT if args.group == 'audit' else
             ['tools.fleet.work_orders.dev.test_oil_unified'] if args.group == 'unified' else
             ['tools.fleet.work_orders.dev.' + name for name in REGRESSION])
    with tempfile.TemporaryDirectory(prefix='oil_unified_verify_') as directory:
        root = Path(directory)
        auth = root / 'authorization.db'
        with closing(sqlite3.connect(auth)) as con, con:
            con.execute('CREATE TABLE auth_migrations(version INTEGER, applied_at TEXT)')
            con.execute("INSERT INTO auth_migrations VALUES(1,'isolated fixture')")
        with patch.object(test_permissions, 'PROJECT_ROOT', root), \
             patch('tools.authorization.net.AuthorizationStore', lambda: AuthorizationStore(auth)), \
             patch.dict(os.environ, {'KOMATSO_OIL_EVIDENCE': str(args.output.parent / 'evidence')}), \
             patch('socket.socket.connect', offline_connect):
            suite = unittest.TestLoader().loadTestsFromNames(names)
            output = io.StringIO()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            progress = args.output.with_suffix('.progress')
            progress.write_text('', encoding='utf-8')
            class TrackingResult(unittest.TextTestResult):
                def startTest(self, test):
                    with progress.open('a', encoding='utf-8') as stream:
                        stream.write(test.id() + '\n')
                    super().startTest(test)
            result = unittest.TextTestRunner(stream=output, verbosity=1, resultclass=TrackingResult).run(suite)
            record = dict(group=args.group, run=result.testsRun, failures=len(result.failures),
                          errors=len(result.errors), skipped=len(result.skipped), success=result.wasSuccessful(),
                          details=output.getvalue(), transport='SIMULATED', external_network='BLOCKED')
            record.update(source_root=str(base),source_sha256=tested_hashes,source_unchanged=hashes()==tested_hashes)
            record['success']=record['success'] and record['source_unchanged']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output.getvalue())
    print(json.dumps({k: v for k, v in record.items() if k not in {'details','source_sha256'}}))
    raise SystemExit(0 if record['success'] else 1)


if __name__ == '__main__':
    main()
