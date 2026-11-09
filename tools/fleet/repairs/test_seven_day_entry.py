"""Boundary, concurrency and deterministic UI contracts for the daily window."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

import openpyxl

from . import entry_dates, entry_service as daily, maintenance_service as maintenance
from . import test_entry_service as fixtures, test_entry_bale as ui
from .entry_service import EntryError, EntryAuthorizationDenied


class CalendarTests(unittest.TestCase):
    def test_work_order_OC_GR_AF_dates_still_require_today(self):
        from tools.fleet.work_orders.core import operational
        with patch.object(operational, 'operational_today', return_value='1405/08/18'):
            for kind in ('OIL_CHANGE', 'GREASING', 'AIR_FILTER'):
                operational.require_order({'work_order_type': kind, 'jalali_date': '1405/08/18'})
                for date in ('1405/08/17', '1405/08/11', '1405/08/19'):
                    with self.assertRaises(operational.OperationalDayExpired):
                        operational.require_order({'work_order_type': kind, 'jalali_date': date})

    def test_gateway_form_imports_and_today_need_no_excel_or_model_packages(self):
        script = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'openpyxl', 'jdatetime', 'openai', 'anthropic'}:
            raise AssertionError('Gateway dependency forbidden: ' + fullname)
sys.meta_path.insert(0, Block())
from tools.fleet.repairs import entry_bale, maintenance_bale, entry_dates
assert entry_dates.today().count('/') == 2
assert maintenance_bale.permitted('641220453')
assert entry_bale.permitted('641220453')
'''
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script],
                                cwd=daily.ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_eight_inclusive_dates_and_jalali_month_year_leap_boundaries(self):
        for current, oldest in [('1405/08/18', '1405/08/11'), ('1405/07/03', '1405/06/27'),
                                ('1405/01/03', '1404/12/25'), ('1404/01/02', '1403/12/25')]:
            dates = entry_dates.choices(current)
            self.assertEqual((len(dates), dates[0], dates[-1]), (8, current, oldest))
        self.assertIn('1403/12/30', entry_dates.choices('1404/01/02'))
        with self.assertRaises(ValueError):
            entry_dates.jalali_date('1404/12/30')

    def test_tehran_midnight_independent_of_host_timezone(self):
        for minute, expected in [(29, '1405/08/17'), (30, '1405/08/18')]:
            instant = datetime(2026, 11, 8, 20, minute, tzinfo=timezone.utc)
            with patch.object(entry_dates, 'datetime') as clock:
                clock.now.side_effect = lambda tz: instant.astimezone(tz)
                self.assertEqual(entry_dates.today(), expected)
                self.assertEqual(str(clock.now.call_args.args[0]), 'Asia/Tehran')


class WindowWriterTests(unittest.TestCase):
    book = fixtures.EntryServiceTests.book

    def setUp(self):
        fixtures.EntryServiceTests.setUp(self)
        book = openpyxl.load_workbook(self.source)
        for date in ['1405/06/19', '1405/06/18', '1405/06/25']:
            sheet = book.copy_worksheet(book.worksheets[0])
            sheet.title = 'unrelated-title-' + date.replace('/', '-')
            sheet['C1'] = 'تاریخ: ' + date
        book.save(self.source)
        book.close()

    def request(self, date='1405/06/19', section='mechanical', text='new', **extra):
        return dict(daily.preview('641220453', 'EX332', section, date=date, **self.kw),
                    description=text, write_mode='replace', operation=uuid.uuid4().hex, **extra)

    def save(self, request, **kw):
        return daily.commit('641220453', request, runtime=self.root/'state', **(self.kw | kw))

    def test_today_creates_blank_day_and_seventh_day_uses_header_and_exact_cell(self):
        request = self.request()
        self.assertEqual(request['expected'], 'mechanical-old')
        result = self.save(dict(request, write_mode='append'))
        self.assertEqual(result['sheet'], 'unrelated-title-1405-06-19')
        self.save(self.request(date='1405/06/26'))
        book = openpyxl.load_workbook(self.source)
        try:
            old = book[result['sheet']]
            self.assertEqual(old['D3'].value, 'mechanical-old\nnew')
            self.assertEqual(old['E3'].value, 'metalwork-old')
            today = daily.today_sheet(book, '1405/06/26')
            self.assertEqual(today['D3'].value, 'new')
            self.assertIsNone(today['E3'].value)
            self.assertEqual(book.worksheets[-1]['D3'].value, 'mechanical-old')
        finally:
            book.close()
        with closing(sqlite3.connect(self.root/'state/audit.sqlite3')) as db:
            rows = db.execute('SELECT actor,operation,request,status FROM edits').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({json.loads(row[2])['date'] for row in rows}, {'1405/06/19', '1405/06/26'})
        self.assertTrue(all(row[0] == '641220453' and row[3] == 'succeeded' and len(row[1]) == 32 for row in rows))

    def test_eight_days_future_and_invalid_calendar_fail_before_publication(self):
        original = self.source.read_bytes()
        for date in ['1405/06/18', '1405/06/27', '1405/13/01', '1404/12/30']:
            with self.subTest(date=date):
                with self.assertRaises(EntryError):
                    self.request(date=date)
                request = dict(self.request(), date=date)
                with patch.object(daily, 'replace_source') as replace:
                    with self.assertRaises(EntryError):
                        self.save(request)
                    replace.assert_not_called()
        self.assertEqual(original, self.source.read_bytes())

    def test_missing_historical_sheet_never_clones_and_duplicate_or_ambiguous_stops(self):
        original = self.source.read_bytes()
        with patch.object(daily, 'clone_day', side_effect=AssertionError('historical creation forbidden')):
            with self.assertRaisesRegex(EntryError, 'موجود نیست'):
                self.request(date='1405/06/20')
            with self.assertRaisesRegex(EntryError, 'موجود نیست'):
                self.save(dict(self.request(), date='1405/06/20', expected=''))
        self.assertEqual(original, self.source.read_bytes())
        for ambiguous in [False, True]:
            self.source.write_bytes(original)
            book = openpyxl.load_workbook(self.source)
            sheet = daily.today_sheet(book, '1405/06/19')
            if ambiguous:
                sheet['B1'] = '1405/06/20'
            else:
                book.copy_worksheet(sheet)
            book.save(self.source)
            book.close()
            with self.assertRaises(EntryError):
                self.request()

    def test_explicit_mode_required_and_other_column_independent(self):
        req = self.request()
        original = self.source.read_bytes()
        req.pop('write_mode')
        with self.assertRaisesRegex(EntryError, 'صریح'):
            self.save(req)
        self.assertEqual(original, self.source.read_bytes())
        metal = self.request(section='metalwork', text='weld')
        self.save(dict(req, write_mode='replace'))
        self.save(metal)
        with self.assertRaisesRegex(EntryError, 'تغییر کرده'):
            self.save(dict(req, operation=uuid.uuid4().hex, write_mode='append'))

    def test_expected_is_rechecked_under_lock_and_new_target_row_is_checked(self):
        req = self.request()
        lock = daily.writer_lock
        from contextlib import contextmanager
        @contextmanager
        def changed_before_lock(runtime):
            book = openpyxl.load_workbook(self.source)
            daily.today_sheet(book, req['date'])['D3'] = 'concurrent editor'
            book.save(self.source)
            book.close()
            with lock(runtime):
                yield
        with patch.object(daily, 'writer_lock', changed_before_lock), patch.object(daily, 'replace_source') as replace:
            with self.assertRaisesRegex(EntryError, 'تغییر کرده'):
                self.save(req)
            replace.assert_not_called()

    def test_midnight_before_lock_and_during_save_rejects_without_moving_date(self):
        req = self.request()
        original = self.source.read_bytes()
        with self.assertRaisesRegex(EntryError, 'خارج از بازه'):
            self.save(req, day='1405/06/27')
        instant = ['1405/06/26']
        original_save = openpyxl.workbook.workbook.Workbook.save
        def save_crosses_midnight(book, output):
            original_save(book, output)
            instant[0] = '1405/06/27'
        with patch.object(entry_dates, 'today', side_effect=lambda: instant[0]), \
             patch.object(openpyxl.workbook.workbook.Workbook, 'save', save_crosses_midnight), \
             patch.object(daily, 'replace_source') as replace:
            with self.assertRaisesRegex(EntryError, 'خارج از بازه'):
                self.save(req, day=None)
            replace.assert_not_called()
        self.assertEqual(original, self.source.read_bytes())
        self.assertEqual(req['date'], '1405/06/19')

    def test_newly_missing_yesterday_sheet_is_not_created_after_midnight(self):
        req = self.request(date='1405/06/26')
        original = self.source.read_bytes()
        with self.assertRaisesRegex(EntryError, 'موجود نیست'):
            self.save(req, day='1405/06/27')
        self.assertEqual(original, self.source.read_bytes())

    def test_save_failure_and_interruption_after_replace_recover_same_uuid(self):
        req = self.request()
        original = self.source.read_bytes()
        with patch.object(daily, 'replace_source', side_effect=OSError('save failed')):
            with self.assertRaises(EntryError):
                self.save(req)
        self.assertEqual(original, self.source.read_bytes())
        real_replace = daily.replace_source
        def lost_reply(*args):
            real_replace(*args)
            raise OSError('process interrupted after replace')
        with patch.object(daily, 'replace_source', side_effect=lost_reply):
            with self.assertRaises(EntryError):
                self.save(req)
        written = self.source.read_bytes()
        self.save(req)
        self.save(req)
        self.assertEqual(written, self.source.read_bytes())
        with closing(sqlite3.connect(self.root/'state/audit.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT operation,status FROM edits').fetchall(), [(req['operation'], 'succeeded')])

    def test_append_journal_keeps_full_cell_for_unchanged_sync_contract(self):
        from . import sync_service
        mine = self.root/'mine.xlsx'
        mine.write_bytes(self.source.read_bytes())
        sync_service.initialize(mine, config=self.config, runtime=self.root/'state')
        req = dict(self.request(), write_mode='append')
        self.save(req)
        self.save(req)
        with closing(sqlite3.connect(self.root/'state/audit.sqlite3')) as db:
            stored = json.loads(db.execute('SELECT request FROM edits WHERE operation=?', (req['operation'],)).fetchone()[0])
        self.assertEqual(stored['description'], 'mechanical-old\nnew')
        self.assertEqual(stored['entered_description'], 'new')
        book = openpyxl.load_workbook(mine)
        daily.today_sheet(book, req['date'])['E3'] = 'independent mine metalwork'
        book.save(mine)
        book.close()
        sync_service.merge_snapshot(mine, self.source, config=self.config, runtime=self.root/'state')
        book = openpyxl.load_workbook(self.source)
        try:
            sheet = daily.today_sheet(book, req['date'])
            self.assertEqual(sheet['D3'].value, 'mechanical-old\nnew')
            self.assertEqual(sheet['E3'].value, 'independent mine metalwork')
        finally:
            book.close()


class WindowUITests(ui.EntryBaleTests):
    async def test_date_buttons_pin_absolute_date_and_stale_other_actor_rejected(self):
        await self.deliver(self.event('شرح خرابی'))
        await self.deliver(self.action_event('mechanical'))
        await self.deliver(self.action_event('dates'))
        session = self.handler.sessions[self.key]
        self.assertEqual(session['date_choices'], entry_dates.choices('1405/06/26'))
        buttons = [b for row in self.messages[-1]['reply_markup']['inline_keyboard'] for b in row]
        self.assertEqual(len([b for b in buttons if ':date_' in b['callback_data']]), 8)
        self.assertIn('امروز — ۱۴۰۵/۰۶/۲۶', [b['text'] for b in buttons])
        stolen = self.action_event('date_14050619', actor='455740857')
        await self.deliver(stolen)
        self.assertEqual(session['date'], '1405/06/26')
        old = self.action_event('date_14050619')
        await self.deliver(old)
        self.assertEqual(session['date'], '1405/06/19')
        old.message_id += '-again'
        await self.deliver(old)
        await self.deliver(self.event('710'))
        self.assertEqual(self.calls[-1]['date'], '1405/06/19')
        await self.deliver(self.event('new text'))
        self.assertIn('۱۴۰۵/۰۶/۱۹', self.messages[-1]['text'])
        self.assertIn('previous description', self.messages[-1]['text'])
        await self.deliver(self.event('تأیید'))
        self.assertEqual([p['action'] for p in self.calls], ['dates', 'preview'])
        await self.deliver(self.action_event('append'))
        self.assertEqual(self.calls[-1]['request']['write_mode'], 'append')
        self.assertIn('۱۴۰۵/۰۶/۱۹', self.messages[-1]['text'])

    async def test_midnight_date_callback_never_reinterprets_offset(self):
        await self.deliver(self.event('شرح خرابی'))
        await self.deliver(self.action_event('mechanical'))
        await self.deliver(self.action_event('dates'))
        oldest = self.action_event('date_14050619')
        with patch.object(entry_dates, 'today', return_value='1405/06/27'):
            await self.deliver(oldest)
        self.assertEqual(self.handler.sessions[self.key]['stage'], 'CODE')
        self.assertEqual(self.handler.sessions[self.key]['date'], '1405/06/19')
        # An expired selection stays pinned; the Backend rejects it at preview/commit.
        self.assertEqual([p['action'] for p in self.calls], ['dates'])

    async def test_back_cancel_and_restart_drafts_never_write(self):
        await self.start_description()
        await self.deliver(self.action_event('back'))
        self.assertEqual(self.handler.sessions[self.key]['stage'], 'CODE')
        await self.deliver(self.action_event('dates'))
        await self.deliver(self.action_event('back'))
        self.restart_handler()
        self.assertEqual([p['action'] for p in self.calls], ['preview', 'dates'])
        await self.deliver(self.event('انصراف'))
        self.assertNotIn(self.key, self.handler.sessions)
        self.assertEqual([p['action'] for p in self.calls], ['preview', 'dates'])


if __name__ == '__main__':
    unittest.main()
