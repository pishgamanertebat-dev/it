from contextlib import closing
import importlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from integrations.hermes.deploy_car_wash_recipient import change, verify_identity
from tools.fleet.work_orders.core.paths import PROJECT_ROOT
from tools.fleet.work_orders.core.staff_dispatch import create_schema


class CarWashRecipientDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT / 'runtime')
        self.addCleanup(self.temp.cleanup)
        self.fleet = Path(self.temp.name) / 'fleet.db'
        with closing(sqlite3.connect(self.fleet)) as con, con:
            con.execute('CREATE TABLE machines(id INTEGER PRIMARY KEY)')
            importlib.import_module('tools.fleet.work_orders.migrations.001_create_work_order_schema_v1').create_schema(con)
            create_schema(con)
            con.execute('CREATE TABLE historical_receipts(number TEXT,recipient TEXT)')
            con.execute("INSERT INTO historical_receipts VALUES ('OLD-1','85539397')")

    def test_addition_is_idempotent_and_preserves_receipts_and_general_roster(self):
        result = change(self.fleet)
        self.assertEqual(result['changed_tables'], ['service_staff'])
        self.assertEqual(result['car_wash_options'][0]['bale_id'], '1028780913')
        self.assertEqual(result['service_options'], [])
        self.assertEqual(change(self.fleet)['changed_tables'], [])
        with closing(sqlite3.connect(self.fleet)) as con:
            self.assertEqual(con.execute('SELECT * FROM historical_receipts').fetchall(), [('OLD-1', '85539397')])
            self.assertEqual(con.execute('SELECT * FROM service_staff_roster').fetchall(), [])

    def test_conflicting_existing_identity_is_preserved(self):
        with closing(sqlite3.connect(self.fleet)) as con, con:
            con.execute("INSERT INTO service_staff(display_name,bale_id,service_role) VALUES ('Other person','1028780913','GENERAL')")
        before = self.fleet.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Conflicting recipient identity'):
            change(self.fleet)
        self.assertEqual(self.fleet.read_bytes(), before)

    def test_only_owner_named_approved_private_identity_is_accepted(self):
        auth = Path(self.temp.name) / 'auth.db'
        with closing(sqlite3.connect(auth)) as con, con:
            con.execute('CREATE TABLE channel_users(platform TEXT,user_id TEXT,chat_id TEXT,verified_name TEXT,registration_status TEXT)')
            con.execute("INSERT INTO channel_users VALUES ('bale','1028780913','1028780913','مهدی کارگری','approved')")
        verify_identity(auth)
        for column, value in [('registration_status', 'pending'), ('chat_id', '999'), ('verified_name', 'Other person')]:
            with closing(sqlite3.connect(auth)) as con, con:
                con.execute("UPDATE channel_users SET chat_id='1028780913',verified_name='مهدی کارگری',registration_status='approved'")
                con.execute(f'UPDATE channel_users SET {column}=?', (value,))
            with self.assertRaises(ValueError):
                verify_identity(auth)


if __name__ == '__main__':
    unittest.main()
