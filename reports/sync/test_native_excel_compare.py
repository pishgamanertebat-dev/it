"""Regression checks for the read-only native Excel comparator."""
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
import zipfile

import openpyxl

SPEC = importlib.util.spec_from_file_location('native_compare', Path(__file__).with_name(
    'AI-EXCEL-FINAL-NATIVE-COMPARE.py'))
COMPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARE)


class NativeCompareTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=COMPARE.ROOT / 'runtime')
        self.addCleanup(scratch.cleanup)
        self.before = Path(scratch.name) / 'before.xlsx'
        self.after = Path(scratch.name) / 'after.xlsx'
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = '521'
        sheet.append(['دامپ 521'])
        sheet.append(['تاریخ', 'ثبت کننده', 'نام مکانیک', 'کد مکانیزم', 'نوع خرابی', 'قطعات مصرفی'])
        sheet.append(['1405/08/25', 'UAT', 'UAT', '521', 'UAT report', ''])
        sheet['F3'] = None
        sheet.print_area = 'A1:F3'
        COMPARE.identity.enable(book)
        COMPARE.identity.attach(book, dict(sheet='521', row=3), '0' * 32, 0)
        book.save(self.before)
        book.close()

    def rewrite(self, *, author='Author', tamper=None, worksheet=None, vml=None, workbook=None):
        with zipfile.ZipFile(self.before) as original, zipfile.ZipFile(self.after, 'w') as modified:
            for path in original.namelist():
                data = original.read(path)
                if path == 'xl/comments/comment1.xml':
                    root = ET.fromstring(data)
                    q = COMPARE.Q
                    root.find(f'{q}authors/{q}author').text = author
                    comment = root.find(f'{q}commentList/{q}comment')
                    comment.set(COMPARE.REVISION_UID, '{00000000-0006-0000-0400-000001000000}')
                    text = comment.find(q + 'text')
                    payload = text[0].text
                    text.remove(text[0])
                    run = ET.SubElement(text, q + 'r')
                    style = ET.SubElement(run, q + 'rPr')
                    for name, key, value in (('sz', 'val', '11'), ('color', 'theme', '1'),
                            ('rFont', 'val', 'Calibri'), ('family', 'val', '2'), ('scheme', 'val', 'minor')):
                        ET.SubElement(style, q + name, {key: value})
                    ET.SubElement(run, q + 't').text = payload
                    if tamper:
                        tamper(root, comment, run)
                    data = ET.tostring(root)
                elif path == 'xl/worksheets/sheet1.xml' and worksheet:
                    data = worksheet(data)
                elif path == 'xl/workbook.xml' and workbook:
                    data = workbook(data)
                elif path.endswith('.vml') and vml:
                    data = vml(data)
                modified.writestr(path, data)
        return COMPARE.compare(self.before, self.after)

    def test_verified_excel_wrapper_preserves_identity_and_reports_author_change(self):
        result = self.rewrite()
        self.assertEqual(result['verdict'], 'REVIEW_REQUIRED')
        self.assertTrue(result['identities_unchanged'])
        self.assertEqual(result['identity_count_after'], 1)
        self.assertEqual(result['notes']['representation_changes'][0]['cell'], 'A3')
        self.assertEqual(result['state_differences'], {})

    def test_unknown_author_and_unverified_payload_or_wrapper_fail(self):
        q = COMPARE.Q
        for alteration in (
            dict(author='Different author'),
            dict(tamper=lambda root, note, run: setattr(run.find(q + 't'), 'text', COMPARE.identity.PREFIX + '{}')),
            dict(tamper=lambda root, note, run: run.find(f'{q}rPr/{q}sz').set('val', '12')),
            dict(tamper=lambda root, note, run: root.find(q + 'commentList').remove(note)),
        ):
            with self.subTest(alteration=alteration):
                result = self.rewrite(**alteration)
                self.assertEqual(result['verdict'], 'FAIL')
                self.assertEqual(result['notes']['representation_changes'], [])

    def test_changes_to_data_and_visibility_are_rejected(self):
        changed = self.rewrite(worksheet=lambda data: data.replace(b'UAT report', b'UAT altered'))
        self.assertEqual(changed['verdict'], 'FAIL')
        self.assertEqual(changed['data_differences'], {'521': ['E3']})
        exposed = self.rewrite(vml=lambda data: data.replace(b'visibility:hidden', b'visibility:visible'))
        self.assertEqual(exposed['verdict'], 'FAIL')
        self.assertEqual(exposed['notes']['invalid_identity_shapes'][0]['cell'], 'A3')

    def test_print_and_layout_changes_are_retained_for_review(self):
        def change_sheet(data):
            data = data.replace(b'<row r="2">', b'<row r="2" ht="30" customHeight="1">')
            return data.replace(b'</worksheet>', b'<pageSetup orientation="landscape"/></worksheet>')
        result = self.rewrite(worksheet=change_sheet,
                              workbook=lambda data: data.replace(b'$A$1:$F$3', b'$A$1:$E$3'))
        self.assertEqual(result['verdict'], 'REVIEW_REQUIRED')
        self.assertIn('521', result['row_differences'])
        self.assertIn('page_setup', result['page_setup_differences']['521'])
        self.assertIn('pageSetup', result['raw_print_markup_differences']['521'])
        self.assertIn('521', result['print_area_differences'])


if __name__ == '__main__':
    unittest.main()
