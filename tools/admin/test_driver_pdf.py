"""PDF delivery fixtures; no production Bale sends or workbook changes."""
from pathlib import Path
import io,unittest
from unittest.mock import patch
import fitz,openpyxl
from tools.admin.test_admin1 import AdminFixture
from integrations.hermes.function_domain.driver_report import build_driver_pdf,SOURCE_NAME,SECTIONS
from tools.scheduler.tasks import driver_daily

class DriverPDFTests(AdminFixture,unittest.TestCase):
    def exporter(self,source,sheet,output,directory,columns):
        self.assertEqual(Path(source).read_bytes(),self.original)
        book=openpyxl.load_workbook(source,read_only=True,data_only=True)
        try:
            values=[str(row[c-1]) for row in book[sheet].iter_rows(values_only=True) for c in columns if c<=len(row) and row[c-1] is not None]
        finally:book.close()
        doc=fitz.open();page=doc.new_page();page.insert_text((30,30),' | '.join(''.join(c for c in v if ord(c)<128) for v in values));doc.save(output);doc.close()
    def test_native_export_exact_sheet_isolated_columns_one_snapshot_and_no_source_write(self):
        self.workbook([('newest','1405/07/12','TODAY_MECH','TODAY_METAL'),('requested','1405/07/10','MECH_ONLY','METAL_ONLY'),('oldlast','1405/07/09','OLD_MECH','OLD_METAL')])
        self.original=(self.data/SOURCE_NAME).read_bytes()
        with patch('tools.fleet.repairs.report.export_pdf',side_effect=self.exporter) as export:
            result=build_driver_pdf('1405/07/10',self.directory/'pdf',self.reader)
        self.assertEqual(len(result['documents']),2);self.assertEqual(result['report']['sheet'],'requested')
        self.assertEqual([call.args[-1] for call in export.call_args_list],[[1,2,3,4],[1,2,3,5]])
        for section,p in zip(SECTIONS,result['documents']):
            with fitz.open(p) as doc:
                text=''.join(page.get_text() for page in doc)
                self.assertEqual(doc[0].get_images(),[])
            self.assertIn('1405/07/10',text)
            self.assertNotIn('TODAY_',text);self.assertNotIn('OLD_',text)
            self.assertIn('MECH_ONLY' if section=='mechanical' else 'METAL_ONLY',text)
            self.assertNotIn('METAL_ONLY' if section=='mechanical' else 'MECH_ONLY',text)
        self.assertEqual((self.data/SOURCE_NAME).read_bytes(),self.original)
        self.assertFalse(list((self.directory/'pdf').glob('driver-excel-*')))
    def test_missing_exact_day_two_searchable_vector_notices_never_export_old_sheet(self):
        self.workbook([('old','1405/07/09','OLD_MECH','OLD_METAL')])
        with patch('tools.fleet.repairs.report.export_pdf') as export:
            result=build_driver_pdf('1405/07/10',self.directory/'pdf',self.reader)
        export.assert_not_called()
        for p in result['documents']:
            with fitz.open(p) as doc:
                text=''.join(page.get_text() for page in doc)
                self.assertIn('1405/07/10',text);self.assertNotIn('OLD_',text);self.assertEqual(doc[0].get_images(),[])
        self.assertTrue(all(s['status']=='date_missing' for s in result['report']['sections'].values()))
    def test_empty_section_notice_other_section_native_excel_pdf(self):
        self.workbook([('requested','1405/07/10',None,'METAL_ONLY')]);self.original=(self.data/SOURCE_NAME).read_bytes()
        with patch('tools.fleet.repairs.report.export_pdf',side_effect=self.exporter) as export:
            result=build_driver_pdf('1405/07/10',self.directory/'pdf',self.reader)
        export.assert_called_once();self.assertEqual(export.call_args.args[-1],[1,2,3,5])
        self.assertEqual(result['report']['sections']['mechanical']['status'],'no_defects')
    def test_permission_rechecked_after_pdf_generation_and_before_upload(self):
        self.assign()
        def revoke(*args):
            self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id='101'")
            return {'report':{'date':'1405/07/10'},'documents':['unused.pdf','unused2.pdf']}
        with patch('integrations.hermes.function_domain.driver_report.build_driver_pdf',side_effect=revoke),patch('tools.scheduler.tasks.BaleSender') as sender:
            result=driver_daily('101',{'date':'1405/07/10'},authorization=self.auth)
        self.assertEqual(result['status'],'skipped');sender.assert_not_called()
    def test_eight_disabled_and_nine_ten_preserved(self):
        import yaml
        config=yaml.safe_load(Path('settings/schedules.yaml').read_text(encoding='utf-8'))
        jobs={job['id']:job for job in config['schedules']}
        for name in ('repairs_daily','metalwork_daily'):self.assertFalse(jobs[name]['enabled'])
        for name,hour in [('overflow_daily_test',9),('driver_daily_office_supervisor',10)]:
            self.assertTrue(jobs[name]['enabled']);self.assertEqual(jobs[name]['trigger']['hour'],hour)
            self.assertEqual(jobs[name]['recipient_role'],'office_supervisor')
            self.assertEqual(jobs[name]['timezone'],'Asia/Tehran')
            self.assertNotIn('recipient',jobs[name])
    def test_wrong_day_or_wrong_document_count_cannot_be_sent(self):
        self.assign()
        for result in [{'report':{'date':'1405/07/09'},'documents':['a.pdf','b.pdf']},{'report':{'date':'1405/07/10'},'documents':['a.pdf']}]:
            with patch('integrations.hermes.function_domain.driver_report.build_driver_pdf',return_value=result),patch('tools.scheduler.tasks.BaleSender') as sender:
                with self.assertRaises(ValueError):driver_daily('101',{'date':'1405/07/10'},authorization=self.auth)
                sender.assert_not_called()

if __name__=='__main__':unittest.main()
