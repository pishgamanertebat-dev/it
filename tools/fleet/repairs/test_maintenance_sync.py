"""The explicitly permitted partial outcome: retain both inputs, block replacement."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import threading
from types import ModuleType
import unittest
from unittest.mock import patch

import openpyxl

from . import entry_service, maintenance_service
from .entry_service import EntryAuthorizationDenied, EntryError
from .maintenance_sync import guard_snapshot, MaintenanceSyncBlocked
from . import test_maintenance_service as service_tests


class MaintenanceGuardTests(unittest.TestCase):
    setUp = service_tests.MaintenanceServiceTests.setUp
    request = service_tests.MaintenanceServiceTests.request
    save = service_tests.MaintenanceServiceTests.save

    def setup_guard(self):
        self.mine = self.root / 'mine.xlsx'
        self.mine.write_bytes(self.source.read_bytes())
        self.sync_kw = dict(config=self.config, runtime=self.root / 'journal')

    def guard(self):
        return guard_snapshot(self.mine, self.source, **self.sync_kw)

    def change_mine(self, mutate):
        book = openpyxl.load_workbook(self.mine)
        try:
            mutate(book)
            book.save(self.mine)
        finally:
            book.close()

    def failures(self):
        with closing(sqlite3.connect(self.root / 'journal/audit.sqlite3')) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM maintenance_sync_failures')]

    def assert_blocked_preserves_both(self, reason='MAINTENANCE_SYNC_REVIEW_REQUIRED'):
        local, incoming = self.source.read_bytes(), self.mine.read_bytes()
        with self.assertRaisesRegex(MaintenanceSyncBlocked, reason):
            self.guard()
        self.assertEqual(self.source.read_bytes(), local)
        self.assertEqual(self.mine.read_bytes(), incoming)
        row = self.failures()[-1]
        self.assertEqual(Path(row['local_snapshot']).read_bytes(), local)
        self.assertEqual(Path(row['incoming_snapshot']).read_bytes(), incoming)
        return row

    def test_previous_row_loss_reproduction_now_preserves_row_and_mine_snapshot(self):
        self.setup_guard()
        request = self.request()
        self.save(request)
        row = self.assert_blocked_preserves_both()
        self.assertEqual(json.loads(row['operations'])[0]['operation'], request['operation'])
        before = self.source.read_bytes()
        self.assert_blocked_preserves_both()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(len(self.failures()), 1)
        self.assertEqual(self.failures()[0]['attempts'], 2)
        self.assertEqual(self.save(request)['row'], 4)
        self.assertEqual(self.source.read_bytes(), before)

    def test_simultaneous_mine_and_bale_appends_remain_separate_pending_review(self):
        self.setup_guard()
        self.save(self.request(description='Bale event'))
        self.change_mine(lambda book: book['710'].cell(4, 4, 'Mine event'))
        self.assert_blocked_preserves_both()

    def test_identical_text_distinct_operation_ids_never_deduplicated(self):
        self.setup_guard()
        first, second = self.request(), self.request()
        self.save(first)
        self.save(second)
        row = self.assert_blocked_preserves_both()
        self.assertEqual(len(json.loads(row['operations'])), 2)
        book = openpyxl.load_workbook(self.source)
        try:
            self.assertEqual(book['710']['D4'].value, book['710']['D5'].value)
        finally:
            book.close()

    def test_mine_manual_edit_delete_and_structure_changes_all_block(self):
        self.setup_guard()
        self.save(self.request())
        mine = self.mine.read_bytes()
        for mutate in (lambda book: book['710'].cell(3, 4, 'edited history'),
                       lambda book: book['710'].delete_rows(3),
                       lambda book: book['710'].cell(2, 4, 'new schema'),
                       lambda book: book.remove(book['710'])):
            with self.subTest(mutate=mutate):
                self.mine.write_bytes(mine)
                self.change_mine(mutate)
                self.assert_blocked_preserves_both()

    def test_identical_known_publication_is_noop_without_duplicate(self):
        self.setup_guard()
        request = self.request()
        self.save(request)
        self.mine.write_bytes(self.source.read_bytes())
        before = self.source.read_bytes()
        self.assertEqual(self.guard()['status'], 'identical_noop')
        self.assertEqual(self.guard()['status'], 'identical_noop')
        self.assertEqual(self.save(request)['row'], 4)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.failures(), [])

    def test_legacy_disappeared_event_not_reported_as_healthy_or_reconstructed(self):
        self.setup_guard()
        request = self.request()
        self.save(request)
        self.source.write_bytes(self.mine.read_bytes())
        self.assert_blocked_preserves_both('MAINTENANCE_SYNC_UNVERIFIED_HISTORY')
        with self.assertRaisesRegex(EntryError, 'عملیات'):
            self.save(request)

    def test_old_operation_proven_through_later_append_hash_chain(self):
        first, second = self.request(), self.request()
        self.save(first)
        self.save(second)
        before = self.source.read_bytes()
        self.assertEqual(self.save(first)['row'], 4)
        self.assertEqual(before, self.source.read_bytes())

    def test_grouped_column_widths_not_created_or_changed_by_append(self):
        book = openpyxl.load_workbook(self.source)
        sheet = book['710']
        sheet.column_dimensions.clear()
        sheet.column_dimensions['A'].min = 1
        sheet.column_dimensions['A'].max = 5
        sheet.column_dimensions['A'].width = 35
        book.save(self.source)
        book.close()
        self.save(self.request())
        book = openpyxl.load_workbook(self.source)
        try:
            self.assertEqual(list(book['710'].column_dimensions), ['A'])
            self.assertEqual(book['710'].column_dimensions['A'].width, 35)
        finally:
            book.close()

    def test_prepared_before_replace_retries_same_operation_once(self):
        self.setup_guard()
        request = self.request()
        with patch.object(entry_service, 'replace_source', side_effect=OSError('disk error')):
            with self.assertRaises(EntryError):
                self.save(request)
        self.assertEqual(self.guard()['status'], 'identical_noop')
        self.assertEqual(self.save(request)['row'], 4)
        self.assert_blocked_preserves_both()
        self.assertEqual(self.save(request)['row'], 4)

    def test_unknown_after_replace_recovers_same_operation_without_append(self):
        self.setup_guard()
        request = self.request()
        replace = entry_service.replace_source
        def interrupted(temporary, source):
            replace(temporary, source)
            raise RuntimeError('process interrupted after replace')
        with patch.object(entry_service, 'replace_source', side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.save(request)
        after = self.source.read_bytes()
        self.assert_blocked_preserves_both()
        self.assertEqual(self.save(request)['row'], 4)
        self.assertEqual(self.source.read_bytes(), after)

    def test_ambiguous_prepared_blocks_and_retains_operation_identity(self):
        self.setup_guard()
        request = self.request()
        with patch.object(entry_service, 'replace_source', side_effect=OSError('disk error')):
            with self.assertRaises(EntryError):
                self.save(request)
        book = openpyxl.load_workbook(self.source)
        book['710']['D3'] = 'untracked change'
        book.save(self.source)
        book.close()
        row = self.assert_blocked_preserves_both('MAINTENANCE_SYNC_UNCERTAIN_OPERATION')
        self.assertEqual(json.loads(row['operations'])[0]['operation'], request['operation'])
        with self.assertRaises(EntryError):
            self.save(request)

    def test_revocation_at_final_publication_prevents_write(self):
        request = self.request()
        before = self.source.read_bytes()
        permission = maintenance_service.permission
        calls = []
        def revoke(actor, config):
            calls.append(actor)
            if len(calls) == 3:  # entry, locked Writer, immediately before Replace
                self.config.write_text(json.dumps({'source': str(self.source), 'allowed_users': []}))
            return permission(actor, config)
        with patch.object(maintenance_service, 'permission', side_effect=revoke):
            with self.assertRaises(EntryAuthorizationDenied):
                self.save(request)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.source.read_bytes(), before)

    def test_hedaayati_karamali_sadeghian_still_denied_no_new_permissions(self):
        before = self.source.read_bytes()
        for actor in ('641220453', '1732374823', '387679249'):
            with self.assertRaises(EntryAuthorizationDenied):
                self.save(self.request(), actor)
        self.assertEqual(self.source.read_bytes(), before)

    def test_sync_waits_for_the_exact_writer_lock(self):
        self.setup_guard()
        self.save(self.request())
        started = threading.Event()
        def run():
            started.set()
            with self.assertRaises(MaintenanceSyncBlocked):
                self.guard()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with entry_service.writer_lock(self.root / 'journal'):
                pending = pool.submit(run)
                self.assertTrue(started.wait(2))
                self.assertFalse(pending.done())
            pending.result(timeout=10)

    def test_actual_sync_route_including_matching_stat_fails_partial(self):
        self.setup_guard()
        self.save(self.request())
        mine_folder = self.root / 'mine-folder'
        mine_folder.mkdir()
        incoming = mine_folder / self.source.name
        incoming.write_bytes(self.mine.read_bytes())
        # Match mtime/size while differing bytes: maintenance must bypass stat skip.
        altered = bytearray(self.source.read_bytes())
        altered[-1] ^= 1
        incoming.write_bytes(altered)
        os.utime(incoming, ns=(self.source.stat().st_atime_ns, self.source.stat().st_mtime_ns))
        from integrations.hermes.deploy_safe_manual_excel import LIVE, guarded_source
        code = LIVE.read_text(encoding='utf8')
        if 'MAINTENANCE_REPORT = ' not in code:
            code = guarded_source(code)
        sync = ModuleType('guard_sync_test')
        exec(compile(code, str(LIVE), 'exec'), sync.__dict__)
        calls = []
        def publish(staged, destination, **kwargs):
            calls.append(kwargs['module'])
            try:
                guard_snapshot(staged, destination, **self.sync_kw)
            except MaintenanceSyncBlocked as exc:
                raise OSError(str(exc)) from exc
        before = self.source.read_bytes()
        replace = os.replace
        def guarded_replace(staged, target):
            if Path(target).resolve() == self.source.resolve():
                raise AssertionError('generic workbook replace forbidden')
            return replace(staged, target)
        with patch.object(sync, 'MAINTENANCE_REPORT', self.source), \
             patch.object(sync, 'publish_fault_report', side_effect=publish), \
             patch.object(sync.os, 'replace', side_effect=guarded_replace):
            result = sync.empty_result()
            sync.sync_tree(mine_folder, self.root, result, lambda **kw: None)
        self.assertEqual(calls, ['tools.fleet.repairs.maintenance_sync'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['errors'], 1)
        self.assertEqual(result['updated'], 0)
        self.assertEqual(result['maintenance_guard']['status'], 'blocked')
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(list(self.root.glob('.mine-sync-*.partial')))


if __name__ == '__main__':
    unittest.main()
