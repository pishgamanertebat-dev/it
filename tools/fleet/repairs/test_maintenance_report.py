from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import openpyxl

from tools.fleet.equipment_codes import canonical_input
from . import entry_service, maintenance_service as service, maintenance_report as reports
from .maintenance_report_parse import parse, declared_report
from .maintenance_baseline import initialize, digest
from .maintenance_sync import guard_snapshot, MaintenanceSyncBlocked

DAY='1405/08/20'


class MaintenanceReportTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[3]/'runtime'
        self.temp=tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'maintenance.xlsx';self.runtime=self.root/'journal'
        self.runtime.mkdir();self.config=self.root/'entry.json';self.policy=self.root/'reports.json'
        self.config.write_text(json.dumps({'source':str(self.source),'allowed_users':['1732374823']}),'utf8')
        self.policy.write_text('{}','utf8')
        self.settings=dict(source=str(self.source),recorder='محسن کرمعلی',department='تعمیرگاه',mechanic='اکیپ مکانیکی',scope='all')
        self.permission=patch.object(reports,'permission',return_value=self.settings)
        self.permission.start();self.addCleanup(self.permission.stop)
        book=openpyxl.Workbook();book.remove(book.active)
        for code in ['710','705','802','1251','1252','1253','1254','1255','1256','متفرقه']:
            sheet=book.create_sheet(code);sheet['A1']='بیل '+code if code.startswith('12') else code
            headers=['تاریخ','ثبت کننده','نام مکانیک','کد مکانیزم','نوع خرابی','قطعات مصرفی']
            if code=='متفرقه':headers=['تاریخ','ثبت کننده','قسمت','نام مکانیک','نوع خرابی','قطعات مصرفی']
            if code in ('705','802'):headers=['تاریخ','ثبت کننده','تعمیرکار' if code=='705' else 'کد مکانیزم','کد مکانیزم' if code=='705' else 'نام مکانیک','نوع خرابی','قطعات مصرفی']
            sheet.append(headers)
            for column in ['A','B','C','D','E','F']:sheet.column_dimensions[column].width=38
            sheet.print_area='A1:F2'
        book.save(self.source);book.close()
        self.kw=dict(config=self.policy,entry_config=self.config,runtime=self.runtime,day=DAY,ledger=self.root/'no-ledger.db')
        self.baseline()

    def receive(self,raw,source='bale:1',actor='1732374823'):
        return reports.receive(actor,actor,source,raw,**self.kw)

    def read(self):return openpyxl.load_workbook(self.source)

    def baseline(self):
        if hasattr(self,'mine'):return
        self.mine=self.root/'mine.xlsx';self.mine.write_bytes(self.source.read_bytes())
        with closing(entry_service.open_audit(self.runtime)):pass
        initialize(self.mine,expected_hash=digest(self.mine.read_bytes()),decision='fixture owner baseline',
                   approved_by='project-owner',config=self.config,runtime=self.runtime)

    def test_all_six_aliases_and_unrelated_identifiers(self):
        for n in range(1,7):
            for value in [str(1000+n),'EX'+str(1000+n),'بیل '+str(1000+n),str(1000+n).translate(str.maketrans('0123456789','۰۱۲۳۴۵۶۷۸۹'))]:
                self.assertEqual(canonical_input(value),'EX'+str(1250+n))
        for value in ['HD1001','1007','part 1001','11001']:
            self.assertEqual(canonical_input(value),value)

    def test_aliases_raw_spans_groups_date_and_miscellaneous(self):
        raw='گزارش مکانیکی 19 آبان 1405\n-بیل1005: بستن تسمه.\n-بیل ۱۰۰۵: بستن واترپمپ.\n-تعمیر ابزار کارگاه'
        result=self.receive(raw)
        self.assertEqual(len(result['records']),2)
        book=self.read()
        self.assertEqual(book['1255']['B3'].value,'محسن کرمعلی')
        self.assertEqual(book['1255']['C3'].value,'اکیپ مکانیکی')
        self.assertEqual(book['1255']['D3'].value,'1255')
        self.assertIn('1255',book['1255']['E3'].value)
        self.assertNotIn('1005',book['1255']['E3'].value)
        self.assertEqual(book['متفرقه']['C3'].value,'تعمیرگاه')
        self.assertEqual(book['متفرقه']['D3'].value,'اکیپ مکانیکی')
        book.close()
        parsed=parse(raw,DAY)
        for item in parsed['items']:self.assertEqual(raw[item['span']['start']:item['span']['end']],item['raw'])
        with closing(sqlite3.connect(self.runtime/'audit.sqlite3')) as db:
            saved=json.loads(db.execute('select request from edits').fetchone()[0])
            self.assertEqual(saved['raw_text'],raw)

    def test_retry_same_message_never_appends_and_changed_message_keeps_both_versions(self):
        raw='گزارش مکانیکی 19 آبان 1405\n-بیل1003: تعویض شلنگ.'
        first=self.receive(raw);before=self.source.read_bytes()
        self.assertEqual(self.receive(raw),first)
        self.assertEqual(before,self.source.read_bytes())
        with self.assertRaisesRegex(ValueError,'نسخه‌ها'):
            self.receive(raw+' متن جدید')
        self.assertEqual(before,self.source.read_bytes())
        with closing(sqlite3.connect(self.runtime/'audit.sqlite3')) as db:
            self.assertEqual(db.execute('select count(*) from maintenance_report_messages').fetchone()[0],2)

    def test_unknown_code_prevents_entire_batch_and_preserves_raw(self):
        raw='گزارش مکانیکی 19 آبان 1405\n-بیل1003: تعمیر.\n-بیل999: تعمیر دوم.'
        before=self.source.read_bytes()
        with self.assertRaises(ValueError):self.receive(raw)
        self.assertEqual(before,self.source.read_bytes())
        with closing(sqlite3.connect(self.runtime/'audit.sqlite3')) as db:
            self.assertEqual(db.execute('select raw_text from maintenance_report_messages').fetchone()[0],raw)
            self.assertEqual(db.execute('select count(*) from edits').fetchone()[0],0)

    def test_second_append_failure_is_atomic_and_final_permission_is_rechecked(self):
        raw='گزارش مکانیکی 19 آبان 1405\n-بیل1003: تعمیر.\n-بیل1005: تعمیر دوم.'
        before=self.source.read_bytes();append=service.append_record;calls=[]
        def fail_second(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise ValueError('second device failed')
            return append(*args,**kwargs)
        with patch.object(service,'append_record',side_effect=fail_second):
            with self.assertRaises(ValueError):self.receive(raw)
        self.assertEqual(before,self.source.read_bytes())
        self.permission.stop()
        with patch.object(reports,'permission',side_effect=[self.settings,self.settings,PermissionError('revoked')]):
            with self.assertRaises(PermissionError):self.receive(raw)
        self.assertEqual(before,self.source.read_bytes())
        self.permission.start()

    def test_identical_text_with_new_message_is_retained_for_review(self):
        raw='گزارش مکانیکی 19 آبان 1405\n-بیل1003: تعمیر.'
        self.receive(raw);before=self.source.read_bytes()
        with self.assertRaisesRegex(ValueError,'متن یکسان'):self.receive(raw,'bale:2')
        self.assertEqual(before,self.source.read_bytes())

    def test_sadeghian_only_uncoded_part_and_coded_text_survives_for_legacy_route(self):
        self.settings.update(scope='miscellaneous',department='آهنگری',mechanic='اکیپ آهنگری',recorder='صادق صادقیان')
        raw='گزارش آهنگری 19 آبان 1405\n-بیل1003: جوشکاری.\n-جوشکاری میز کارگاه'
        result=self.receive(raw,actor='387679249')
        self.assertEqual(len(result['records']),1)
        self.assertIn('بیل1003',result['remaining_text'])
        book=self.read()
        self.assertIsNone(book['1253']['A3'].value)
        self.assertEqual(book['متفرقه']['C3'].value,'آهنگری')
        self.assertEqual(book['متفرقه']['D3'].value,'اکیپ آهنگری');book.close()

    def test_inspected_reversed_705_columns_write_code_and_mechanic_correctly(self):
        self.receive('گزارش مکانیکی 19 آبان 1405\n-دامپتراک 705: تعمیر.')
        book=self.read()
        self.assertEqual(book['705']['C3'].value,'705')
        self.assertEqual(book['705']['D3'].value,'اکیپ مکانیکی');book.close()

    def test_inspected_802_blank_recorder_header_still_records_verified_name(self):
        book=self.read();sheet=book['802']
        sheet.delete_rows(1,sheet.max_row)
        sheet['A2']='بیل 802'
        for col,label in [(1,'تاریخ'),(3,'کد مکانیزم'),(4,'نام مکانیک'),(5,'نوع خرابی'),(6,'قطعات مصرفی')]:
            sheet.cell(3,col,label)
        book.save(self.source);book.close()
        self.runtime=self.root/'802-journal';self.runtime.mkdir();self.kw['runtime']=self.runtime
        with closing(entry_service.open_audit(self.runtime)):pass
        self.mine=self.root/'802-mine.xlsx';self.mine.write_bytes(self.source.read_bytes())
        initialize(self.mine,expected_hash=digest(self.mine.read_bytes()),decision='fixture inspected 802 schema',
                   approved_by='project-owner',config=self.config,runtime=self.runtime)
        self.receive('گزارش مکانیکی 19 آبان 1405\n-بیل802: تعمیر.')
        book=self.read()
        self.assertEqual(book['802']['B4'].value,'محسن کرمعلی')
        self.assertEqual(book['802']['C4'].value,'اکیپ مکانیکی')
        self.assertEqual(book['802']['D4'].value,'802');book.close()

    def test_future_old_dates_and_multiple_device_clauses_do_not_write(self):
        before=self.source.read_bytes()
        for raw in ['گزارش مکانیکی 21 آبان 1405\n-بیل1003: تعمیر.',
                    'گزارش مکانیکی 1 آبان 1405\n-بیل1003: تعمیر.',
                    'گزارش مکانیکی 19 آبان 1405\n-بیل1003 و بیل1005: تعمیر.']:
            with self.assertRaises(ValueError):self.receive(raw,'bale:'+str(len(raw)))
        self.assertEqual(before,self.source.read_bytes())
        self.assertFalse(declared_report('گزارش ۱۸ آبان از نظر من قابل تایید است'))

    def test_same_sheet_sync_carries_only_proven_reports_without_duplication(self):
        self.baseline()
        raw='گزارش مکانیکی 19 آبان 1405\n-بیل1003: تعمیر.'
        original=self.receive(raw)
        book=openpyxl.load_workbook(self.mine)
        service.append_record(book,dict(sheet='1253',code='1253',canonical='EX1253',date='1405/08/20',
                              mechanic='mine mechanic',description='upstream append',parts=''),recorder='mine actor')
        book.save(self.mine);book.close()
        result=guard_snapshot(self.mine,self.source,config=self.config,runtime=self.runtime)
        self.assertEqual(result['merge_strategy'],'journalled_report_append_v1')
        self.assertEqual(guard_snapshot(self.mine,self.source,config=self.config,runtime=self.runtime)['status'],'unchanged')
        self.assertEqual(self.receive(raw),original)
        book=self.read()
        self.assertEqual(book['1253']['E3'].value,'upstream append')
        self.assertEqual(book['1253']['E4'].value,'-بیل1253: تعمیر.');book.close()
        # A local row which is absent from the UUID journal must never be carried.
        book=self.read();book['1253']['E5']='untracked';book.save(self.source);book.close()
        before=self.source.read_bytes()
        with self.assertRaises(MaintenanceSyncBlocked):guard_snapshot(self.mine,self.source,config=self.config,runtime=self.runtime)
        self.assertEqual(before,self.source.read_bytes())


if __name__=='__main__':unittest.main()
