"""Isolated roster/profile activation checks; no production DB or transport."""
from contextlib import closing
import importlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from integrations.hermes.deploy_service_staff_net import (
    HAMID, HUSSEIN, ROOT, change, verify_app,
)
from tools.authorization import AuthorizationStore
from tools.authorization.net import NET_MANAGER, NET_STAFF, migrate_net


class ServiceStaffNetTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT/'runtime/service-staff-net-tests'
        parent.mkdir(parents=True,exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.auth_path, self.fleet_path = self.root/'auth.db', self.root/'fleet.db'
        with closing(sqlite3.connect(self.auth_path)) as con, con:
            con.execute('CREATE TABLE channel_users (platform TEXT,user_id TEXT,chat_id TEXT,registration_status TEXT,PRIMARY KEY(platform,user_id))')
            con.executemany("INSERT INTO channel_users VALUES ('bale',?,?,'approved')", [(u,u) for u in (HAMID[0],HUSSEIN[0],'101')])
        self.store = AuthorizationStore(self.auth_path)
        self.store.migrate(self.root/'initial-backups')
        self.store.migrate_admin(self.root/'initial-backups')
        migrate_net(self.store,self.root/'initial-backups',assignments=[('101',NET_MANAGER)])
        with closing(sqlite3.connect(self.fleet_path)) as con, con:
            con.execute('CREATE TABLE machines (id INTEGER PRIMARY KEY)')
            importlib.import_module('tools.fleet.work_orders.migrations.001_create_work_order_schema_v1').create_schema(con)
            importlib.import_module('tools.fleet.work_orders.migrations.002_create_work_order_permissions').create_schema(con)
            from tools.fleet.work_orders.core.staff_dispatch import create_schema
            create_schema(con)
            con.executemany("INSERT INTO service_staff(id,display_name,bale_id,service_role,active) VALUES (?,?,?,'GENERAL',1)",
                            [(4,'محسن غضنفری','1432500464'),(5,HUSSEIN[1],HUSSEIN[0]),(3,'پوریا آسترکی','1294822197')])
            con.executemany('INSERT INTO service_staff_roster VALUES (?,?,?,1)',
                            [(1,'محسن غضنفری',4),(2,HUSSEIN[1],5),(3,'پوریا آسترکی',3)])
            con.execute('CREATE TABLE historical_dispatch (number TEXT, recipient TEXT)')
            con.execute("INSERT INTO historical_dispatch VALUES ('OLD-1','85539397')")

    def auth_sql(self, sql, args=()):
        with closing(sqlite3.connect(self.auth_path)) as con, con:
            return con.execute(sql,args).fetchall()

    def apply(self):
        return change(self.auth_path,self.fleet_path)

    def test_membership_and_fourth_recipient_preserve_manager_and_history(self):
        old_roles = self.auth_sql('SELECT * FROM auth_user_roles')
        old_markers = self.auth_sql('SELECT * FROM auth_domain_authority')
        self.apply()
        result = verify_app(self.auth_path,self.fleet_path)
        self.assertEqual(len(result['staff_options']),4)
        self.assertEqual(result['staff_options'][-1]['bale_id'],HAMID[0])
        for user in (HAMID[0],HUSSEIN[0]):
            self.assertEqual(self.store.roles(user),(NET_STAFF,))
            self.assertFalse(self.store.function_scope(user))
            self.assertFalse(self.store.has_capability(user,'work_orders.send'))
            self.assertIsNone(self.store.resolve_profile(user,'999'))
        self.assertEqual(self.store.capabilities_for_role(NET_STAFF),())
        self.assertEqual(self.auth_sql('SELECT * FROM auth_domain_authority'),old_markers)
        self.assertEqual(self.auth_sql("SELECT * FROM auth_user_roles WHERE user_id='101'"),old_roles)
        with closing(sqlite3.connect(self.fleet_path)) as con:
            self.assertEqual(con.execute('SELECT * FROM historical_dispatch').fetchall(),[('OLD-1','85539397')])

    def test_repeat_is_noop_including_audit(self):
        self.apply()
        before = self.auth_sql('SELECT * FROM auth_events')
        result = self.apply()
        self.assertEqual(result['changed_tables'],{'authorization':[],'fleet':[]})
        self.assertEqual(self.auth_sql('SELECT * FROM auth_events'),before)

    def assert_rolled_back(self):
        self.assertEqual(self.auth_sql('SELECT * FROM auth_roles WHERE role=?',(NET_STAFF,)),[])
        with closing(sqlite3.connect(self.fleet_path)) as con:
            self.assertEqual(con.execute('SELECT id FROM service_staff WHERE bale_id=?',(HAMID[0],)).fetchall(),[])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM service_staff_roster').fetchone(),(3,))

    def test_unapproved_identity_rolls_back_both_databases(self):
        self.auth_sql("UPDATE channel_users SET registration_status='pending_approval' WHERE user_id=?",(HUSSEIN[0],))
        with self.assertRaisesRegex(ValueError,'Approved private'):
            self.apply()
        self.assert_rolled_back()

    def test_profile_conflict_rolls_back_both_databases(self):
        self.store.assign_role(HUSSEIN[0],'office_supervisor',actor='fixture')
        with self.assertRaisesRegex(ValueError,'Conflicting effective profile'):
            self.apply()
        self.assert_rolled_back()

    def test_conflicting_recipient_is_not_overwritten(self):
        with closing(sqlite3.connect(self.fleet_path)) as con, con:
            con.execute("INSERT INTO service_staff(display_name,bale_id,service_role,active) VALUES ('Different person',?,'GENERAL',1)",(HAMID[0],))
        with self.assertRaisesRegex(ValueError,'Conflicting Hamid'):
            self.apply()
        self.assertEqual(self.auth_sql('SELECT * FROM auth_roles WHERE role=?',(NET_STAFF,)),[])

    def test_existing_staff_role_with_capabilities_is_rejected(self):
        self.auth_sql('INSERT INTO auth_roles VALUES (?,?)',(NET_STAFF,'نیروی نت'))
        self.auth_sql("INSERT INTO auth_role_capabilities VALUES (?,'work_orders.send')",(NET_STAFF,))
        with self.assertRaisesRegex(sqlite3.DatabaseError,'must not grant capabilities'):
            self.apply()
        with closing(sqlite3.connect(self.fleet_path)) as con:
            self.assertEqual(con.execute('SELECT id FROM service_staff WHERE bale_id=?',(HAMID[0],)).fetchall(),[])

    def test_backups_capture_both_original_databases(self):
        result = change(self.auth_path,self.fleet_path,self.root/'deployment-backups')
        self.assertEqual(len(result['backups']),2)
        with closing(sqlite3.connect(result['backups'][0]['path'])) as con:
            self.assertEqual(con.execute('SELECT role FROM auth_roles WHERE role=?',(NET_STAFF,)).fetchall(),[])
        with closing(sqlite3.connect(result['backups'][1]['path'])) as con:
            self.assertEqual(con.execute('SELECT id FROM service_staff WHERE bale_id=?',(HAMID[0],)).fetchall(),[])


if __name__ == '__main__':
    unittest.main()
