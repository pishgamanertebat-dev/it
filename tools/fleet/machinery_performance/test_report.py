"""Synthetic month rollover, partial days, raw averages and real PDF cell checks."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from contextlib import contextmanager

import openpyxl

from tools.fleet.oil_balance.test_report import package
from tools.scheduler.report_errors import ReportFailure
from .report import DAILY_COLUMNS, TOTAL_COLUMNS, scan, select
from .render import render, validate_pdf
from integrations.hermes.function_domain.scoped import ScopedReader,ScopeDenied
from tools.authorization.store import FunctionScope

TARGET = '1405/08/11'


def workbook(month='8'):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = month
    sheet['B1'], sheet['C1'] = 'نوع دستگاه', 'کد دستگاه'
    for day, start in ((10,4),(11,14),(12,24)):
        for i,label in enumerate(DAILY_COLUMNS):
            sheet.cell(2,start+i,label)
            sheet.cell(3,start+i,day)
    for i,label in enumerate(TOTAL_COLUMNS):
        sheet.cell(1,34+i,label)
    for r,name,code in ((4,'بیل مکانیکی',601),(5,'لودر',601),(6,'دامپتراک',714)):
        sheet.cell(r,2,name);sheet.cell(r,3,code)
        for start in (4,14):
            values=[11,10,7,3,2,1,1,0,400,None]
            for i,v in enumerate(values):
                sheet.cell(r,start+i,v)
        values=[110,100,70,30,20,10,10,0,4000,40,36]
        for i,v in enumerate(values):
            sheet.cell(r,34+i,v)
    return book


class PerformanceReportTests(unittest.TestCase):
    def report(self, book=None, target=TARGET, caches=None):
        book = book or workbook()
        try:
            return scan(package(book,caches),target)
        finally:
            book.close()

    def test_exact_month_rollover_and_no_latest_fallback(self):
        self.assertEqual(len(self.report(workbook('9'),'1405/09/11')['rows']),3)
        self.assertEqual(self.report(workbook('8'),'1405/09/11')['rows'],[])
        with self.assertRaises(ValueError):
            self.report(target='1404/08/11')

    def test_future_template_date_is_missing_despite_populated_summary(self):
        report=self.report(target='1405/08/12')
        self.assertEqual(report['rows'],[])
        self.assertEqual(report['missing_codes'],['601','601','714'])

    def test_one_partial_machine_blocks_daily_report_but_not_other_machine_query(self):
        book=workbook();s=book.active
        for c in (16,17,20,21):s.cell(6,c).value=None
        report=self.report(book)
        self.assertEqual(report['missing_codes'],['714'])
        queried=select(report,mode='daily',comparison='average_gt_limit',machine_code='601')
        self.assertEqual(queried['missing_codes'],[])
        self.assertTrue(queried['ambiguous_code'])
        self.assertEqual([r['name'] for r in queried['rows']],['بیل مکانیکی','لودر'])

    def test_explicit_zero_records_machine_state(self):
        book=workbook();s=book.active
        for c in (16,17,20,21):s.cell(6,c).value=None
        s.cell(6,20,0)
        self.assertFalse(self.report(book)['missing_codes'])

    def test_daily_query_and_monthly_push_use_different_values(self):
        report=self.report()
        daily=select(report,mode='daily',comparison='average_gt_limit')
        monthly=select(report,mode='monthly',comparison='average_gt_limit')
        self.assertEqual(daily['rows'][0]['values'][1],10)
        self.assertEqual(monthly['rows'][0]['values'][1],100)
        self.assertEqual(daily['rows'][0]['values'][9],40)

    def test_alert_uses_unrounded_value_and_strict_greater_than(self):
        book=workbook();s=book.active
        s.cell(4,43,36.001);s.cell(4,43).number_format='0'
        s.cell(5,43,36);s.cell(6,43,35.999)
        report=select(self.report(book),mode='monthly',comparison='average_gt_limit')
        self.assertEqual([r['alarm'] for r in report['rows']],[True,False,False])
        self.assertEqual(report['rows'][0]['display'][9],'36')

    def test_unsaved_or_stale_formula_stops_instead_of_recalculating(self):
        for cache in (None,99):
            book=workbook();book.active.cell(4,35,'=AJ4+AK4')
            with self.assertRaises(ReportFailure):
                self.report(book,caches={'AI4':cache} if cache is not None else None)

    def test_negative_and_boolean_raw_state_are_rejected(self):
        for value in (-1,True):
            book=workbook();book.active.cell(4,16,value)
            with self.assertRaises(ReportFailure):self.report(book)

    def test_raw_codes_are_not_guessed_from_aliases(self):
        report=self.report()
        with self.assertRaises(ValueError):
            select(report,mode='daily',comparison='average_gt_limit',machine_code='HD714')

    def test_pdf_contains_all_source_cells_and_red_rows(self):
        import fitz
        report=select(self.report(),mode='monthly',comparison='average_gt_limit')
        root=Path('runtime/machinery-performance-tests');root.mkdir(exist_ok=True,parents=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            pdf=render(report,Path(tmp)/'report.pdf')
            result=validate_pdf(pdf,report)
            self.assertEqual(result['rows'],3)
            with fitz.open(pdf) as doc:
                self.assertTrue(any(d.get('fill') and d['fill'][0]>.99 and .87<d['fill'][1]<.89 for d in doc[0].get_drawings()))
            # 714 exists elsewhere on the page; validation must check its cell.
            report['pdf_cells'][1]['values'][1]='714'
            with self.assertRaises(ReportFailure):validate_pdf(pdf,report)

    def test_pin_preserves_original_bytes_and_detects_corruption(self):
        from . import delivery
        report=select(self.report(),mode='monthly',comparison='average_gt_limit')
        root=Path('runtime/machinery-performance-tests');root.mkdir(exist_ok=True,parents=True)
        @contextmanager
        def source():yield b'fixture',report['source_sha256']
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            out=Path(tmp);pdf=render(report,out/'generated.pdf')
            with patch.object(delivery,'build',return_value=dict(status='ready',report=report,pdf=str(pdf))) as builder,patch.object(delivery,'source_snapshot',source):
                first=delivery.prepare(TARGET,outbox=out/'outbox')
                original=Path(first['pdf']).read_bytes()
                second=delivery.prepare(TARGET,outbox=out/'outbox')
                self.assertEqual(Path(second['pdf']).read_bytes(),original)
                builder.assert_called_once()
            Path(second['pdf']).write_bytes(b'corrupt')
            with self.assertRaises(ReportFailure):delivery.pinned(TARGET,outbox=out/'outbox')

    def test_exact_date_worker_query_uses_only_its_file_scope(self):
        from integrations.hermes.function_domain.worker import run
        from .report import SOURCE_NAME
        root=Path('runtime/machinery-performance-tests');root.mkdir(exist_ok=True,parents=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            folder=Path(tmp);(folder/SOURCE_NAME).write_bytes(package(workbook()))
            store=Mock();store.function_scope.return_value=FunctionScope(False,(SOURCE_NAME,))
            request=dict(operation='report',args=dict(kind='machinery_performance',date=TARGET,machine_code='714'),
                         identity=dict(platform='bale',chat_type='dm',user_id='101',chat_id='101'))
            result=run(request,store=store,reader=ScopedReader(folder),cache=folder/'cache')['result']
            self.assertEqual(result['devices'][0]['metrics']['مجموع کارکرد'],10)
            self.assertEqual(result['mode'],'daily')
            store.function_scope.return_value=FunctionScope(False,('تعمیرات 1405.xlsx',))
            with self.assertRaises(PermissionError):run(request,store=store,reader=ScopedReader(folder),cache=folder/'cache')
            request['identity']['chat_id']='999'
            with self.assertRaises(PermissionError):run(request,store=store,reader=ScopedReader(folder),cache=folder/'cache')

    def test_saved_snapshot_never_widens_file_or_path_scope(self):
        from .report import SOURCE_NAME,source_snapshot
        root=Path('runtime/machinery-performance-tests');root.mkdir(exist_ok=True,parents=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            folder=Path(tmp);data=package(workbook());(folder/SOURCE_NAME).write_bytes(data)
            reader=ScopedReader(folder,allowed_files=[SOURCE_NAME])
            self.assertEqual(reader.stable_snapshot(SOURCE_NAME),data)
            for name in ('other.xlsx','../source.xlsx'):
                with self.assertRaises(ScopeDenied):reader.stable_snapshot(name)
            with patch.object(reader,'stable_snapshot',side_effect=[data,b'changed']):
                with self.assertRaises(ReportFailure):
                    with source_snapshot(reader):pass


if __name__=='__main__':unittest.main()
