"""Approved blank ordinals must not hide missing or duplicated machine identity."""
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import openpyxl

from integrations.hermes.function_domain.driver_report import load_driver,SECTIONS
from tools.fleet.repairs.report import export_pdf
from tools.fleet.repairs.pdf_validation import validate_driver_pdf,DriverPDFInvalid
from tools.fleet.repairs.test_pdf_validation import make_pdf
from tools.scheduler.report_errors import ReportFailure,public_reason


class DriverOrdinalTests(unittest.TestCase):
    def workbook(self,rows,columns=None):
        book=openpyxl.Workbook();sheet=book.active;sheet.title='report-19'
        sheet.append(['گزارش','1405/08/19'])
        sheet.append(columns or ['ردیف','نوع دستگاه','کد جدید',*SECTIONS.values()])
        for row in rows:sheet.append(row)
        stream=io.BytesIO();book.save(stream);book.close()
        return stream.getvalue()

    def test_blank_ordinal_preserves_all_rows_and_descriptions_without_renumbering(self):
        data=self.workbook([[25,'دامپتراک','HD717','شرح قبل','آهنگری قبل'],
            [None,'دامپتراک','HD718','شرح دستگاه بدون شماره','آهنگری دستگاه بدون شماره'],
            [26,'دامپتراک','HD719','شرح بعد',None]])
        report=load_driver('1405/08/19',data=data)
        self.assertEqual([r['values'][0] for r in report['inventory']],[25,None,26])
        self.assertEqual([r['values'][2] for r in report['inventory']],['HD717','HD718','HD719'])
        self.assertEqual([len(v['rows']) for v in report['sections'].values()],[3,2])
        self.assertIsNone(report['sections']['mechanical']['rows'][1][0])

    def test_required_identity_still_blocks_with_public_cell_diagnostic(self):
        for index,field,cell,label in [(1,'type','B3','نوع دستگاه'),(2,'code','C3','کد دستگاه')]:
            for missing in (None,'   '):
                with self.subTest(field=field,missing=missing):
                    row=[None,'دامپتراک','HD718','شرح',None];row[index]=missing
                    with self.assertRaises(ReportFailure) as caught:
                        load_driver('1405/08/19',data=self.workbook([row]))
                    failure=caught.exception
                    self.assertEqual(failure.code,f'source_driver_{field}_missing')
                    text=public_reason(failure.code,failure.serialized())
                    for part in [cell,label,'1405/08/19','report-19']:self.assertIn(part,text)
                    self.assertNotIn('شرح',failure.serialized())

    def test_duplicate_code_with_blank_ordinals_remains_blocked(self):
        data=self.workbook([[None,'دامپتراک','HD718','شرح اول',None],
            [None,'دامپتراک','HD718','شرح دوم',None]])
        with self.assertRaises(ReportFailure) as caught:load_driver('1405/08/19',data=data)
        self.assertEqual(caught.exception.code,'source_driver_code_duplicate')
        self.assertIn('C4',public_reason(caught.exception.code,caught.exception.serialized()))

    def test_required_identity_diagnostics_follow_reordered_columns(self):
        data=self.workbook([[None,'شرح',None,None,'دامپتراک']],
            [*SECTIONS.values(),'ردیف','کد جدید','نوع دستگاه'])
        with self.assertRaises(ReportFailure) as caught:load_driver('1405/08/19',data=data)
        self.assertEqual(caught.exception.details['column'],4)
        self.assertIn('D3',public_reason(caught.exception.code,caught.exception.serialized()))

    def test_pdf_requires_blank_ordinal_and_complete_description_for_both_sections(self):
        data=self.workbook([[None,'دامپتراک','HD718','شرح مکانیکی کامل','شرح آهنگری کامل']])
        report=load_driver('1405/08/19',data=data);digest=hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.pdf'
            for section in SECTIONS:
                make_pdf(path,report,section)
                self.assertEqual(validate_driver_pdf(path,report,section,digest)['status'],'passed')
                report['inventory'][0]['values'][0]=999
                with self.assertRaises(DriverPDFInvalid):validate_driver_pdf(path,report,section,digest)
                report['inventory'][0]['values'][0]=None

    def test_export_accepts_blank_ordinal_without_saving_source(self):
        data=self.workbook([[None,'دامپتراک','HD718','شرح کامل',None]])
        report=load_driver('1405/08/19',data=data)
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory);source=directory/'source.xlsx';source.write_bytes(data)
            output=directory/'report.pdf';make_pdf(output,report,'mechanical')
            with patch('tools.fleet.repairs.report.subprocess.run',return_value=SimpleNamespace(returncode=0)):
                export_pdf(source,'report-19',output,directory,[1,2,3,4])
            self.assertEqual(source.read_bytes(),data)
            self.assertEqual(validate_driver_pdf(output,report,'mechanical',hashlib.sha256(data).hexdigest())['status'],'passed')

    def test_export_still_rejects_missing_type_or_code_before_native_rendering(self):
        for index,field in [(1,'type'),(2,'code')]:
            row=[None,'دامپتراک','HD718','شرح',None];row[index]=None
            with tempfile.TemporaryDirectory() as directory:
                directory=Path(directory);source=directory/'source.xlsx';source.write_bytes(self.workbook([row]))
                with patch('tools.fleet.repairs.report.subprocess.run') as native:
                    with self.assertRaises(ReportFailure) as caught:
                        export_pdf(source,'report-19',directory/'report.pdf',directory,[1,2,3,4])
                native.assert_not_called();self.assertEqual(caught.exception.code,f'source_driver_{field}_missing')


if __name__=='__main__':unittest.main()
