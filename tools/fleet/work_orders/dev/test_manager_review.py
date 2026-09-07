from unittest.mock import patch
from openpyxl import load_workbook

from tools.fleet.work_orders.core.review import normalize_shift, confirm_document_review
from tools.fleet.work_orders.dev.test_bale_work_order_create import WorkOrderCreateTests
from tools.fleet.work_orders.dev.test_permissions import test_database
from tools.fleet.work_orders.dev.test_bale_creation_flow import CreationFlowTests


class ManagerReviewTests(WorkOrderCreateTests):
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
    async def test_document_failure_keeps_order_and_offers_retry(self):
        async def fail(*args):
            raise RuntimeError("TEST UPLOAD FAILURE")
        self.handler.document_sender = fail
        await self.fill_to_shift()
        with self.assertLogs("tools.fleet.work_orders.channels.bale.message_handler", level="ERROR"):
            self.message("صبح ظهر")
            await self.settle()
        self.assertIn("حکم محفوظ است", self.replies[-1])
        self.assertIn("ارسال مجدد AF-", self.replies[-1])
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
        self.assertIn("آیا تایید می‌کنید", self.replies[-1])
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
