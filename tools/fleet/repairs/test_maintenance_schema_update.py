"""Reviewed template migration cannot turn ordinary Sync into an overwrite path."""
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch

import openpyxl

from . import entry_service, maintenance_service
from .maintenance_baseline import accept_reviewed_upstream, digest, initialize
from .maintenance_sync import guard_snapshot, MaintenanceSyncBlocked
from . import test_maintenance_service as fixtures


class ReviewedMaintenanceSchemaTests(unittest.TestCase):
    setUp = fixtures.MaintenanceServiceTests.setUp
    request = fixtures.MaintenanceServiceTests.request
    save = fixtures.MaintenanceServiceTests.save

    def setup_boundary(self):
        self.mine = self.root / 'mine.xlsx'
        self.mine.write_bytes(self.source.read_bytes())
        self.args = dict(config=self.config, runtime=self.root / 'journal')
        self.args['runtime'].mkdir()
        with closing(entry_service.open_audit(self.args['runtime'])):
            pass
        self.before = digest(self.source.read_bytes())
        initialize(self.mine, expected_hash=self.before, approved_by='project-owner',
                   decision='original owner-approved baseline', **self.args)

    def update_schema(self):
        book = openpyxl.load_workbook(self.mine)
        for sheet in book:
            sheet.insert_cols(2)
            sheet.cell(2, 2, 'ثبت کننده')
        book.save(self.mine)
        book.close()

    def accept(self, **changes):
        options = dict(local_hash=self.before, incoming_hash=digest(self.mine.read_bytes()),
                       decision='Owner requests current template with recorder column',
                       approved_by='project-owner', **self.args)
        return accept_reviewed_upstream(self.mine, **dict(options, **changes))

    def test_misc_department_header_and_machine_rejection(self):
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = 'متفرقه'
        sheet.append(['تاریخ', 'ثبت کننده', 'قسمت', 'نام مکانیک', 'نوع خرابی', 'قطعات مصرفی'])
        self.assertEqual(maintenance_service.layout(sheet)[1]['code'], 3)
        sheet.title = '710'
        with self.assertRaises(entry_service.EntryError):
            maintenance_service.layout(sheet)
        book.close()

    def test_reviewed_import_retains_exact_bytes_and_original_baseline(self):
        self.setup_boundary()
        self.update_schema()
        incoming = self.mine.read_bytes()
        with self.assertRaises(MaintenanceSyncBlocked):
            guard_snapshot(self.mine, self.source, **self.args)
        result = self.accept()
        self.assertEqual(result['status'], 'imported')
        self.assertEqual(self.source.read_bytes(), incoming)
        self.assertEqual(result['reviewed_change']['incoming_hash'], digest(incoming))
        with closing(entry_service.open_audit(self.args['runtime'])) as db:
            self.assertEqual(db.execute('select count(*) from maintenance_baseline_events').fetchone()[0], 1)
            self.assertEqual(db.execute('select count(*) from edits').fetchone()[0], 1)
            self.assertEqual(db.execute('pragma integrity_check').fetchone()[0], 'ok')
        self.assertEqual(guard_snapshot(self.mine, self.source, **self.args)['status'], 'unchanged')

    def test_stale_review_and_non_owner_cannot_publish(self):
        self.setup_boundary()
        self.update_schema()
        original = self.source.read_bytes()
        for options, error in [({'local_hash': '0' * 64}, entry_service.EntryError),
                               ({'incoming_hash': '0' * 64}, entry_service.EntryError),
                               ({'approved_by': '654806764'}, PermissionError),
                               ({'decision': ''}, PermissionError)]:
            with self.subTest(options=options), self.assertRaises(error):
                self.accept(**options)
            self.assertEqual(self.source.read_bytes(), original)

    def test_local_writer_branch_is_never_overwritten(self):
        self.setup_boundary()
        self.save(self.request())
        local = self.source.read_bytes()
        self.update_schema()
        with self.assertRaisesRegex(entry_service.EntryError, 'LOCAL_BRANCH'):
            self.accept(local_hash=digest(local))
        self.assertEqual(self.source.read_bytes(), local)

    def test_ordinary_append_after_migration_imports_and_history_edit_still_stops(self):
        self.setup_boundary()
        self.update_schema()
        self.accept()
        book = openpyxl.load_workbook(self.mine)
        sheet = book['710']
        sheet.append(['1405/06/26', 'actor', 'mechanic', '710', 'new upstream event', 'none'])
        book.save(self.mine)
        book.close()
        self.assertEqual(guard_snapshot(self.mine, self.source, **self.args)['status'], 'imported')
        original = self.source.read_bytes()
        book = openpyxl.load_workbook(self.mine)
        book['710']['E3'] = 'historic edit still requires review'
        book.save(self.mine)
        book.close()
        with self.assertRaises(MaintenanceSyncBlocked):
            guard_snapshot(self.mine, self.source, **self.args)
        self.assertEqual(self.source.read_bytes(), original)

    def test_changed_sheet_list_and_broken_chain_are_rejected(self):
        self.setup_boundary()
        book = openpyxl.load_workbook(self.mine)
        book.remove(book['710'])
        book.save(self.mine)
        book.close()
        original = self.source.read_bytes()
        with self.assertRaisesRegex(entry_service.EntryError, 'SHEET_LIST_CHANGED'):
            self.accept()
        self.assertEqual(self.source.read_bytes(), original)
        with closing(sqlite3.connect(self.args['runtime'] / 'audit.sqlite3')) as db:
            db.execute("insert into edits values ('bad','bad','actor',?, '{}','a','b','x','prepared','{}','now')",
                       (str(self.source),))
            db.commit()
        with self.assertRaises(entry_service.EntryError):
            self.accept()

    def test_recorder_comes_from_approved_identity_and_retry_keeps_one_row(self):
        self.setup_boundary()
        self.update_schema()
        self.accept()
        request=self.request(recorder='untrusted name')
        settings={'source':str(self.source),'recorder':'approved actor'}
        with patch.object(maintenance_service,'permission',return_value=settings):
            result=self.save(request)
            saved=self.source.read_bytes()
            self.assertEqual(self.save(request),result)
        self.assertEqual(saved,self.source.read_bytes())
        book=openpyxl.load_workbook(self.source)
        self.assertEqual(book['710'].cell(result['row'],2).value,'approved actor')
        self.assertEqual(result['recorder'],'approved actor')
        book.close()

    def test_missing_recorder_label_in_updated_template_does_not_publish_blank_name(self):
        self.setup_boundary()
        self.update_schema()
        self.accept()
        book=openpyxl.load_workbook(self.source)
        book['710']['B2']=None
        book.save(self.source)
        book.close()
        original=self.source.read_bytes()
        request=self.request()
        with patch.object(maintenance_service,'permission',return_value={'source':str(self.source),'recorder':'actor'}):
            with self.assertRaisesRegex(entry_service.EntryError,'ثبت‌کننده'):
                self.save(request)
        self.assertEqual(self.source.read_bytes(),original)


if __name__ == '__main__':
    unittest.main()
