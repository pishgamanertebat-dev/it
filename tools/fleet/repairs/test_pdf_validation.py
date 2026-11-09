"""Actual PDF content mutations and pinned-source tests; no network or COM."""
from copy import deepcopy
import hashlib
import html
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
import openpyxl

from integrations.hermes.function_domain.driver_report import (
    load_driver,build_driver_pdf,render_pdf_notice,SECTIONS)
from .pdf_validation import validate_driver_pdf,DriverPDFInvalid


def make_pdf(path,report,section,*,omit_title=False,overrides=None):
    """Searchable shaped PDF table, independently laid out by MuPDF HTML."""
    xs=[25,405,495,720,815];headers=[report['sections'][section]['title'],'کد جدید','نوع دستگاه','ردیف']
    css='@font-face{font-family:Test;src:url(tahoma.ttf)} body{font-family:Test;font-size:11px;margin:0} p{margin:0;text-align:right}'
    archive=fitz.Archive('C:/Windows/Fonts')
    overrides=overrides or {}
    with fitz.open() as doc:
        def cell(page,value,rect):
            body='<p dir="auto">'+html.escape(str(value if value is not None else '')).replace('\n','<br>')+'</p>'
            spare,scale=page.insert_htmlbox(rect,body,css=css,archive=archive,scale_low=1)
            if spare<0:raise AssertionError('Test PDF cell overflow')
        def new_page():
            page=doc.new_page(width=842,height=595)
            for t,b in [(20,45),(45,85)]:
                for a,z in zip(xs,xs[1:]):page.draw_rect(fitz.Rect(a,t,z,b),color=(0,0,0),width=.5)
            cell(page,report['date'],fitz.Rect(xs[0]+3,23,xs[-1]-3,43))
            for i,h in enumerate(headers):
                if i==0 and omit_title:continue
                cell(page,h,fitz.Rect(xs[i]+3,48,xs[i+1]-3,83))
            return page,85
        page,y=new_page();col=3 if section=='mechanical' else 4
        for index,item in enumerate(report['inventory']):
            if y+65>575:page,y=new_page()
            values=item['values'];desc=overrides.get(index,values[col])
            for i,value in enumerate([desc,values[2],values[1],values[0]]):
                page.draw_rect(fitz.Rect(xs[i],y,xs[i+1],y+65),color=(0,0,0),width=.5)
                cell(page,value,fitz.Rect(xs[i]+3,y+3,xs[i+1]-3,y+63))
            y+=65
        doc.save(path)


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        book=openpyxl.Workbook();ws=book.active;ws.title='report-17'
        ws.append(['گزارش','1405/08/17']);ws.append(['ردیف','نوع دستگاه','کد جدید',*SECTIONS.values()])
        for i,desc in enumerate(['پین و بوش خراب است','پین و بوش خراب است','شرح بلند\nپایان کامل شرح با ۱۵۰۰ و ۲۰۰۰'],1):
            ws.append([i,'بیل مکانیکی',f'QA{i:03}',f'شرح مستقل مکانیکی {i}',desc])
        ws.print_area='A1:D5';stream=io.BytesIO();book.save(stream);book.close()
        self.data=stream.getvalue();self.report=load_driver('1405/08/17',data=self.data)
        self.digest=hashlib.sha256(self.data).hexdigest();self.path=self.directory/'report.pdf'

    def validate(self,section='metalwork',**kwargs):
        make_pdf(self.path,self.report,section,**kwargs)
        return validate_driver_pdf(self.path,self.report,section,self.digest)

    def reject(self,defect,**kwargs):
        with self.assertRaises(DriverPDFInvalid) as caught:self.validate(**kwargs)
        self.assertEqual(caught.exception.details['defect'],defect)
        self.assertEqual(caught.exception.details['section'],'metalwork')
        return caught.exception

    def test_complete_rows_and_duplicate_descriptions(self):
        result=self.validate()
        self.assertEqual(result['nonempty_descriptions'],3)
        self.assertEqual(len(result['cells']),3)
        self.assertEqual(result['snapshot_sha256'],self.digest)

    def test_latin_identifiers_and_hyphen_glyphs(self):
        self.report['inventory'][0]['values'][4]='mechanical-only'
        self.report['inventory'][1]['values'][4]='METAL_ONLY'
        self.assertEqual(self.validate()['status'],'passed')

    def test_missing_title(self):self.reject('title_missing',omit_title=True)
    def test_missing_one_duplicate(self):self.reject('description_mismatch',overrides={1:''})
    def test_only_common_words(self):self.reject('description_mismatch',overrides={0:'پین'})
    def test_missing_tail_and_last_row(self):self.reject('description_mismatch',overrides={2:'شرح بلند'})
    def test_cross_section_text(self):self.reject('description_mismatch',overrides={1:'شرح مستقل مکانیکی 2'})

    def test_wrong_section_pdf(self):
        make_pdf(self.path,self.report,'mechanical')
        with self.assertRaises(DriverPDFInvalid) as caught:
            validate_driver_pdf(self.path,self.report,'metalwork',self.digest)
        self.assertEqual(caught.exception.details['defect'],'section_mismatch')

    def test_identity_mismatch(self):
        changed=deepcopy(self.report);changed['inventory'][1]['values'][1]='لودر'
        make_pdf(self.path,changed,'metalwork')
        with self.assertRaises(DriverPDFInvalid) as caught:
            validate_driver_pdf(self.path,self.report,'metalwork',self.digest)
        self.assertEqual(caught.exception.details['defect'],'identity_mismatch')

    def test_actual_empty_excel_not_blank_pdf(self):
        book=openpyxl.load_workbook(io.BytesIO(self.data));ws=book.active
        for row in ws.iter_rows(min_row=3):row[4].value=None
        stream=io.BytesIO();book.save(stream);book.close()
        report=load_driver('1405/08/17',data=stream.getvalue())
        self.assertEqual(report['sections']['metalwork']['status'],'no_defects')
        render_pdf_notice(report,'metalwork',self.path)
        result=validate_driver_pdf(self.path,report,'metalwork',hashlib.sha256(stream.getvalue()).hexdigest())
        self.assertEqual(result['nonempty_descriptions'],0)
        with self.assertRaises(DriverPDFInvalid):
            validate_driver_pdf(self.path,self.report,'metalwork',self.digest)

    def test_unsearchable_uncertain_blocks(self):
        with fitz.open() as doc:
            doc.new_page();doc.save(self.path)
        with self.assertRaises(DriverPDFInvalid):validate_driver_pdf(self.path,self.report,'metalwork',self.digest)

    def test_parser_crash_after_evidence_cannot_authorize_send(self):
        from types import SimpleNamespace
        with patch('tools.fleet.repairs.pdf_validation.subprocess.run',return_value=SimpleNamespace(
                returncode=-1,stdout=json.dumps({'status':'passed'}))):
            with self.assertRaises(DriverPDFInvalid) as caught:
                validate_driver_pdf(self.path,self.report,'metalwork',self.digest)
        self.assertEqual(caught.exception.details['defect'],'visibility_uncertain')

    def test_builder_validates_same_snapshot_despite_live_changes(self):
        class Reader:
            calls=0
            def snapshot(reader,name):reader.calls+=1;return self.data
        reader=Reader()
        def export(source,sheet,output,directory,columns):
            actual=Path(source).read_bytes()
            self.assertEqual(actual,self.data)
            report=load_driver('1405/08/17',data=actual)
            section='metalwork' if columns[-1]==5 else 'mechanical'
            make_pdf(output,report,section)
        with patch('tools.fleet.repairs.report.export_pdf',side_effect=export):
            result=build_driver_pdf('1405/08/17',self.directory/'output',reader)
        self.assertEqual(reader.calls,1)
        self.assertTrue(result['ok']);self.assertEqual(set(result['validation']),set(SECTIONS))
        self.assertTrue(all(v['snapshot_sha256']==self.digest for v in result['validation'].values()))

    def test_partial_generation_does_not_discard_healthy_mechanical(self):
        def export(source,sheet,output,directory,columns):
            section='metalwork' if columns[-1]==5 else 'mechanical'
            make_pdf(output,self.report,section,omit_title=section=='metalwork')
        with patch('tools.fleet.repairs.report.export_pdf',side_effect=export):
            result=build_driver_pdf('1405/08/17',self.directory/'output',data=self.data,allow_partial=True)
        self.assertEqual(set(result['artifacts']),{'mechanical'})
        self.assertEqual(result['failures']['metalwork'].code,'driver_pdf_content')
        self.assertFalse(result['ok'])

    def test_diagnostic_has_sheet_number_date_section_and_defect(self):
        from tools.scheduler.report_errors import public_reason
        failure=self.reject('description_mismatch',overrides={2:''})
        reason=public_reason(failure.code,failure.serialized())
        for value in ['report-17','1405/08/17','آهنگری','شماره شیت 1','انتهای آن']:self.assertIn(value,reason)


if __name__=='__main__':unittest.main()
