from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from tools.fleet.car_wash.proposal import build_proposal, evaluate
from tools.fleet.car_wash.source import read_source


def make_source(path):
    book = Workbook()
    ws = book.active
    ws.title = 'ساعت کاری'
    ws.cell(1, 7, 'فروردین')
    for day in range(1, 9):
        col = 7 + (day - 1) * 2
        ws.cell(2, col, day)
        ws.cell(3, col, 'روز')
        ws.cell(3, col + 1, 'شب')
    for row, (name, code, old) in enumerate([
        ('دامپتراک کوماتسو 7-785', 'HD719', 433),
        ('بیل مکانیکی دوسان 230', 'EX231', 110),
        ('مزدا', 'MZ1', 'MZ1'),
        ('بیل مکانیکی 1250', 'EX1251', 120),
        ('بلدوزر کوماتسو 155', 'D155', 151),
        ('بلدوزر کوماتسو 155', 'D155', 152),
        ('کامیون آب پاش', 's1', 501),
    ], 4):
        ws.cell(row, 1, name)
        ws.cell(row, 2, code)
        ws.cell(row, 3, old)
    blue = PatternFill('solid', fgColor='FF00B0F0')
    ws['G4'].fill, ws['G4'].value = blue, 10
    for col in range(8, 16):
        ws.cell(4, col, 20)
    ws['G5'] = 3  # No blue: available for manual addition, but not automatic.
    ws['I6'].fill = blue  # 1/2 -> 1/9 is exactly seven days.
    ws['G7'].fill, ws['G7'].value, ws['H7'].value = blue, 5, 'باز کردن موتور'
    ws['G8'].fill = blue
    ws['G9'].fill = blue
    ws['G10'].fill = blue
    book.save(path)
    book.close()


class WashProposalTests(unittest.TestCase):
    def test_unreadable_source_is_an_actionable_error_not_an_empty_proposal(self):
        with self.assertRaisesRegex(ValueError, 'قابل خواندن نیست'):
            read_source(Path('runtime/carwash-unit-tests/does-not-exist.xlsx'), (1, 8, 1))

    def test_threshold_includes_blue_shift_and_all_values_above_200(self):
        for value, state in [(169.999, 'OK'), (170, 'NEAR_DUE'), (199.999, 'NEAR_DUE'),
                             (200, 'DUE'), (230, 'DUE'), (500, 'DUE')]:
            entries = [{'stamp': (1, 1, 0), 'blue': True, 'hours': 10, 'cell': 'G4'}]
            remaining = value - 10
            n = 0
            while remaining > 0:
                hours = min(20, remaining)
                entries.append({'stamp': (1, 2 + n // 2, n % 2), 'blue': False, 'hours': hours, 'cell': f'C{n}'})
                remaining -= hours
                n += 1
            component, reason = evaluate({'code': 'HD719', 'name': 'دامپتراک', 'entries': entries}, '1405/01/30')
            self.assertIsNone(reason)
            self.assertAlmostEqual(component['value'], value)
            self.assertEqual(component['state'], state)

    def test_weekly_rule_uses_calendar_days_for_both_light_groups(self):
        for code in ('MZ1', 'PR2'):
            machine = {'code': code, 'name': 'سواری', 'entries': [
                {'stamp': (1, 2, 1), 'blue': True, 'hours': None, 'cell': 'J4'}]}
            for date, expected in [('1405/01/08', 'OK'), ('1405/01/09', 'DUE')]:
                result, reason = evaluate(machine, date)
                self.assertIsNone(reason)
                self.assertEqual(result['state'], expected)

    def test_blank_blue_is_an_event_blank_and_dash_are_zero(self):
        machine = {'code': 'HD719', 'name': 'دامپتراک', 'entries': [
            {'stamp': (1, 1, 0), 'blue': True, 'hours': None, 'cell': 'G4'},
            {'stamp': (1, 1, 1), 'blue': False, 'hours': '-', 'cell': 'H4'},
            {'stamp': (1, 2, 0), 'blue': False, 'hours': 5, 'cell': 'I4'}]}
        result, reason = evaluate(machine, '1405/01/09')
        self.assertIsNone(reason)
        self.assertEqual(result['value'], 5)

    def test_missing_blue_and_non_numeric_hours_remain_review(self):
        for entry in [dict(blue=False, hours=10), dict(blue=True, hours='خراب'), dict(blue=True, hours=-1),
                      dict(blue=True, hours=True), dict(blue=True, hours=float('nan'))]:
            result, reason = evaluate({'code': 'HD719', 'name': 'دامپتراک', 'entries': [
                {'stamp': (1, 1, 0), 'cell': 'G4', **entry}]}, '1405/01/09')
            self.assertIsNone(result)
            self.assertTrue(reason)

    def test_only_latest_blue_restarts_the_counter(self):
        machine = {'code': 'HD719', 'name': 'دامپتراک', 'entries': [
            {'stamp': (1, 1, 0), 'blue': True, 'hours': 24, 'cell': 'G4'},
            {'stamp': (1, 2, 0), 'blue': True, 'hours': 3, 'cell': 'I4'},
            {'stamp': (1, 2, 1), 'blue': False, 'hours': 4, 'cell': 'J4'}]}
        result, _ = evaluate(machine, '1405/01/09')
        self.assertEqual(result['value'], 7)

    def test_snapshot_read_preserves_input_and_includes_review_devices_in_catalog(self):
        root = Path(__file__).resolve().parents[3] / 'runtime/carwash-unit-tests'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            path = Path(temp) / 'source.xlsx'
            make_source(path)
            before = path.read_bytes()
            proposal = build_proposal(path, (1, 8, 1), '1405/01/09')
            self.assertEqual(before, path.read_bytes())
            self.assertEqual({i['machine_code'] for i in proposal['items']}, {'HD719', 'MZ1'})
            self.assertIn('EX231', {i['machine_code'] for i in proposal['catalog']})
            self.assertEqual({r['code'] for r in proposal['review']}, {'EX231', 'EX1251'})
            self.assertTrue({'D151', 'D152'}.issubset({i['machine_code'] for i in proposal['catalog']}))
            source = read_source(path, (1, 1, 0))
            self.assertFalse(any(e['stamp'] > (1, 1, 0) for m in source['machines'] for e in m['entries']))
