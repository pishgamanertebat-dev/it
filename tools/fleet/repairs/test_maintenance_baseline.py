"""Approved-boundary, real Journal, crash and conservative-merge contracts."""
from contextlib import closing
import json
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import openpyxl

from . import entry_service, maintenance_service
from .maintenance_baseline import initialize, digest, EVENT
from .maintenance_sync import guard_snapshot, MaintenanceSyncBlocked
from . import test_maintenance_service as service_tests


class MaintenanceBaselineTests(unittest.TestCase):
    setUp = service_tests.MaintenanceServiceTests.setUp
    request = service_tests.MaintenanceServiceTests.request
    save = service_tests.MaintenanceServiceTests.save

    def setup_baseline(self, legacy=True):
        self.mine = self.root / 'mine.xlsx'
        self.mine.write_bytes(self.source.read_bytes())
        if legacy:
            self.old_request = self.request(description='unverified historic operation')
            self.save(self.old_request)
            self.source.write_bytes(self.mine.read_bytes())
        else:
            with closing(entry_service.open_audit(self.root / 'journal')):
                pass
        self.args = dict(config=self.config, runtime=self.root / 'journal')
        self.approval = dict(expected_hash=digest(self.mine.read_bytes()),
                             decision='Owner accepts current content; preserve history; no replay',
                             approved_by='project-owner', **self.args)
        return initialize(self.mine, **self.approval)

    def sync(self):
        return guard_snapshot(self.mine, self.source, **self.args)

    def change_mine(self, code='851', description='mine new event'):
        request = self.request(code, description=description, mechanic='mine mechanic')
        book = openpyxl.load_workbook(self.mine)
        try:
            maintenance_service.append_record(book, request)
            book.save(self.mine)
        finally:
            book.close()

    def edits(self):
        with closing(sqlite3.connect(self.root / 'journal/audit.sqlite3')) as db:
            return db.execute('SELECT rowid,* FROM edits ORDER BY rowid').fetchall()

    def test_baseline_equal_preserves_excel_and_historic_succeeded(self):
        original = self.source.read_bytes()
        result = self.setup_baseline()
        old = self.edits()
        self.assertEqual(result['kind'], EVENT)
        self.assertFalse(result['historical_presence_proven'])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.mine.read_bytes(), original)
        for _ in range(2):
            with patch.object(entry_service, 'replace_source', side_effect=AssertionError('no Replace')):
                self.assertEqual(self.sync()['status'], 'unchanged')
        self.assertEqual(self.edits(), old)
        self.assertEqual(old[0][-3], 'succeeded')
        with self.assertRaisesRegex(entry_service.EntryError, 'عملیات'):
            self.save(self.old_request)
        self.assertEqual(initialize(self.mine, **self.approval)['status'], 'already_enabled')

    def test_mine_only_exact_byte_import_then_noop(self):
        self.setup_baseline()
        self.change_mine()
        incoming = self.mine.read_bytes()
        self.assertEqual(self.sync()['status'], 'imported')
        self.assertEqual(self.source.read_bytes(), incoming)
        rows = self.edits()
        self.assertEqual(self.sync()['status'], 'unchanged')
        self.assertEqual(self.edits(), rows)

    def test_new_bale_uuid_protected_and_retry_never_appends(self):
        self.setup_baseline()
        request = self.request()
        result = self.save(request)
        local = self.source.read_bytes()
        self.assertIn(request['operation'], self.sync()['protected_operations'])
        self.assertEqual(self.source.read_bytes(), local)
        self.assertEqual(self.save(request), result)
        self.assertEqual(self.source.read_bytes(), local)

    def test_disjoint_sheets_merge_and_persist_across_repeated_mine_changes(self):
        self.setup_baseline()
        request = self.request(description='same description')
        result = self.save(request)
        self.change_mine(description='same description')
        self.assertEqual(self.sync()['status'], 'merged')
        self.assertEqual(self.sync()['status'], 'unchanged')
        self.change_mine(description='second mine event')
        self.assertEqual(self.sync()['status'], 'merged')
        self.assertEqual(self.save(request), result)
        book = openpyxl.load_workbook(self.source)
        try:
            self.assertEqual(book['710']['D4'].value, 'same description')
            self.assertEqual(book['851']['E4'].value, 'same description')
            self.assertEqual(book['851']['E5'].value, 'second mine event')
        finally:
            book.close()

    def test_same_sheet_same_text_two_uuids_and_same_row_is_ambiguous(self):
        self.setup_baseline()
        request = self.request(description='same text')
        self.save(request)
        self.change_mine('710', 'same text')
        before, mine = self.source.read_bytes(), self.mine.read_bytes()
        for _ in range(2):
            with self.assertRaisesRegex(MaintenanceSyncBlocked, 'AMBIGUOUS_BRANCHES.*710'):
                self.sync()
        self.assertEqual(before, self.source.read_bytes())
        self.assertEqual(mine, self.mine.read_bytes())
        self.assertEqual(len(self.edits()), 2)

    def test_historical_cell_deletion_header_and_sheet_changes_fail_closed(self):
        self.setup_baseline()
        baseline = self.mine.read_bytes()
        for mutate in (lambda b: setattr(b['710']['D3'], 'value', None),
                       lambda b: setattr(b['710']['D3'], 'value', 'changed'),
                       lambda b: setattr(b['710']['D2'], 'value', 'bad header'),
                       lambda b: b.remove(b['710'])):
            self.mine.write_bytes(baseline)
            book = openpyxl.load_workbook(self.mine)
            mutate(book)
            book.save(self.mine)
            book.close()
            with self.assertRaises((MaintenanceSyncBlocked, entry_service.EntryError)):
                self.sync()
            self.assertEqual(self.source.read_bytes(), baseline)

    def test_baseline_requires_owner_hash_and_no_pending_operations(self):
        self.setup_baseline()
        with self.assertRaises(PermissionError):
            initialize(self.mine, **dict(self.approval, approved_by='654806764'))
        with self.assertRaisesRegex(entry_service.EntryError, 'CHANGED'):
            initialize(self.mine, **dict(self.approval, expected_hash='0'*64))
        with closing(sqlite3.connect(self.root / 'journal/audit.sqlite3')) as db:
            db.execute("UPDATE edits SET status='prepared'")
            db.commit()
        with self.assertRaisesRegex(entry_service.EntryError, 'UNRESOLVED'):
            initialize(self.mine, **self.approval)

    def test_local_unjournaled_and_missing_or_tampered_snapshot_block(self):
        self.setup_baseline()
        original = self.source.read_bytes()
        self.source.write_bytes(original + b'unjournaled')
        with self.assertRaisesRegex(MaintenanceSyncBlocked, 'UNJOURNALED_LOCAL'):
            self.sync()
        self.source.write_bytes(original)
        baseline = self.root / 'journal/backups' / (digest(original) + '.xlsx')
        baseline.write_bytes(b'corrupt')
        with self.assertRaisesRegex(MaintenanceSyncBlocked, '(SNAPSHOT|BACKUP)_CORRUPT'):
            self.sync()

    def test_historic_audit_tampering_is_not_hidden_by_baseline(self):
        self.setup_baseline()
        with closing(sqlite3.connect(self.root / 'journal/audit.sqlite3')) as db:
            db.execute("UPDATE edits SET actor='tampered'")
            db.commit()
        with self.assertRaisesRegex(MaintenanceSyncBlocked, 'HISTORICAL_JOURNAL_CHANGED'):
            self.sync()

    def test_baseline_event_immutable(self):
        self.setup_baseline()
        with closing(sqlite3.connect(self.root / 'journal/audit.sqlite3')) as db:
            for command in ('DELETE FROM maintenance_baseline_events',
                            "UPDATE maintenance_baseline_events SET kind='wrong'"):
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable'):
                    db.execute(command)

    def test_cut_before_replace_recovers_without_duplicate(self):
        self.setup_baseline()
        self.change_mine()
        before = self.source.read_bytes()
        with patch.object(entry_service, 'replace_source', side_effect=OSError('power loss before Replace')):
            with self.assertRaises(MaintenanceSyncBlocked):
                self.sync()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.sync()['status'], 'imported')
        self.assertEqual([r[-3] for r in self.edits()], ['succeeded', 'not_applied', 'succeeded'])
        self.assertEqual(self.sync()['status'], 'unchanged')

    def test_cut_after_replace_recovers_same_uuid(self):
        self.setup_baseline()
        self.change_mine()
        replace = entry_service.replace_source
        def interrupted(*args):
            replace(*args)
            raise OSError('power loss after Replace')
        with patch.object(entry_service, 'replace_source', side_effect=interrupted):
            with self.assertRaises(MaintenanceSyncBlocked):
                self.sync()
        operations = [r[1] for r in self.edits()]
        self.assertEqual(self.sync()['status'], 'unchanged')
        self.assertEqual(operations, [r[1] for r in self.edits()])
        self.assertTrue(all(r[-3] == 'succeeded' for r in self.edits()))

    def test_cut_during_baseline_insert_leaves_no_active_event_and_retry_works(self):
        self.setup_baseline()
        # Independent Journal copy, preserving the actual historic edit row.
        other = self.root / 'cut-journal'
        other.mkdir()
        with closing(sqlite3.connect(self.root / 'journal/audit.sqlite3')) as src, \
             closing(sqlite3.connect(other / 'audit.sqlite3')) as dst:
            src.backup(dst)
            dst.execute('DROP TRIGGER maintenance_baseline_no_delete')
            dst.execute('DELETE FROM maintenance_baseline_events')
            dst.execute("CREATE TRIGGER cut_insert BEFORE INSERT ON maintenance_baseline_events "
                        "BEGIN SELECT RAISE(ABORT, 'interrupted transaction'); END")
            dst.commit()
        args = dict(self.approval, runtime=other)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'interrupted'):
            initialize(self.mine, **args)
        with closing(sqlite3.connect(other / 'audit.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM maintenance_baseline_events').fetchone()[0], 0)
            db.execute('DROP TRIGGER cut_insert')
            db.commit()
        self.assertEqual(initialize(self.mine, **args)['status'], 'enabled')

    def test_installed_sync_route_reports_unchanged_without_replace_and_imports_new_mine(self):
        self.setup_baseline()
        from integrations.hermes.deploy_maintenance_baseline import OUT, LIVE
        script = OUT / 'stage/mine_file_sync.py'
        spec = importlib.util.spec_from_file_location('approved_baseline_route_test', script if script.exists() else LIVE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        mine_folder = self.root / 'incoming'
        mine_folder.mkdir()
        incoming = mine_folder / self.source.name
        incoming.write_bytes(self.mine.read_bytes())
        def publish(staged, destination, **kwargs):
            return guard_snapshot(staged, destination, **self.args)
        with patch.object(module, 'MAINTENANCE_REPORT', self.source), \
             patch.object(module, 'publish_fault_report', side_effect=publish):
            result = module.empty_result()
            module.sync_tree(mine_folder, self.root, result, lambda **kwargs: None)
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['maintenance_guard']['status'], 'unchanged')
            self.assertEqual((result['updated'], result['skipped']), (0, 1))
            self.change_mine()
            incoming.write_bytes(self.mine.read_bytes())
            result = module.empty_result()
            module.sync_tree(mine_folder, self.root, result, lambda **kwargs: None)
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['maintenance_guard']['status'], 'imported')
            self.assertEqual(self.source.read_bytes(), incoming.read_bytes())

    def test_equal_bytes_with_bale_keep_uuid_and_do_not_claim_upstream_event_identity(self):
        self.setup_baseline()
        request = self.request()
        self.save(request)
        self.mine.write_bytes(self.source.read_bytes())
        rows = self.edits()
        result = self.sync()
        self.assertEqual(result['status'], 'unchanged')
        self.assertTrue(result['upstream_identity_unacknowledged'])
        self.assertIn(request['operation'], result['protected_operations'])
        self.assertEqual(self.edits(), rows)

    def test_modified_post_baseline_request_fails_journal_validation(self):
        self.setup_baseline()
        request = self.request()
        self.save(request)
        with closing(sqlite3.connect(self.root / 'journal/audit.sqlite3')) as db:
            db.execute("UPDATE edits SET actor='tampered' WHERE operation=?", (request['operation'],))
            db.commit()
        with self.assertRaisesRegex(MaintenanceSyncBlocked, 'INVALID_JOURNAL_FINGERPRINT'):
            self.sync()

    def test_serialization_failure_preserves_both_branches_then_retries(self):
        self.setup_baseline()
        self.save(self.request())
        self.change_mine()
        before, incoming = self.source.read_bytes(), self.mine.read_bytes()
        rows = self.edits()
        with patch('openpyxl.workbook.workbook.Workbook.save', side_effect=OSError('disk full during save')):
            with self.assertRaises(OSError):
                self.sync()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.mine.read_bytes(), incoming)
        self.assertEqual(self.edits(), rows)
        self.assertEqual(self.sync()['status'], 'merged')

    def test_process_exit_mid_baseline_transaction_recovers_without_partial_activation(self):
        self.setup_baseline()
        other = self.root / 'process-cut'
        other.mkdir()
        with closing(sqlite3.connect(self.root / 'journal/audit.sqlite3')) as src, \
             closing(sqlite3.connect(other / 'audit.sqlite3')) as dst:
            src.backup(dst)
            dst.execute('DROP TRIGGER maintenance_baseline_no_delete')
            dst.execute('DELETE FROM maintenance_baseline_events')
            dst.execute('CREATE TRIGGER terminate_insert BEFORE INSERT ON maintenance_baseline_events '
                        'BEGIN SELECT terminate_process(); END')
            dst.commit()
        code = '''import os,sys,json
from tools.fleet.repairs import maintenance_baseline as m
original=m.open_audit
def connect(runtime):
    db=original(runtime)
    db.create_function('terminate_process',0,lambda:os._exit(73))
    return db
m.open_audit=connect
args=json.loads(sys.argv[1])
m.initialize(sys.argv[2],**args)
'''
        args = {k:str(v) if isinstance(v,Path) else v for k,v in dict(self.approval, runtime=other).items()}
        child = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, json.dumps(args), str(self.mine)],
                               cwd=maintenance_service.ROOT, capture_output=True, timeout=30)
        self.assertEqual(child.returncode, 73, child.stderr)
        with closing(sqlite3.connect(other / 'audit.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM maintenance_baseline_events').fetchone()[0], 0)
            db.execute('DROP TRIGGER terminate_insert')
            db.commit()
        self.assertEqual(initialize(self.mine, **dict(self.approval, runtime=other))['status'], 'enabled')


if __name__ == '__main__':
    unittest.main()
