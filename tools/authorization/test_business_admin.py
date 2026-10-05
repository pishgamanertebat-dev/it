"""Business Admin generic routing: fixtures only, no production writes or sends."""
from contextlib import closing
from pathlib import Path
import sqlite3, unittest
from tools.authorization.test_authorization import Fixture
from tools.authorization import BUSINESS_ADMIN, MECHANICAL_STAFF, FUNCTION_READ

class BusinessAdminRouting(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.auth.migrate_admin(self.directory/'backups')
        self.auth.migrate_mechanical(self.directory/'backups')

    def migrate(self, assignments=()):
        return self.auth.migrate_business_admin_profile(self.directory/'backups', assignments=assignments, actor='fixture')

    def snapshot(self):
        return {t:self.sql('SELECT * FROM '+t+' ORDER BY 1,2') for t in
                ['channel_users','auth_roles','auth_capabilities','auth_role_capabilities','auth_role_profiles','auth_user_roles','auth_events','auth_extensions','auth_migrations']}

    def test_priority_business_only_staff_and_supervisor(self):
        self.migrate([('101',BUSINESS_ADMIN),('202',BUSINESS_ADMIN)])
        self.assertEqual(self.auth.resolve_profile('101','101'),'admin')
        self.auth.assign_role('101',MECHANICAL_STAFF,actor='fixture')
        self.assertEqual(self.auth.resolve_profile('101','101'),'maintenance')
        self.assign('202')
        self.assertEqual(self.auth.resolve_profile('202','202'),'admin')
        self.assertEqual(self.sql("SELECT profile,priority,active FROM auth_role_profiles WHERE role='business_admin'"), [('admin',10,1)])
        self.assertTrue(self.auth.function_scope('101').all)
        self.assertTrue(self.auth.has_capability('101',FUNCTION_READ))

    def test_backup_idempotency_no_new_capabilities_or_push(self):
        before=self.snapshot()
        backup=self.migrate([('101',BUSINESS_ADMIN)])
        with closing(sqlite3.connect(backup)) as c:
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchall(),[('ok',)])
            self.assertEqual(c.execute('SELECT * FROM auth_user_roles').fetchall(),before['auth_user_roles'])
        after=self.snapshot()
        for t in ['channel_users','auth_roles','auth_capabilities','auth_role_capabilities','auth_migrations']:
            self.assertEqual(before[t],after[t])
        self.migrate([('101',BUSINESS_ADMIN)])
        self.assertEqual(after,self.snapshot())
        self.assertEqual(set(self.auth.capabilities_for_role(BUSINESS_ADMIN)),{FUNCTION_READ})
        for cap, in self.sql('SELECT capability FROM auth_capabilities'):
            if cap.endswith('daily_receive'):
                self.assertFalse(self.auth.has_capability('101',cap))
                self.assertNotIn('101',self.auth.resolve_active_recipients(cap).recipients)

    def test_unapproved_nonprivate_missing_identity_rolls_back_mapping_and_all_assignments(self):
        for user in ['303','999','202']:
            with self.subTest(user=user):
                if user=='202':self.sql("UPDATE channel_users SET chat_id='group' WHERE user_id='202'")
                before=self.snapshot()
                with self.assertRaises(ValueError):self.migrate([('101',BUSINESS_ADMIN),(user,MECHANICAL_STAFF)])
                self.assertEqual(before,self.snapshot())

    def test_conflicting_mapping_write_grant_or_specialist_priority_rolls_back(self):
        changes=["INSERT INTO auth_role_profiles VALUES ('business_admin','maintenance',10,1)",
                 "UPDATE auth_role_profiles SET priority=10 WHERE role='mechanical_staff'",
                 "INSERT INTO auth_capabilities VALUES ('maintenance.entries.edit','fixture')",
                 "UPDATE auth_capabilities SET resource='C:\\' WHERE capability='function.read_all'"]
        for change in changes:
            backup=self.auth.backup(self.directory/'backups')
            try:
                self.sql(change)
                if 'maintenance.entries.edit' in change:
                    self.sql("INSERT INTO auth_role_capabilities VALUES ('business_admin','maintenance.entries.edit')")
                before=self.snapshot()
                with self.assertRaises(sqlite3.DatabaseError):self.migrate([('101',BUSINESS_ADMIN)])
                self.assertEqual(before,self.snapshot())
            finally:
                with closing(sqlite3.connect(backup)) as src,closing(sqlite3.connect(self.path)) as dest:src.backup(dest)

    def test_ambiguous_highest_priority_fails_closed(self):
        self.migrate([('101',BUSINESS_ADMIN),('101',MECHANICAL_STAFF)])
        self.assign('101')
        self.assertIsNone(self.auth.resolve_profile('101','101'))
        self.assertTrue(self.auth.function_scope('101').all)

    def test_revocation_nonprivate_and_platform_do_not_get_profile(self):
        self.migrate([('101',BUSINESS_ADMIN)])
        self.assertIsNone(self.auth.resolve_profile('101','group'))
        self.assertIsNone(self.auth.resolve_profile('101','101','telegram'))
        self.sql("UPDATE channel_users SET registration_status='revoked' WHERE user_id='101'")
        self.assertIsNone(self.auth.resolve_profile('101','101'))
        self.assertFalse(self.auth.function_scope('101'))

    def test_no_unrequested_responsibility_role(self):
        before=self.snapshot()
        with self.assertRaises(ValueError):self.migrate([('101','mechanical_manager')])
        self.assertEqual(before,self.snapshot())

if __name__=='__main__':unittest.main()
