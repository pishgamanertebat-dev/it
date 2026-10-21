import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.fleet.work_orders.dev.test_staff_dispatch import StaffDispatchTests
from tools.fleet.work_orders.dev.test_permissions import test_database
from tools.fleet.work_orders.core import staff_dispatch as core
from tools.fleet.work_orders.core.review import confirm_document_review
from tools.fleet.work_orders.core.delivery import send_work_order, store_manager_pdf


class StaffPdfTests(StaffDispatchTests):
    def test_pretransfer_pdf_change_releases_only_definite_air_filter_claim(self):
        from tools.fleet.work_orders.channels.bale import staff_flow
        order=self.create();number=order['work_order_no']
        pdf=Path(order['excel_path']).with_suffix('.pdf');pdf.write_bytes(b'%PDF-reviewed')
        store_manager_pdf(number,pdf)
        confirm_document_review(number,'455740857')
        original_claim=core.claim_send
        def claim_then_change(number):
            token=original_claim(number)
            pdf.write_bytes(b'%PDF-changed after claim')
            return token
        calls=[]
        async def upload(**kw):calls.append(kw)
        gateway=SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=SimpleNamespace(send_document=upload))})
        with patch.object(core,'claim_send',side_effect=claim_then_change):
            reply=asyncio.run(staff_flow.dispatch(gateway,number,'455740857','455740857',core.staff_options()[0]['id']))
        self.assertIn('تأیید تازه',reply)
        self.assertEqual(calls,[])
        with test_database(self.db_path) as con:
            self.assertEqual(con.execute('SELECT state FROM service_work_order_dispatch WHERE work_order_no=?',(number,)).fetchone()[0],'FAILED')

    def prepared_order(self):
        order = self.create()
        number = order['work_order_no']
        self.review_pdf(number)
        core.prepare_dispatch(number, '455740857', '455740857', core.staff_options()[0]['id'])
        return order

    def test_all_types_send_pdf_and_preserve_excel(self):
        for index,kind in enumerate(('AIR_FILTER', 'GREASING', 'OIL_CHANGE')):
            with self.subTest(kind=kind):
                order = self.create(jalali_date=f'1405/06/{15+index:02d}')
                number = order['work_order_no']
                excel = Path(order['excel_path'])
                original = excel.read_bytes()
                pdf = excel.with_suffix('.pdf')
                pdf.write_bytes(b'%PDF-1.7\nTEST')
                with test_database(self.db_path) as con:
                    con.execute('UPDATE service_work_orders SET work_order_type=? WHERE work_order_no=?', (kind, number))
                store_manager_pdf(number,pdf)
                confirm_document_review(number,'455740857')
                core.prepare_dispatch(number,'455740857','455740857',core.staff_options()[0]['id'])
                sender = Mock()
                with patch('tools.fleet.work_orders.core.delivery.export_staff_pdf', return_value=pdf) as export, patch('tools.fleet.work_orders.core.delivery.archive_delivered_order'):
                    send_work_order(work_order_no=number, sender=sender)
                    export.assert_not_called()
                self.assertEqual(sender.send_document.call_args.kwargs['file_path'], str(pdf))
                self.assertEqual(sender.send_document.call_args.kwargs['file_name'], pdf.name)
                self.assertEqual(sender.send_document.call_args.kwargs['document_bytes'],pdf.read_bytes())
                self.assertEqual(excel.read_bytes(), original)
                with test_database(self.db_path) as con:
                    row = con.execute('SELECT status,pdf_path,excel_path FROM service_work_orders WHERE work_order_no=?', (number,)).fetchone()
                self.assertEqual(tuple(row), ('SENT', str(pdf), str(excel)))

    def test_staff_receives_the_exact_pdf_sent_to_the_manager(self):
        from tools.fleet.work_orders.channels.bale.message_handler import send_manager_excel
        order = self.create()
        number = order['work_order_no']
        excel = Path(order['excel_path'])
        pdf = excel.with_suffix('.pdf')
        pdf.write_bytes(b'%PDF-1.7\nMANAGER COPY')
        preview = {'work_order_no': number, 'label': 'هواکش', 'item_count': len(order['items']),
                   'file_name': excel.name, 'file_path': str(excel)}
        captured = {}

        async def send_document(**kwargs):
            captured['filename'] = kwargs['filename']
            captured['body'] = kwargs['document'].read()

        gateway = SimpleNamespace(adapters={'bale': SimpleNamespace(_bot=SimpleNamespace(send_document=send_document))})
        def render(snapshot):
            target=Path(snapshot).with_suffix('.pdf');target.write_bytes(b'%PDF-1.7\nMANAGER COPY');return target
        with patch('tools.fleet.work_orders.core.delivery.export_staff_pdf', side_effect=render) as export:
            asyncio.run(send_manager_excel(gateway, '455740857', preview))
            export.assert_called_once()
            self.assertEqual(Path(export.call_args.args[0]).name,excel.name)
        self.assertEqual(captured['filename'], pdf.name)
        self.assertEqual(captured['body'], b'%PDF-1.7\nMANAGER COPY')
        with test_database(self.db_path) as con:
            stored = con.execute('SELECT pdf_path FROM service_work_orders WHERE work_order_no=?', (number,)).fetchone()[0]
        self.assertEqual(stored, str(pdf))
        confirm_document_review(number, '455740857')
        core.prepare_dispatch(number, '455740857', '455740857', core.staff_options()[0]['id'])
        sender = Mock()
        with patch('tools.fleet.work_orders.core.delivery.export_staff_pdf', side_effect=AssertionError('staff send must reuse the manager PDF')):
            send_work_order(work_order_no=number, sender=sender)
        self.assertEqual(sender.send_document.call_args.kwargs['file_path'], str(pdf))
        self.assertEqual(sender.send_document.call_args.kwargs['file_name'], pdf.name)
        self.assertEqual(pdf.read_bytes(), b'%PDF-1.7\nMANAGER COPY')

    def test_missing_approved_pdf_does_not_regenerate_send_excel_or_mark_sent(self):
        order = self.prepared_order()
        from tools.fleet.work_orders.core.review import DocumentReviewRequired
        Path(order['excel_path']).with_suffix('.pdf').unlink()
        sender = Mock()
        with patch('tools.fleet.work_orders.core.delivery.export_staff_pdf') as export:
            with self.assertRaises(DocumentReviewRequired):
                send_work_order(work_order_no=order['work_order_no'], sender=sender)
            export.assert_not_called()
        sender.send_document.assert_not_called()
        with test_database(self.db_path) as con:
            row = con.execute('SELECT status,send_attempts,last_send_error FROM service_work_orders WHERE work_order_no=?', (order['work_order_no'],)).fetchone()
        self.assertEqual(tuple(row), ('APPROVED', 1, 'DocumentReviewRequired'))
