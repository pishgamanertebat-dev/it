from __future__ import annotations

import unittest
import importlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from tools.fleet.work_orders.channels.bale.work_order_menu import (
    InvalidWorkOrderSelection,
    WorkOrderTypeDisabled,
    build_work_order_menu,
    resolve_work_order_selection,
)
from tools.fleet.work_orders.core import registry, service
from tools.fleet.work_orders.core.assignment import assign_work_order
from tools.fleet.work_orders.core.approval import approve_work_order
from tools.fleet.work_orders.core.delivery import send_work_order
from tools.fleet.work_orders.core.permissions import WorkOrderPermissionDenied
from tools.fleet.work_orders.dev.test_permissions import (
    PermissionDatabaseTestCase,
    test_database,
)


class WorkOrderMenuTests(PermissionDatabaseTestCase):
    def menu(self, bale_id="455740857"):
        return build_work_order_menu(bale_id=bale_id, db_path=self.db_path)

    def select(self, value, bale_id="455740857"):
        return resolve_work_order_selection(value, bale_id=bale_id, db_path=self.db_path)

    def test_menu_lists_three_types_in_requested_order(self):
        self.assertEqual(
            self.menu().splitlines()[-3:],
            ["1) هواکش", "2) تعویض روغن (به‌زودی)", "3) گریس‌کاری"],
        )

    def test_air_filter_selection_accepts_persian_and_arabic_digits(self):
        for value in (1, "1", "۱", "١", " ۱ "):
            with self.subTest(value=value):
                item = self.select(value)
                self.assertEqual(item["key"], "AIR_FILTER")
                self.assertTrue(item["enabled"])

    def test_future_types_cannot_be_selected(self):
        for value in (2,):
            with self.subTest(value=value), self.assertRaises(WorkOrderTypeDisabled):
                self.select(value)

    def test_invalid_selections_are_rejected(self):
        for value in (0, -1, 4, "", "text", True, 1.0, "1.0", None, "9" * 5000):
            with self.subTest(value=str(value)[:20]), self.assertRaises(InvalidWorkOrderSelection):
                self.select(value)

    def test_unauthorized_users_cannot_view_or_select(self):
        for user in ("9999", "1002", "1003", "1004"):
            with self.subTest(user=user):
                with self.assertRaises(WorkOrderPermissionDenied):
                    self.menu(user)
                with self.assertRaises(WorkOrderPermissionDenied):
                    self.select(1, user)

    def test_permission_is_checked_before_registry_lookup(self):
        with patch("tools.fleet.work_orders.channels.bale.work_order_menu.list_work_order_types") as lookup:
            with self.assertRaises(WorkOrderPermissionDenied):
                self.menu("9999")
            with self.assertRaises(WorkOrderPermissionDenied):
                self.select(1, "9999")
            lookup.assert_not_called()

    def test_revocation_after_display_blocks_selection(self):
        self.menu()
        with test_database(self.db_path) as con:
            con.execute("UPDATE service_work_order_users SET active=0 WHERE bale_id='455740857'")
        with self.assertRaises(WorkOrderPermissionDenied):
            self.select(1)

    def test_menu_reflects_registry_labels_and_enabled_state(self):
        spec = replace(registry.get_work_order_spec("AIR_FILTER"), label_fa="عنوان آزمایشی", operational=False)
        with patch.dict(registry.WORK_ORDER_REGISTRY, AIR_FILTER=spec):
            self.assertIn("1) عنوان آزمایشی (به‌زودی)", self.menu())
            with self.assertRaises(WorkOrderTypeDisabled):
                self.select(1)

    def test_menu_and_selection_do_not_write_to_database(self):
        before = self.db_path.read_bytes()
        self.menu()
        self.select(1)
        self.assertEqual(before, self.db_path.read_bytes())


