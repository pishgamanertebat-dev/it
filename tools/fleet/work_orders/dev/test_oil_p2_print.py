"""Printed identity comes from the persisted order, including revision linkage."""
from pathlib import Path
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from openpyxl import load_workbook

from tools.fleet.work_orders.core import service
from tools.fleet.work_orders.dev.test_bale_work_order_create import WorkOrderCreateTests
from tools.fleet.work_orders.dev.test_permissions import test_database
from tools.fleet.work_orders.types.oil_change import builder


class OilPrintedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = WorkOrderCreateTests('test_real_builder_produces_six_machine_excel_and_file_ready_record')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def create(self, interval=1800, **kwargs):
        return service.create_work_order(work_order_type='OIL_CHANGE',jalali_date='1405/06/15',
            shift='روزانه',machine_codes=['HD464'],item_actions={'HD464':builder.action_for('HD465-7R',interval)},
            created_by='bale:455740857',**kwargs)

    def test_number_issuance_and_revision_are_read_from_record(self):
        original = self.create()
        revised = self.create(1600,revision_of=original['work_order_no'])
        self.assertNotEqual(original['work_order_no'],revised['work_order_no'])
        self.assertEqual(service.get_work_order(original['work_order_no'])['superseded_by'],revised['work_order_no'])
        for order in (original,revised):
            # Rebuild with an unrelated filename and an old, fixed UTC timestamp.
            with test_database(self.fixture.db_path) as con:
                con.execute("UPDATE service_work_orders SET created_at='2026-10-16 21:00:00' WHERE id=?",(order['id'],))
            record = service.get_work_order(order['work_order_no'])
            output = self.fixture.db_path.parent / ('unrelated-'+str(order['id'])+'.xlsx')
            builder.build_document(output_path=output,jalali_date=record['jalali_date'],items=record['items'],work_order=record)
            book = load_workbook(output)
            try:
                self.assertEqual(book.active['E2'].value,'1405/07/25')  # UTC date is the 16th; Tehran is the 17th.
                self.assertEqual(book.active['J2'].value,'شماره حکم:\n'+record['work_order_no'])
                if record['revision_of']:
                    self.assertIn('نسخه اصلاحی حکم: '+original['work_order_no'],book.active['A1'].value)
            finally:
                book.close()
            if record['revision_of']:
                with ZipFile(builder.TEMPLATES['HD465-7R'][0]) as source, ZipFile(output) as result:
                    source_sheet = ET.fromstring(source.read('xl/worksheets/sheet3.xml'))
                    output_sheet = ET.fromstring(result.read('xl/worksheets/sheet3.xml'))
                    ns = {'s':builder.NS}
                    title = source_sheet.find('.//s:c[@r="A1"]',ns)
                    original_runs = ET.fromstring(source.read('xl/sharedStrings.xml'))[int(title.find('s:v',ns).text)]
                    output_runs = output_sheet.find('.//s:c[@r="A1"]/s:is',ns)
                    self.assertEqual([ET.tostring(r) for r in original_runs], [ET.tostring(r) for r in list(output_runs)[:-1]])

    def test_missing_record_timestamp_rolls_back_creation(self):
        real_builder = builder.build_document
        def missing_timestamp(**kwargs):
            kwargs['work_order']['created_at'] = None
            return real_builder(**kwargs)
        with patch.object(builder,'build_document',side_effect=missing_timestamp):
            with self.assertRaises((TypeError,ValueError)):
                self.create()
        with test_database(self.fixture.db_path) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM service_work_orders').fetchone()[0],0)
        self.assertEqual(list(self.fixture.output_root.rglob('*.xlsx')),[])
