from unittest.mock import patch
from openpyxl import load_workbook

from tools.fleet.work_orders.core.review import normalize_shift, confirm_document_review
from tools.fleet.work_orders.dev.test_bale_work_order_create import WorkOrderCreateTests
from tools.fleet.work_orders.dev.test_permissions import test_database
from tools.fleet.work_orders.dev.test_bale_creation_flow import CreationFlowTests


class ManagerReviewTests(WorkOrderCreateTests):
    def test_legacy_retry_recovers_only_latest_owned_ready_order(self):
        from tools.fleet.work_orders.channels.bale.create_worker import execute_request
        from tools.fleet.work_orders.core import service
        owned = service.create_work_order(work_order_type='AIR_FILTER', jalali_date='1405/06/15', shift='صبح',
                                          machine_codes=['714'], created_by='bale:455740857')
        service.create_work_order(work_order_type='AIR_FILTER', jalali_date='1405/06/15', shift='صبح',
                                  machine_codes=['714'], created_by='bale:1006')
        result = execute_request({'action': 'preview_latest', 'bale_id': '455740857'}, db_path=self.db_path)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['order']['work_order_no'], owned['work_order_no'])

    def test_shift_and_persistent_review_preserve_workflow(self):
        self.assertEqual(normalize_shift("صبح عصر"), "صبح-ظهر")
        self.assertEqual(normalize_shift("صبح-ظهر شب"), "صبح-ظهر-شب")
        with self.assertRaises(ValueError):
            normalize_shift("نامعتبر")
        from tools.fleet.work_orders.core import service
        order = service.create_work_order(work_order_type="AIR_FILTER", jalali_date="1405/06/15", shift=normalize_shift("صبح ظهر"), machine_codes=["714"], created_by="bale:455740857")
        wb = load_workbook(order["excel_path"], read_only=True)
        try:
            self.assertEqual(wb.active["A1"].value, "لیست هواکش شیفت صبح-ظهر")
        finally:
            wb.close()
        with test_database(self.db_path) as con:
            con.execute("UPDATE service_work_orders SET notes='existing note'")
        first = confirm_document_review(order["work_order_no"], "455740857")
        self.assertEqual(confirm_document_review(order["work_order_no"], "455740857"), first)
        with self.assertRaises(ValueError):
            confirm_document_review(order["work_order_no"], "1006")
        updated = service.get_work_order(order["work_order_no"])
        self.assertEqual(updated["status"], "FILE_READY")
        self.assertIsNone(updated["assigned_staff_id"])
        self.assertIsNone(updated["approved_at"])
        self.assertIsNone(updated["sent_at"])
        self.assertTrue(updated["notes"].startswith("existing note\n"))
        self.assertEqual(updated["notes"].count("MANAGER_DOCUMENT_REVIEW:"), 1)


class ManagerDeliveryTests(CreationFlowTests):
    async def test_bare_retry_and_confirmation_survive_session_loss(self):
        async def fail(*args):
            raise RuntimeError('upload failed')
        self.handler.document_sender = fail
        await self.fill_to_shift()
        with self.assertLogs('tools.fleet.work_orders.channels.bale.message_handler', level='ERROR'):
            self.message('صبح')
            await self.settle()
        self.message('تایید')
        self.assertIn('ابتدا', self.replies[-1])
        self.handler.pending.clear()
        async def worker(request):
            self.requests.append(request)
            if request['action'] == 'confirm_review':
                return {'ok': True, 'work_order_type': 'AIR_FILTER'}
            return {'ok': True, 'order': {'work_order_no': request['work_order_no'], 'work_order_type': 'AIR_FILTER',
                    'label': 'هواکش', 'item_count': 1, 'file_name': 'order.xlsx'}}
        async def upload(*args):
            pass
        self.handler.worker = worker
        self.handler.document_sender = upload
        self.message('ارسال مجدد')
        await self.settle()
        self.assertEqual(self.requests[-1]['action'], 'preview')
        self.assertIn('تایید یا ویرایش را از دکمه‌های زیر', self.replies[-1])
        self.handler.pending.clear()
        with patch('tools.fleet.work_orders.core.staff_dispatch.staff_menu', return_value=('سرویسکار را انتخاب کنید:', [{'id': 1, 'display_name':'سرویسکار آزمایشی'}])):
            self.message('تایید')
            await self.settle()
        self.assertEqual(self.requests[-1]['action'], 'confirm_review')
        self.assertEqual(next(iter(self.handler.pending.values())).stage, 'STAFF')
        self.assertIn('سرویسکار را از دکمه‌های زیر انتخاب کنید', self.replies[-1])

    async def test_document_failure_keeps_order_and_offers_retry(self):
        async def fail(*args):
            raise RuntimeError("TEST UPLOAD FAILURE")
        self.handler.document_sender = fail
        await self.fill_to_shift()
        with self.assertLogs("tools.fleet.work_orders.channels.bale.message_handler", level="ERROR"):
            self.message("صبح ظهر")
            await self.settle()
        self.assertIn("حکم محفوظ است", self.replies[-1])
        self.assertTrue(self.replies[-1].endswith("ارسال مجدد"))
        self.assertEqual(self.requests[-1]["shift"], "صبح-ظهر")
        self.assertNotIn("آیا تأیید می‌کنید", self.replies[-1])

    async def test_successful_document_prompts_review_and_numbered_command_survives_session_loss(self):
        received = []
        async def upload(gateway, chat, order):
            received.append((chat, order["work_order_no"]))
        self.handler.document_sender = upload
        await self.fill_to_shift()
        self.message("صبح")
        await self.settle()
        self.assertEqual(received, [("455740857", "AF-1405-06-15-001")])
        self.assertIn("تایید یا ویرایش را از دکمه‌های زیر", self.replies[-1])
        self.handler.pending.clear()
        async def confirm(request):
            self.requests.append(request)
            return {"ok": True, "review": {"bale_id": "455740857"}}
        self.handler.worker = confirm
        self.message("ثبت تأیید AF-1405-06-15-001")
        with patch('tools.fleet.work_orders.core.staff_dispatch.staff_menu', return_value=('TEST STAFF', [])):
            await self.settle()
        self.assertEqual(self.requests[-1]["action"], "confirm_review")
        self.assertIn("ثبت شد", self.replies[-1])
