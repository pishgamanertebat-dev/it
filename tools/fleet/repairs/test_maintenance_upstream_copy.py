"""Owner-selected Share copies with UUID-proven local appends, on private files."""
import json
import openpyxl
import unittest
import uuid
from unittest.mock import patch

from . import entry_service
from . import test_maintenance_report as fixture
from .maintenance_sync import guard_snapshot, MaintenanceSyncBlocked
from .maintenance_baseline import digest
from tools.fleet.maintenance_daily.prepared import source_proof


class UpstreamCopyTests(unittest.TestCase):
    baseline = fixture.MaintenanceReportTests.baseline
    receive = fixture.MaintenanceReportTests.receive
    read = fixture.MaintenanceReportTests.read

    def setUp(self):
        fixture.MaintenanceReportTests.setUp(self)
        settings = json.loads(self.config.read_text('utf8'))
        settings['sync_policy'] = 'upstream_copy'
        self.config.write_text(json.dumps(settings), 'utf8')

    def sync(self):
        return guard_snapshot(self.mine, self.source, config=self.config, runtime=self.runtime)

    def alter_share(self):
        book = openpyxl.load_workbook(self.mine)
        book['710']['A1'] = 'changed title'
        book['710'].sheet_view.zoomScale = 85
        book['710'].page_setup.orientation = 'landscape'
        book['710']['E3'] = 'share historical correction'
        book['710']['A3'] = '1405/08/19'
        book['710']['D3'] = '710'
        book.save(self.mine)
        book.close()

    def test_historical_and_layout_changes_copy_exact_bytes_without_local_reports(self):
        self.alter_share()
        incoming = self.mine.read_bytes()
        result = self.sync()
        self.assertEqual(result['status'], 'imported')
        self.assertEqual(result['hash'], digest(incoming))
        self.assertEqual(self.source.read_bytes(), incoming)
        self.assertEqual(self.sync()['status'], 'unchanged')
        source_proof(digest(incoming), runtime=self.runtime, source=self.source)

    def test_corrections_keep_both_same_sheet_events_and_repeat_without_duplicates(self):
        saved = self.receive('گزارش مکانیکی 19 آبان 1405\n-دامپتراک710: کار بله.')
        self.alter_share()
        for iteration in range(2):
            if iteration:
                book = openpyxl.load_workbook(self.mine)
                book['710']['E3'] = 'second share correction'
                book.save(self.mine);book.close()
            result = self.sync()
            self.assertEqual(result['status'], 'merged')
            self.assertEqual(result['hash'], digest(self.source.read_bytes()))
            self.assertIn(saved['operation'], result['protected_operations'])
            book = self.read()
            self.assertEqual(book['710'].sheet_view.zoomScale, 85)
            self.assertIn('share correction' if iteration else 'historical correction', book['710']['E3'].value)
            self.assertIn('کار بله', book['710']['E4'].value)
            self.assertIsNone(book['710']['E5'].value)
            book.close()
            before = self.source.read_bytes()
            self.assertEqual(self.sync()['status'], 'unchanged')
            self.assertEqual(before, self.source.read_bytes())
            source_proof(digest(before), runtime=self.runtime, source=self.source)

    def test_local_file_is_writable_during_journal_work_and_changed_bytes_stop_replace(self):
        self.alter_share()
        from . import maintenance_baseline as baseline
        check = baseline.check_journal
        def concurrent_save(db):
            with self.source.open('r+b') as writer:
                data = writer.read()
                writer.seek(0);writer.write(data + b'Excel save during processing')
            check(db)
        with patch.object(baseline, 'check_journal', concurrent_save):
            with self.assertRaisesRegex(MaintenanceSyncBlocked, 'SOURCE_CHANGED'):
                self.sync()
        self.assertTrue(self.source.read_bytes().endswith(b'Excel save during processing'))

    def test_unknown_local_change_and_missing_target_still_preserve_both_inputs(self):
        self.receive('گزارش مکانیکی 19 آبان 1405\n-دامپتراک710: کار بله.')
        book = openpyxl.load_workbook(self.mine)
        book.remove(book['710']);book.save(self.mine);book.close()
        before = self.source.read_bytes()
        with self.assertRaises(MaintenanceSyncBlocked): self.sync()
        self.assertEqual(before, self.source.read_bytes())

    def test_concurrent_excel_save_cannot_be_reported_as_unchanged(self):
        from . import maintenance_baseline as baseline
        check = baseline.check_journal
        def concurrent_save(db):
            with self.source.open('ab') as writer: writer.write(b'concurrent Excel save')
            check(db)
        with patch.object(baseline, 'check_journal', concurrent_save):
            with self.assertRaisesRegex(MaintenanceSyncBlocked, 'SOURCE_CHANGED'):
                self.sync()

    def test_manual_form_append_is_also_kept_across_share_corrections(self):
        from . import maintenance_service as service
        book = self.read()
        selected = service.machine(book, '710');book.close()
        request = dict(selected, date=fixture.DAY, mechanic='mechanic', description='manual form entry',
                       parts='test part', operation=uuid.uuid4().hex)
        with patch.object(service, 'permission', return_value=self.settings):
            service.commit('1732374823', request, config=self.config, runtime=self.runtime, day=fixture.DAY)
        self.alter_share()
        self.assertEqual(self.sync()['status'], 'merged')
        book = self.read()
        self.assertEqual(book['710']['E4'].value, 'manual form entry')
        self.assertEqual(book['710']['B4'].value, self.settings['recorder'])
        book.close()

    def test_exact_whole_workbook_returned_to_share_is_not_appended_again(self):
        self.receive('گزارش مکانیکی 19 آبان 1405\n-دامپتراک710: کار بله.')
        self.mine.write_bytes(self.source.read_bytes())
        self.assertEqual(self.sync()['status'], 'unchanged')
        self.alter_share()
        self.assertEqual(self.sync()['status'], 'imported')
        self.assertEqual(self.source.read_bytes(), self.mine.read_bytes())
