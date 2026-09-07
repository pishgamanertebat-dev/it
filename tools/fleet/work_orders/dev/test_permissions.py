from __future__ import annotations

import importlib
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from tools.fleet.work_orders.core.paths import PROJECT_ROOT
from tools.fleet.work_orders.core.permissions import (
    WorkOrderPermissionDenied,
    check_work_order_permission,
    require_work_order_permission,
)


create_schema = importlib.import_module(
    "tools.fleet.work_orders.migrations.002_create_work_order_permissions"
).create_schema


@contextmanager
def test_database(path):
    con = sqlite3.connect(path)
    try:
        with con:
            yield con
    finally:
        con.close()


class PermissionDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        root = PROJECT_ROOT / "runtime" / "work_order_permissions_tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "permissions.db"
        with test_database(self.db_path) as con:
            create_schema(con)
            con.executemany(
                "INSERT INTO service_work_order_users (bale_id, role, active) VALUES (?, ?, ?)",
                [
                    ("455740857", "MAINTENANCE_MANAGER", 1),
                    ("1002", "MAINTENANCE_MANAGER", 0),
                    ("1003", "AIR_FILTER", 1),
                    ("1004", "ADMIN", 1),
                ],
            )

    def check(self, bale_id):
        return check_work_order_permission(bale_id, db_path=self.db_path)


class PermissionTests(PermissionDatabaseTestCase):
    def test_active_manager_is_allowed_as_string_or_integer(self):
        for bale_id in ("455740857", 455740857, " 455740857 "):
            with self.subTest(bale_id=bale_id):
                result = self.check(bale_id)
                self.assertTrue(result.allowed)
                self.assertEqual(result.status, "ALLOWED")
                self.assertEqual(result.role, "MAINTENANCE_MANAGER")

    def test_unknown_inactive_and_other_roles_are_denied(self):
        for bale_id, reason in (
            ("9999", "UNKNOWN_USER"),
            ("1002", "INACTIVE_USER"),
            ("1003", "ROLE_NOT_ALLOWED"),
            ("1004", "ROLE_NOT_ALLOWED"),
        ):
            with self.subTest(bale_id=bale_id):
                result = self.check(bale_id)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason, reason)

    def test_invalid_ids_cannot_authorize(self):
        for value in (None, True, False, 0, -1, 455740857.0, "", "@manager", "0", "0455740857", "1 OR 1=1", "455740857\n1"):
            with self.subTest(value=value):
                self.assertEqual(self.check(value).reason, "INVALID_BALE_ID")

    def test_permission_changes_are_observed_without_cache(self):
        self.assertTrue(self.check("455740857").allowed)
        with test_database(self.db_path) as con:
            con.execute("UPDATE service_work_order_users SET active=0 WHERE bale_id='455740857'")
        self.assertEqual(self.check("455740857").reason, "INACTIVE_USER")

    def test_missing_database_does_not_create_a_file(self):
        missing = self.db_path.parent / "missing.db"
        result = check_work_order_permission("455740857", db_path=missing)
        self.assertEqual(result.reason, "PERMISSION_STORE_UNAVAILABLE")
        self.assertFalse(missing.exists())

    def test_missing_schema_does_not_create_table(self):
        empty = self.db_path.parent / "empty.db"
        sqlite3.connect(empty).close()
        self.assertFalse(check_work_order_permission("455740857", db_path=empty).allowed)
        with test_database(empty) as con:
            self.assertEqual(con.execute("SELECT name FROM sqlite_master").fetchall(), [])

    def test_corrupt_database_is_denied(self):
        corrupt = self.db_path.parent / "corrupt.db"
        corrupt.write_bytes(b"not a SQLite database")
        self.assertEqual(check_work_order_permission("455740857", db_path=corrupt).reason, "PERMISSION_STORE_UNAVAILABLE")

    def test_staff_membership_does_not_grant_permission(self):
        with test_database(self.db_path) as con:
            con.execute("CREATE TABLE service_staff (bale_id TEXT, service_role TEXT, active INTEGER)")
            con.execute("INSERT INTO service_staff VALUES ('9999', 'MAINTENANCE_MANAGER', 1)")
        self.assertEqual(self.check("9999").reason, "UNKNOWN_USER")

    def test_checks_do_not_modify_database(self):
        before = self.db_path.read_bytes()
        self.check("455740857")
        self.check("9999")
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_require_raises_with_denial_reason(self):
        with self.assertRaises(WorkOrderPermissionDenied) as error:
            require_work_order_permission("1002", db_path=self.db_path)
        self.assertEqual(error.exception.result.reason, "INACTIVE_USER")

    def test_schema_is_idempotent_and_does_not_grant_access(self):
        with test_database(self.db_path) as con:
            before = con.execute("SELECT * FROM service_work_order_users").fetchall()
            create_schema(con)
            self.assertEqual(con.execute("SELECT * FROM service_work_order_users").fetchall(), before)
            con.execute("INSERT INTO service_work_order_users (bale_id, role) VALUES ('1005', 'MAINTENANCE_MANAGER')")
        self.assertEqual(self.check("1005").reason, "INACTIVE_USER")


if __name__ == "__main__":
    unittest.main()
