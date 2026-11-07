"""Synthetic moved sections, exact dates, formula caches and actual PDF cells."""
import copy
import io
from pathlib import Path
import re
import tempfile
import unittest
from zipfile import ZipFile

import openpyxl

from tools.scheduler.report_errors import ReportFailure
from .report import COLUMNS, STOCK_COLUMNS, STOCK_LABEL, TITLE, scan, source_snapshot
from .render import render, validate_pdf

TARGET = '1405/08/11'


def workbook(*, offset=0, col_offset=0, rows=None):
    book = openpyxl.Workbook()
    s = book.active
    s.title = 'موازنه'
    def write(r, c, v):
        s.cell(r+offset, c+col_offset, v)
    write(1, 1, TITLE)
    for c, label in enumerate(COLUMNS, 1):
        write(2, c, label)
    records = rows if rows is not None else [
        [23, '1405/08/10', 'ژنراتور', 'DG1', 10],
        [24, TARGET, 'بیل مکانیکی', 1255, 86],
        [25, TARGET, 'دامپتراک', 720, 130],
    ]
    for r, record in enumerate(records, 3):
        for c, value in enumerate(record, 1):
            write(r, c, value)
    write(90, 1, 'جمع کل تعویض')
    write(91, 1, 'جمع سرریز روغن')
    for c, value in enumerate(['ردیف', 'تاریخ', None, None, *STOCK_COLUMNS], 1):
        write(92, c, value)
    write(93, 1, 1)
    write(93, 2, '1405/08/12')  # Other sections never establish change-table readiness.
    write(100, 1, 'نوع')
    for c, label in enumerate(STOCK_COLUMNS, 5):
        write(100, c, label)
    write(101, 1, STOCK_LABEL)
    for c, value in enumerate([732, 1057, 1224, 342.85], 5):
        write(101, c, value)
        s.cell(101+offset, c+col_offset).number_format = '#,##0'
    return book


def package(book, caches=None):
    buffer = io.BytesIO()
    book.save(buffer)
    if not caches:
        return buffer.getvalue()
    output = io.BytesIO()
    with ZipFile(buffer) as src, ZipFile(output, 'w') as dst:
        for entry in src.infolist():
            data = src.read(entry.filename)
            if entry.filename == 'xl/worksheets/sheet1.xml':
                xml = data.decode('utf-8')
                for coordinate, value in caches.items():
                    pattern = r'(<c\b[^>]*\br="' + coordinate + r'"[^>]*>)(.*?)(</c>)'
                    def replace(match):
                        body = re.sub(r'<v(?:\s*/>|>.*?</v>)', '', match[2])
                        return match[1] + body + f'<v>{value}</v>' + match[3]
                    xml, count = re.subn(pattern, replace, xml)
                    if count != 1:
                        raise AssertionError('Fixture cache cell not found')
                data = xml.encode('utf-8')
            dst.writestr(entry, data)
    return output.getvalue()


