"""Run isolated oil UI and OC/GR/AF regressions without production writes/sends."""
import argparse
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
    parser.add_argument('--group', choices=['audit', 'unified', 'regression'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(stream=io.StringIO(), level=logging.WARNING)
    original_connect = socket.socket.connect

    def offline_connect(sock, address):
        # Windows asyncio needs its local self-pipe socketpair.
        if isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
            return original_connect(sock, address)
        raise AssertionError('External transport is forbidden in this verifier')

    names = (AUDIT if args.group == 'audit' else
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output.getvalue())
    print(json.dumps({k: v for k, v in record.items() if k != 'details'}))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