class RegistryCompatibilityTests(unittest.TestCase):
    def test_dictionary_and_core_interfaces_agree(self):
        self.assertEqual(len(registry.list_work_order_specs()), 3)
        self.assertEqual([item["key"] for item in registry.list_work_order_types()], ["AIR_FILTER", "GREASING"])
        for item in registry.list_work_order_types(enabled_only=False):
            spec = registry.get_work_order_spec(item["key"])
            self.assertEqual(item["enabled"], spec.operational)
            self.assertEqual(item["label"], spec.label_fa)
            self.assertEqual(item["builder"], spec.builder_module)
        self.assertEqual(registry.get_work_order_spec("AIR_FILTER").number_prefix, "AF")

    def test_core_can_load_air_filter_builder(self):
        builder = service._load_builder("AIR_FILTER")
        self.assertTrue(callable(builder.get_items))
        self.assertTrue(callable(builder.build_document))

    def test_core_rejects_future_types_before_import_or_database_access(self):
        with patch.object(service, "connect_db") as connect, patch.object(service.importlib, "import_module") as load:
            for key in ("OIL_CHANGE",):
                with self.subTest(key=key):
                    with self.assertRaises(RuntimeError):
                        service._load_builder(key)
                    with self.assertRaises(RuntimeError):
                        service.create_work_order(work_order_type=key, jalali_date="1405/06/11", shift="TEST", machine_codes=["714"], created_by="TEST")
            connect.assert_not_called()
            load.assert_not_called()

    def test_unknown_type_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "UNKNOWN WORK ORDER TYPE"):
            registry.get_work_order_type("UNKNOWN")


class CoreLifecycleRegressionTests(PermissionDatabaseTestCase):
    def test_registry_change_preserves_create_assign_approve_send(self):
        schema = importlib.import_module(
            "tools.fleet.work_orders.migrations.001_create_work_order_schema_v1"
        )
        with test_database(self.db_path) as con:
            con.execute("CREATE TABLE machines (id INTEGER PRIMARY KEY, canonical_code TEXT)")
            con.execute("INSERT INTO machines VALUES (1, 'HD714')")
            schema.create_schema(con)
            con.execute("INSERT INTO service_staff (id, display_name, bale_id, service_role, active) VALUES (1, 'TEST STAFF', '1003', 'AIR_FILTER', 1)")

        def build_document(*, output_path, jalali_date, items, shift):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"TEST DOCUMENT - NOT FOR DELIVERY")

        builder = SimpleNamespace(
            get_items=lambda codes: [{"machine_code": "714", "machine_name": "TEST", "action_code": "AIR_FILTER_OUTER", "action_text": "TEST"}],
            build_document=build_document,
        )
        deliveries = []
        sender = SimpleNamespace(send_document=lambda **kwargs: deliveries.append(kwargs) or {"ok": True})
        with (
            patch("tools.fleet.work_orders.core.db.DB_PATH", self.db_path),
            patch.object(service, "WORK_ORDER_OUTPUT_ROOT", self.db_path.parent / "orders"),
            patch.object(service.importlib, "import_module", return_value=builder),
        ):
            order = service.create_work_order(work_order_type="AIR_FILTER", jalali_date="1405/06/11", shift="TEST", machine_codes=["714"], created_by="TEST")
            number = order["work_order_no"]
            self.assertEqual(number, "AF-1405-06-11-001")
            self.assertEqual(order["status"], "FILE_READY")
            self.assertEqual(order["work_order_label_fa"], "هواکش")
            self.assertEqual(len(service.list_eligible_staff("AIR_FILTER")), 1)
            with self.assertRaises(RuntimeError):
                approve_work_order(work_order_no=number, approved_by="TEST")
            with self.assertRaises(RuntimeError):
                send_work_order(work_order_no=number, sender=sender)
            self.assertEqual(deliveries, [])
            self.assertEqual(assign_work_order(work_order_no=number, staff_id=1)["status"], "ASSIGNED")
            self.assertEqual(approve_work_order(work_order_no=number, approved_by="TEST")["status"], "APPROVED")
            self.assertEqual(send_work_order(work_order_no=number, sender=sender)["status"], "SENT")
            self.assertEqual(service.get_work_order(number)["status"], "SENT")
            self.assertEqual(len(deliveries), 1)


if __name__ == "__main__":
    unittest.main()
