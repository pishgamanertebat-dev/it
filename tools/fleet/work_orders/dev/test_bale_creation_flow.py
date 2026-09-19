from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace

from openpyxl import load_workbook

from tools.fleet.work_orders.channels.bale.message_handler import WorkOrderMenuHandler
from tools.fleet.work_orders.channels.bale import create_worker
from tools.fleet.work_orders.core.paths import PROJECT_ROOT
from tools.fleet.work_orders.dev.test_permissions import PermissionDatabaseTestCase, test_database


class CreationFlowTests(PermissionDatabaseTestCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.requests = []
        self.replies = []
        self.gate = None

        async def worker(request):
            self.requests.append(request)
            if self.gate:
                await self.gate.wait()
            if request["action"] == "validate_machines":
                return {"ok": True, "machine_codes": request["machine_codes"]}
            return {"ok": True, "order": {"work_order_no": "AF-1405-06-15-001", "label": "هواکش", "item_count": len(request["machine_codes"]), "status": "FILE_READY", "file_name": "AF-1405-06-15-001.xlsx"}}

        async def document_sender(gateway, chat, order):
            pass

        self.handler = WorkOrderMenuHandler(db_path=self.db_path, worker=worker, document_sender=document_sender)

    def message(self, text, message_id=None):
        event = SimpleNamespace(text=text, message_id=message_id, source=SimpleNamespace(platform="bale", chat_type="dm", user_id="455740857", chat_id="455740857"))
        return self.handler.handle(event, None, send=lambda gateway, chat, reply: self.replies.append(reply))

    async def settle(self):
        if self.handler.tasks:
            await asyncio.gather(*list(self.handler.tasks))

    async def fill_to_shift(self):
        self.start_manual()
        self.message("۴۶۵، ۷۱۰ ۷۱۱ ۷۱۲ ۷۱۳ ۷۱۴")
        await self.settle()
        self.message("۱۴۰۵/۰۶/۱۵")

    def start_manual(self):
        # Retain regression coverage for the existing manual form stages;
        # the new default proposal path has a real-plugin integration test.
        self.message('حکم کار')
        session = next(iter(self.handler.pending.values()))
        session.work_order_type = 'AIR_FILTER'
        session.stage = 'MACHINES'

    async def test_fields_reach_creator_and_result_reports_ready_file(self):
        await self.fill_to_shift()
        self.assertEqual(self.message("صبح")["reason"], "work-order-creating")
        await self.settle()
        request = self.requests[-1]
        self.assertEqual(request["bale_id"], "455740857")
        self.assertEqual(request["work_order_type"], "AIR_FILTER")
        self.assertEqual(request["machine_codes"], ["465","710","711","712","713","714"])
        self.assertEqual(request["jalali_date"], "1405/06/15")
        self.assertEqual(request["shift"], "صبح")
        self.assertIn("AF-1405-06-15-001", self.replies[-1])
        self.assertIn("تایید یا ویرایش را از دکمه‌های زیر", self.replies[-1])
        self.assertNotIn("تعداد دستگاه:", self.replies[-1])
        self.assertNotIn("آماده ارسال", self.replies[-1])
        self.assertNotIn("نوع:", self.replies[-1])

    async def test_invalid_date_does_not_advance_or_create(self):
        self.start_manual(); self.message("714")
        await self.settle()
        for text in ("1404/06/15", "1405/13/01", "1405/12/30"):
            self.assertEqual(self.message(text)["reason"], "work-order-input-rejected")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(next(iter(self.handler.pending.values())).stage, "DATE")

    async def test_revocation_before_create_blocks_write(self):
        await self.fill_to_shift()
        with test_database(self.db_path) as con:
            con.execute("UPDATE service_work_order_users SET active=0 WHERE bale_id='455740857'")
        self.assertEqual(self.message("صبح")["reason"], "work-order-permission-denied")
        self.assertEqual(len(self.requests), 1)

    async def test_repeated_message_and_busy_inputs_do_not_duplicate_creation(self):
        await self.fill_to_shift()
        self.gate = asyncio.Event()
        self.message("صبح", message_id="final-input")
        self.assertEqual(self.message("صبح", message_id="final-input")["reason"], "work-order-duplicate-message")
        self.assertEqual(self.message("حکم کار")["reason"], "work-order-busy")
        self.gate.set()
        await self.settle()
        self.assertEqual(len([r for r in self.requests if r["action"] == "create"]), 1)
        self.message("نتیجه")
        self.assertIn("AF-1405-06-15-001", self.replies[-1])

    async def test_builder_validation_error_preserves_machine_input_step(self):
        async def invalid(request):
            return {"ok": False, "error": "INVALID_INPUT", "message": "کد دستگاه تکراری"}
        self.handler.worker = invalid
        self.start_manual(); self.message("714 714")
        await self.settle()
        self.assertIn("کد دستگاه تکراری", self.replies[-1])
        self.assertEqual(next(iter(self.handler.pending.values())).stage, "MACHINES")

    async def test_cancel_before_creation_writes_nothing(self):
        await self.fill_to_shift()
        self.message("انصراف")
        self.assertEqual(self.handler.pending, {})
        self.assertEqual(len(self.requests), 1)

    async def test_failed_creation_is_not_automatically_retried(self):
        await self.fill_to_shift()
        async def fail(request):
            return {"ok": False, "error": "CREATE_FAILED", "message": "وضعیت حکم باید بررسی شود."}
        self.handler.worker = fail
        self.message("صبح")
        await self.settle()
        self.assertNotIn("✅", self.replies[-1])
        self.assertEqual(next(iter(self.handler.pending.values())).stage, "RESULT")


class RealWorkerProcessTests(PermissionDatabaseTestCase):
    def test_real_worker_creates_excel_in_isolated_database(self):
        schema = importlib.import_module("tools.fleet.work_orders.migrations.001_create_work_order_schema_v1")
        with test_database(self.db_path) as con:
            con.execute("CREATE TABLE machines (id INTEGER PRIMARY KEY, canonical_code TEXT)")
            schema.create_schema(con)
        import subprocess
        request = {"action": "create", "bale_id": "455740857", "work_order_type": "AIR_FILTER", "machine_codes": ["465","710","711","712","713","714"], "jalali_date": "1405/06/15", "shift": "صبح"}
        output_root = self.db_path.parent / "worker_output"
        result = subprocess.run(
            [str(PROJECT_ROOT / ".venv/Scripts/python.exe"), "-E", "-s", "-B", "-X", "utf8", "-m", "tools.fleet.work_orders.channels.bale.create_worker", "--test-db", str(self.db_path), "--test-output", str(output_root)],
            input=json.dumps(request), text=True, encoding="utf-8", capture_output=True,
            cwd=PROJECT_ROOT, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertTrue(response["ok"], response)
        with test_database(self.db_path) as con:
            row = con.execute("SELECT status,created_by,excel_path,assigned_staff_id,sent_at FROM service_work_orders").fetchone()
        self.assertEqual(row[0:2], ("FILE_READY", "bale:455740857"))
        self.assertEqual(row[3:], (None, None))
        wb = load_workbook(row[2], read_only=True)
        try:
            self.assertEqual(wb.active["F1"].value, "1405/06/15")
            self.assertEqual(str(wb.active["C8"].value), "714")
        finally:
            wb.close()

    def test_worker_denies_before_calling_creator(self):
        from unittest.mock import patch
        with patch.object(create_worker.service, "create_work_order") as create:
            result = create_worker.execute_request({"action": "create", "bale_id": "9999"}, db_path=self.db_path)
        self.assertEqual(result["error"], "DENIED")
        create.assert_not_called()