class ExtractionTests(unittest.TestCase):
    def report(self, book=None, target=TARGET):
        book = book or workbook()
        try:
            return scan(package(book), target)
        finally:
            book.close()

    def test_two_exact_rows_and_visible_inventory_without_other_dates(self):
        report = self.report()
        self.assertEqual([r['values'][:5] for r in report['rows']], [
            ['24', TARGET, 'بیل مکانیکی', '1255', '86'], ['25', TARGET, 'دامپتراک', '720', '130']])
        self.assertEqual([v['display'] for v in report['inventory']], ['732', '1,057', '1,224', '343'])
        self.assertEqual(report['inventory'][-1]['value'], 342.85)

    def test_independent_row_and_column_shifts(self):
        for offset, columns in [(7, 0), (0, 3), (14, 2)]:
            with self.subTest(offset=offset, columns=columns):
                report = self.report(workbook(offset=offset, col_offset=columns))
                self.assertEqual(report['rows_matched'], 2)
                self.assertEqual(report['source_locations']['inventory_row'], 101+offset)

    def test_column_mapping_uses_labels_not_fixed_order(self):
        book = workbook()
        s = book.active
        for r in range(2, 90):
            a, b = s.cell(r, 5).value, s.cell(r, 8).value
            s.cell(r, 5).value, s.cell(r, 8).value = b, a
        self.assertEqual(self.report(book)['rows'][0]['values'][4], '86')

    def test_future_date_in_overflow_is_not_target_date_evidence(self):
        self.assertEqual(self.report(target='1405/08/12')['rows_matched'], 0)

    def test_date_only_template_and_serial_only_do_not_count(self):
        book = workbook(rows=[[1], [2, TARGET]])
        self.assertEqual(self.report(book)['rows_matched'], 0)

    def test_missing_title_header_inventory_or_end_marker_fails_closed(self):
        for coordinates in [('A1',), ('G2',), ('A101',), ('G100',), ('A90', 'A91')]:
            with self.subTest(coordinates=coordinates):
                book = workbook()
                for coordinate in coordinates:
                    book.active[coordinate] = None
                with self.assertRaises(ReportFailure) as caught:
                    self.report(book)
                self.assertEqual(caught.exception.code, 'source_section_missing')

    def test_duplicate_title_or_stock_is_ambiguous(self):
        for value in (TITLE, STOCK_LABEL):
            book = workbook()
            book.active['A110'] = value
            with self.assertRaises(ReportFailure) as caught:
                self.report(book)
            self.assertEqual(caught.exception.code, 'source_section_missing')

    def test_undated_activity_and_missing_machine_fail(self):
        for record in ([1, None, 'بیل مکانیکی', 1255, 86], [1, TARGET, None, 1255, 86]):
            with self.assertRaises(ReportFailure):
                self.report(workbook(rows=[record]))

    def test_zero_is_a_real_record_blank_stays_blank(self):
        report = self.report(workbook(rows=[[1, TARGET, 'بیل مکانیکی', 1255, 0]]))
        self.assertEqual(report['rows'][0]['values'][4:], ['0', '', '', '', ''])

    def test_arabic_letters_spaces_and_persian_digits(self):
        book = workbook()
        s = book.active
        s['A1'] = TITLE.replace('ی', 'ي').replace(' ', '\u200c')
        s['F2'] = 'روغن ۳۰'
        s['B4'] = '۱۴۰۵/۰۸/۱۱'
        self.assertEqual(self.report(book)['rows_matched'], 2)

    def test_local_arithmetic_and_sum_caches_must_match(self):
        book = workbook()
        s = book.active
        s['E101'] = '=SUM(E103:E105)+E106*2-4/2'
        s['E103'], s['E104'], s['E105'], s['E106'] = 700, 20, None, 7
        for saved in (732, 731, None):
            with self.subTest(saved=saved):
                data = package(book, {'E101': saved} if saved is not None else None)
                if saved == 732:
                    self.assertEqual(scan(data, TARGET)['inventory'][0]['value'], 732)
                else:
                    with self.assertRaises(ReportFailure) as caught:
                        scan(data, TARGET)
                    self.assertEqual(caught.exception.code, 'source_formula_unverified')
        book.close()

    def test_external_or_unknown_or_cyclic_formula_never_runs(self):
        for formula in ('=WEBSERVICE("https://example.invalid")', '=Other!E1', '=E101'):
            book = workbook()
            book.active['E101'] = formula
            try:
                with self.assertRaises(ReportFailure) as caught:
                    scan(package(book, {'E101': 732}), TARGET)
                self.assertEqual(caught.exception.code, 'source_formula_unverified')
            finally:
                book.close()

    def test_corrupt_input_and_non_numeric_inventory_never_render(self):
        with self.assertRaises(ReportFailure):
            scan(b'not an xlsx', TARGET)
        book = workbook()
        book.active['E101'] = 'نامشخص'
        with self.assertRaises(ReportFailure):
            self.report(book)

    def test_snapshot_is_scoped_read_only_and_unchanged(self):
        from integrations.hermes.function_domain.scoped import ScopedReader
        from .report import SOURCE_NAME
        root = Path('runtime/oil-balance-tests')
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            path = Path(directory)/SOURCE_NAME
            book = workbook()
            data = package(book)
            book.close()
            path.write_bytes(data)
            with source_snapshot(ScopedReader(directory, allowed_files=[SOURCE_NAME])) as (actual, digest):
                self.assertEqual(actual, data)
                self.assertEqual(len(digest), 64)
            self.assertEqual(path.read_bytes(), data)


class PDFTests(unittest.TestCase):
    report = ExtractionTests.report
    def test_actual_persian_pdf_checks_every_source_cell(self):
        report = self.report()
        root = Path('runtime/oil-balance-tests')
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            path = Path(directory)/'report.pdf'
            render(report, path)
            validation = validate_pdf(path, report)
            self.assertEqual((validation['pages'], validation['rows'], validation['inventory_values']), (1, 0, 4))
            wrong = copy.deepcopy(report)
            wrong['pdf_cells'][-1]['values'][3] = '999'
            with self.assertRaises(ReportFailure):
                validate_pdf(path, wrong)

    def test_daily_details_are_omitted_even_with_many_source_rows(self):
        import fitz
        from .render import pdf_key
        report = self.report(workbook(rows=[[n, TARGET, 'بیل مکانیکی', 1255, n] for n in range(1, 18)]))
        root = Path('runtime/oil-balance-tests')
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            path = Path(directory)/'many.pdf'
            render(report, path)
            result = validate_pdf(path, report)
            self.assertEqual((result['pages'], result['rows']), (1, 0))
            with fitz.open(path) as doc:
                text = pdf_key(doc[0].get_text())
            self.assertNotIn(pdf_key('بیل مکانیکی'), text)
            self.assertNotIn(pdf_key('کد دستگاه'), text)
            self.assertNotIn(pdf_key(TITLE), text)
            self.assertIn(pdf_key(STOCK_LABEL), text)


if __name__ == '__main__':
    unittest.main()
