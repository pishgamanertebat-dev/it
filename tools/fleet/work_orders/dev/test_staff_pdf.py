from pathlib import Path
from unittest.mock import Mock, patch

from tools.fleet.work_orders.dev.test_staff_dispatch import StaffDispatchTests
from tools.fleet.work_orders.dev.test_permissions import test_database
from tools.fleet.work_orders.core import staff_dispatch as core
from tools.fleet.work_orders.core.review import confirm_document_review
from tools.fleet.work_orders.core.delivery import send_work_order


class StaffPdfTests(StaffDispatchTests):
    def prepared_order(self):
        order = self.create()
        number = order['work_order_no']
        confirm_document_review(number, '455740857')
        core.prepare_dispatch(number, '455740857', '455740857', core.staff_options()[0]['id'])
        return order

    def test_all_types_send_pdf_and_preserve_excel(self):
        for kind in ('AIR_FILTER', 'GREASING', 'OIL_CHANGE'):
            with self.subTest(kind=kind):
                order = self.prepared_order()
                number = order['work_order_no']
                excel = Path(order['excel_path'])
                original = excel.read_bytes()
                pdf = excel.with_suffix('.pdf')
                pdf.write_bytes(b'%PDF-1.7\nTEST')
                with test_database(self.db_path) as con:
                    con.execute('UPDATE service_work_orders SET work_order_type=? WHERE work_order_no=?', (kind, number))
                sender = Mock()
                with patch('tools.fleet.work_orders.core.delivery.export_staff_pdf', return_value=pdf) as export, patch('tools.fleet.work_orders.core.delivery.archive_delivered_order'):
                    send_work_order(work_order_no=number, sender=sender)
                    export.assert_called_once_with(excel)
                self.assertEqual(sender.send_document.call_args.kwargs['file_path'], str(pdf))
                self.assertEqual(sender.send_document.call_args.kwargs['file_name'], pdf.name)
                self.assertEqual(excel.read_bytes(), original)
                with test_database(self.db_path) as con:
                    row = con.execute('SELECT status,pdf_path,excel_path FROM service_work_orders WHERE work_order_no=?', (number,)).fetchone()
                self.assertEqual(tuple(row), ('SENT', str(pdf), str(excel)))

    def test_export_failure_does_not_send_excel_or_mark_sent(self):
        order = self.prepared_order()
        sender = Mock()
        with patch('tools.fleet.work_orders.core.delivery.export_staff_pdf', side_effect=RuntimeError('EXPORT FAILED')):
            with self.assertRaisesRegex(RuntimeError, 'EXPORT FAILED'):
                send_work_order(work_order_no=order['work_order_no'], sender=sender)
        sender.send_document.assert_not_called()
        with test_database(self.db_path) as con:
            row = con.execute('SELECT status,send_attempts,last_send_error FROM service_work_orders WHERE work_order_no=?', (order['work_order_no'],)).fetchone()
        self.assertEqual(tuple(row), ('APPROVED', 1, 'EXPORT FAILED'))
