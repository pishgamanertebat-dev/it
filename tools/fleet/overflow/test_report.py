from __future__ import annotations

import ast
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import openpyxl

from .bale import OverflowHandler, ROOT, build_report
from .report import ReportError, load_report, parse_command, render_report, validate_date


class Reports(unittest.TestCase):
    def setUp(self):
        runtime = ROOT / 'runtime/overflow-tests'
        runtime.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime)
        self.assertEqual(Path(self.temp.name).resolve().parent, runtime.resolve())
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source.xlsx'

    def workbook(self, dates):
        workbook = openpyxl.Workbook()
        workbook.remove(workbook.active)
        for index, date in enumerate(dates):
            sheet = workbook.create_sheet(f'sheet-{index}')
            sheet.append(['لیست سرریز روزانه ماشین آلات', None, None, 'تاریخ:', date])
            sheet.append(['ردیف', 'نام دستگاه', 'کد دستگاه', 'روغن موتور', 'توضیحات'])
            sheet.append([1, 'لودر', 472, 0, '<b>نشتی & توضیح</b>'])
            sheet.append([2, 'لودر', 472, None, None])
            sheet.append(['جمع', None, None, 0])
        workbook.save(self.source)
        workbook.close()

    def test_command_variants_and_dates(self):
        for command in ['سرریز', 'سر ریز روزانه', 'سرریز روزانه میخواهم', 'سر\u200cریز']:
            self.assertEqual(parse_command(command), (True, None))
        for command in ['سرریز ۱۴۰۵/۶/۱۴', 'سرريز روزانه ١٤٠٥/٠٦/١٤', 'سرریز تاریخ 1405-06-14']:
            self.assertEqual(parse_command(command), (True, '1405/06/14'))
        for command in ['حکم کار', 'HD714', 'سرریزها', 'وضعیت سرویس', '1405/06/14']:
            self.assertEqual(parse_command(command), (False, None))

    def test_invalid_dates(self):
        for date in ['1405/0/6/14', '1405/13/01', '1405/07/31', '1405/12/30', '1404/12/30', '1405/06/00']:
            with self.assertRaises(ReportError):
                parse_command('سرریز ' + date)
        self.assertEqual(validate_date('1403/12/30'), '1403/12/30')

    def test_latest_is_full_date_and_updates_are_visible(self):
        self.workbook(['1405/06/14', '1404/12/29', '1405/06/15'])
        self.assertEqual(load_report(source=self.source)['date'], '1405/06/15')
        self.workbook(['1405/06/16', '1405/06/15'])
        self.assertEqual(load_report(source=self.source)['date'], '1405/06/16')

    def test_history_preserves_duplicate_machine_rows_blanks_and_zero(self):
        self.workbook(['1405/06/15', '1404/01/18'])
        report = load_report('1404/1/18', self.source)
        self.assertEqual(report['date'], '1404/01/18')
        self.assertEqual(len(report['rows']), 2)
        self.assertEqual(report['rows'][0][3], 0)
        self.assertIsNone(report['rows'][1][3])
        self.assertEqual(report['totals'][3], 0)

    def test_missing_date_and_ambiguous_date_never_fall_back(self):
        self.workbook(['1405/06/15', '1405/06/15'])
        with self.assertRaisesRegex(ReportError, 'چند شیت'):
            load_report(source=self.source)
        with self.assertRaisesRegex(ReportError, 'ثبت نشده'):
            load_report('1405/06/14', self.source)
        with self.assertRaisesRegex(ReportError, 'در دسترس'):
            load_report(source=self.source.with_name('missing.xlsx'))

    def test_render_multiple_pages(self):
        self.workbook(['1405/06/15'])
        report = load_report(source=self.source)
        report['rows'] *= 10
        paths = render_report(report, Path(self.temp.name) / 'images')
        self.assertEqual(len(paths), 2)
        for path in paths:
            self.assertEqual(Path(path).read_bytes()[:8], b'\x89PNG\r\n\x1a\n')


def event(text='سرریز', platform='bale', chat_type='dm', message_id='1'):
    return SimpleNamespace(text=text, message_id=message_id,
                           source=SimpleNamespace(platform=platform, chat_type=chat_type,
                                                  user_id='test-user', chat_id='test-user'))


def allowed_authorization():
    auth = Mock()
    auth.can_read_overflow.return_value = True
    return auth


class Routing(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_error_routing(self):
        worker = AsyncMock(return_value={'ok': False, 'message': 'گزارش موجود نیست'})
        handler = OverflowHandler(worker, allowed_authorization())
        adapter = SimpleNamespace(send=AsyncMock())
        gateway = SimpleNamespace(adapters={'bale': adapter})
        sent = []
        send = lambda g, c, t: sent.append(t)
        self.assertIsNone(handler.handle(event('حکم کار'), gateway, send=send))
        self.assertIsNone(handler.handle(event(platform='telegram'), gateway, send=send))
        self.assertIsNone(handler.handle(event(chat_type='group'), gateway, send=send))
        result = handler.handle(event(), gateway, send=send)
        self.assertEqual(result['action'], 'skip')
        self.assertEqual(handler.handle(event(), gateway, send=send)['reason'], 'overflow-duplicate')
        await asyncio.gather(*handler.tasks)
        worker.assert_awaited_once()
        adapter.send.assert_awaited_once_with('test-user', 'گزارش موجود نیست')
        handler.handle(event('سرریز 1405/0/6/14'), gateway, send=send)
        self.assertIn('قالب تاریخ', sent[-1])

    async def test_worker_failure_is_handled(self):
        handler = OverflowHandler(AsyncMock(side_effect=RuntimeError('test error')), allowed_authorization())
        adapter = SimpleNamespace(send=AsyncMock())
        with self.assertLogs('tools.fleet.overflow.bale', level='ERROR'):
            handler.handle(event(), SimpleNamespace(adapters={'bale': adapter}), send=lambda *a: None)
            await asyncio.gather(*handler.tasks)
        self.assertFalse(handler.busy)
        adapter.send.assert_awaited_once()

    async def test_real_workbook_and_worker_with_fake_bale_transport(self):
        from .report import DEFAULT_SOURCE
        if not DEFAULT_SOURCE.exists():
            self.skipTest('Operational workbook is not available on this machine')
        received = []
        async def photo(**kwargs):
            received.append((kwargs['caption'], kwargs['photo'].read(8)))
        adapter = SimpleNamespace(send=AsyncMock(), _bot=SimpleNamespace(send_photo=photo))
        handler = OverflowHandler(build_report, allowed_authorization())
        handler.handle(event('سرریز 1405/06/14'), SimpleNamespace(adapters={'bale': adapter}), send=lambda *a: None)
        await asyncio.gather(*handler.tasks)
        self.assertEqual(received[0][1], b'\x89PNG\r\n\x1a\n')
        self.assertIn('1405/06/14', received[0][0])
        adapter.send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
