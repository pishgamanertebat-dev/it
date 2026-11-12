"""Recorder and miscellaneous columns survive schema selection and PDF validation."""
import copy
from pathlib import Path
import tempfile
import unittest

from . import report
from .test_report import workbook, package, DATE


class RecorderSchemaTests(unittest.TestCase):
    def fixture(self, *, miscellaneous=False, missing_label=False):
        book, config=workbook([('801',[[DATE,801,'mechanic','repair','part']])])
        self.addCleanup(book.close)
        sheet=book['801']
        sheet.unmerge_cells('A1:E1')
        sheet.insert_cols(2)
        if not missing_label:sheet['B2']='ثبت کننده'
        sheet['B3']='محسن کرمعلی'
        variant={'columns':[[c.column,c.value] for c in sheet[2] if c.value is not None],
                 'recorder_column':2,'report_columns':[1,3,2,4,5,6]}
        if miscellaneous:
            sheet.title='متفرقه'
            sheet['C2']='قسمت'
            sheet['C3']='تعمیرگاه'
            rule=config['sheets'].pop('801')
            config['sheets']['متفرقه']=rule
            rule['exclude']=True
            variant.update(columns=[[c.column,c.value] for c in sheet[2] if c.value is not None],
                           exclude=False,miscellaneous=True,device='متفرقه')
        else:rule=config['sheets']['801']
        rule['schema_variants']=[variant]
        return book,config,sheet

    def test_recorder_is_a_separate_column_and_legacy_still_works(self):
        book,config,sheet=self.fixture()
        result=report.scan(package(book),DATE,layout=config)
        self.assertEqual(result['devices'][0]['columns'],report.RECORDED_COLUMNS)
        self.assertEqual(result['devices'][0]['rows'][0]['values'],
                         [DATE,'801','محسن کرمعلی','mechanic','repair','part'])
        old,old_config=workbook([('801',[[DATE,801,'mechanic','old','part']])])
        self.addCleanup(old.close)
        self.assertEqual(len(report.scan(package(old),DATE,layout=old_config)['devices'][0]['columns']),5)

    def test_miscellaneous_needs_no_equipment_code_and_keeps_department(self):
        book,config,sheet=self.fixture(miscellaneous=True)
        result=report.scan(package(book),DATE,layout=config)
        device=result['devices'][0]
        self.assertEqual(device['columns'][1],'قسمت')
        self.assertEqual(device['rows'][0]['values'][1:3],['تعمیرگاه','محسن کرمعلی'])
        self.assertEqual(result['excluded_sheets'],[])

    def test_extra_column_or_wrong_code_still_fails(self):
        book,config,sheet=self.fixture()
        sheet['G3']='uninspected'
        with self.assertRaises(ValueError):report.scan(package(book),DATE,layout=config)
        sheet['G3']=None
        sheet['C3']=802
        with self.assertRaises(ValueError):report.scan(package(book),DATE,layout=config)

    def test_missing_label_only_allowed_by_explicit_sheet_variant(self):
        book,config,sheet=self.fixture(missing_label=True)
        self.assertEqual(report.scan(package(book),DATE,layout=config)['devices'][0]['rows'][0]['values'][2],
                         'محسن کرمعلی')
        invalid=copy.deepcopy(config)
        invalid['sheets']['801'].pop('schema_variants')
        with self.assertRaises(ValueError):report.scan(package(book),DATE,layout=invalid)

    def test_six_column_pdf_preserves_every_recorder_and_miscellaneous_cell(self):
        book,config,sheet=self.fixture(miscellaneous=True)
        sheet['B3']='محسن کرمعلی\n' * 28
        sheet['E3']='شرح کامل فعالیت تعمیرگاه\n' * 55
        result=report.scan(package(book),DATE,layout=config)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3]/'runtime') as folder:
            path=Path(folder)/'recorder.pdf'
            validation=report.render(result,path,test_sample=True)
            self.assertTrue(validation['searchable'])
            self.assertGreaterEqual(validation['pages'],2)
            self.assertTrue(all(len(fragment['rects'])==6 for fragment in result['pdf_layout']))


if __name__=='__main__':unittest.main()
