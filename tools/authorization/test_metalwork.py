"""Metalwork acceptance: synthetic identities, native-export stub and fake transport only."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import sqlite3
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import fitz
import yaml
from tools.admin.test_admin1 import AdminFixture
from tools.admin import test_driver_pdf as pdf_fixture
from tools.authorization import (
    METALWORK_STAFF, METALWORK_DRIVER_RECEIVE, OFFICE_SUPERVISOR,
    MECHANICAL_STAFF, MECHANICAL_MANAGER, MECH_DRIVER_RECEIVE,
    MECH_OVERFLOW_RECEIVE, DRIVER_RECEIVE, DAILY_RECEIVE,
)
from tools.scheduler.tasks import metalwork_driver_daily, overflow_report_date
from tools.scheduler.runner import load_config, tick
from integrations.hermes import role_routing
from integrations.hermes.function_domain.driver_report import build_driver_pdf, SOURCE_NAME

TARGET = '387679249'  # Synthetic fixture identity; never a transport destination here.


class MetalworkFixture(AdminFixture):
    def setUp(self):
        super().setUp()
        self.auth.migrate_mechanical(self.directory/'backups')
        self.sql('INSERT INTO channel_users VALUES (?,?,?,?)', ('bale', TARGET, TARGET, 'approved'))
        self.auth.migrate_metalwork(self.directory/'backups', assignments=[TARGET])
        self.workbook([('new','1405/07/12','TODAY_MECH','TODAY_METAL'),
                       ('requested','1405/07/10','MECH_ONLY','METAL_ONLY'),
                       ('oldlast','1405/07/09','OLD_MECH','OLD_METAL')])
        self.original = (self.data/SOURCE_NAME).read_bytes()
        self.sender = Mock()
        self.outputs = []

    def snapshot(self):
        return {t: self.sql('SELECT * FROM '+t+' ORDER BY 1,2') for t in
                ['channel_users','auth_roles','auth_capabilities','auth_role_capabilities',
                 'auth_role_profiles','auth_user_roles','auth_events','auth_extensions','auth_migrations']}

    def build(self, date, directory, *, sections, missing_result=False):
        self.assertEqual(sections, ('metalwork',))
        with patch('tools.fleet.repairs.report.export_pdf', side_effect=lambda *a: pdf_fixture.DriverPDFTests.exporter(self, *a)):
            result = build_driver_pdf(date, directory, self.reader, sections=sections, missing_result=missing_result)
        self.outputs.extend(result['documents'])
        return result

    def deliver(self, date='1405/07/10', builder=None):
        with patch('integrations.hermes.function_domain.driver_report.build_driver_pdf', side_effect=builder or self.build), \
             patch('tools.scheduler.tasks.BaleSender', return_value=self.sender) as factory:
            result = metalwork_driver_daily('capability:'+METALWORK_DRIVER_RECEIVE, {'date':date}, authorization=self.auth)
        self.assertTrue(all(not Path(p).exists() for p in self.outputs), 'Temporary delivery PDFs leaked')
        return result, factory


class MetalworkAuthorization(MetalworkFixture, unittest.TestCase):
    def test_target_profile_role_capability_no_operational_scope(self):
        self.assertEqual(self.auth.roles(TARGET), (METALWORK_STAFF,))
        self.assertEqual(self.sql('SELECT display_name FROM auth_roles WHERE role=?',(METALWORK_STAFF,)),[('نیروی آهنگری',)])
        self.assertEqual(self.auth.resolve_profile(TARGET,TARGET), 'maintenance')
        self.assertEqual(self.auth.capabilities_for_role(METALWORK_STAFF), (METALWORK_DRIVER_RECEIVE,))
        self.assertTrue(self.auth.has_capability(TARGET, METALWORK_DRIVER_RECEIVE))
        self.assertFalse(self.auth.function_scope(TARGET))
        for cap in ['maintenance.records.read','function.read_all','repairs.driver_report.read',
                    'reports.driver_daily.read','reports.overflow.read','maintenance.entries.edit',
                    'work_orders.manage',MECH_DRIVER_RECEIVE,MECH_OVERFLOW_RECEIVE]:
            self.assertFalse(self.auth.has_capability(TARGET,cap),cap)
        self.assertNotIn(MECHANICAL_STAFF,self.auth.roles(TARGET))

    def test_idempotent_full_snapshot_and_backup_health(self):
        before = self.snapshot()
        for _ in range(2):
            backup=self.auth.migrate_metalwork(self.directory/'backups',assignments=[TARGET,TARGET])
            with closing(sqlite3.connect(backup)) as c:
                self.assertEqual(c.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
                self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.sql('SELECT version FROM auth_migrations ORDER BY version'),[(1,),(2,)])
        self.auth.migrate_metalwork(self.directory/'backups',assignments=['202','202'])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM auth_events WHERE user_id='202' AND role='metalwork_staff'"),[(1,)])

    def test_unapproved_nonprivate_unknown_assignment_rollback(self):
        for user in ['303','999','202']:
            if user=='202':self.sql("UPDATE channel_users SET chat_id='group' WHERE user_id='202'")
            before=self.snapshot()
            with self.assertRaises(ValueError):
                self.auth.migrate_metalwork(self.directory/'backups',assignments=[TARGET,user])
            self.assertEqual(before,self.snapshot())

    def test_conflicting_profile_and_existing_operational_grant_refused(self):
        self.assign('101')
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'effective profile'):
            self.auth.migrate_metalwork(self.directory/'backups',assignments=['101'])
        self.assertEqual(before,self.snapshot())
        self.auth.assign_role('202',MECHANICAL_STAFF,actor='fixture')
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'operational grants'):
            self.auth.migrate_metalwork(self.directory/'backups',assignments=['202'])
        self.assertEqual(before,self.snapshot())

    def test_conflicting_role_grant_resource_or_mapping_refused(self):
        for statement in ["INSERT INTO auth_role_capabilities VALUES ('metalwork_staff','function.read_all')",
                          "UPDATE auth_role_profiles SET profile='admin' WHERE role='metalwork_staff'",
                          "UPDATE auth_capabilities SET resource='E:\\Function' WHERE capability='reports.driver_daily.metalwork_daily_receive'"]:
            backup=self.auth.backup(self.directory/'backups')
            try:
                self.sql(statement);before=self.snapshot()
                with self.assertRaises(sqlite3.DatabaseError):
                    self.auth.migrate_metalwork(self.directory/'backups',assignments=[TARGET])
                self.assertEqual(before,self.snapshot())
            finally:
                with closing(sqlite3.connect(backup)) as src,closing(sqlite3.connect(self.path)) as dest:src.backup(dest)

    def test_native_resolver_ambiguity_and_revocation_retained(self):
        self.assign(TARGET)
        self.assertIsNone(self.auth.resolve_profile(TARGET,TARGET))
        self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id=? AND role=?",(TARGET,OFFICE_SUPERVISOR))
        self.assertEqual(self.auth.resolve_profile(TARGET,TARGET),'maintenance')
        self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id=?",(TARGET,))
        self.assertIsNone(self.auth.resolve_profile(TARGET,TARGET))
        self.assertEqual(self.auth.resolve_active_recipients(METALWORK_DRIVER_RECEIVE).recipients,())

    def test_on_demand_worker_and_toolsets_deny_function_access(self):
        for op,args in [('list',{}),('read',{'path':SOURCE_NAME}),('attach',{'path':SOURCE_NAME}),
                        ('report',{'kind':'driver_daily','date':'1405/07/10'})]:
            with self.assertRaises(PermissionError):
                self.request(op,args,TARGET)
        base=['web','skills_readonly','delegation','komatso_public_browser','komatso_technical_docs','no_mcp','komatso_function']
        with patch.object(role_routing,'AuthorizationStore',return_value=self.auth):
            actual=role_routing.resolve_toolsets(platform='bale',user_id=TARGET,chat_id=TARGET,chat_type='dm',base_toolsets=base)
        self.assertEqual(set(actual),set(base)-{'komatso_function'})

    def test_existing_office_mechanical_sets_and_roles_preserved(self):
        self.assign('101')
        self.auth.assign_role('202',MECHANICAL_STAFF,actor='fixture')
        self.auth.assign_role('202',MECHANICAL_MANAGER,actor='fixture')
        caps=[DAILY_RECEIVE,DRIVER_RECEIVE,MECH_DRIVER_RECEIVE,MECH_OVERFLOW_RECEIVE]
        before={cap:self.auth.resolve_active_recipients(cap) for cap in caps}
        users={u:(self.auth.roles(u),self.auth.resolve_profile(u,u),self.auth.function_scope(u)) for u in ['101','202']}
        self.auth.migrate_metalwork(self.directory/'backups',assignments=[TARGET])
        self.assertEqual(before,{cap:self.auth.resolve_active_recipients(cap) for cap in caps})
        self.assertEqual(users,{u:(self.auth.roles(u),self.auth.resolve_profile(u,u),self.auth.function_scope(u)) for u in users})


class MetalworkDelivery(MetalworkFixture, unittest.TestCase):
    def test_metalwork_only_exact_date_searchable_vector_source_unchanged_cleanup(self):
        def inspect(user,path,caption):
            with fitz.open(path) as pdf:
                text=''.join(p.get_text() for p in pdf)
                self.assertTrue(all(not p.get_images() for p in pdf))
            self.assertIn('METAL_ONLY',text)
            for token in ['MECH_ONLY','TODAY_','OLD_']:self.assertNotIn(token,text)
            self.assertEqual(user,TARGET)
            self.assertIn('1405/07/10',caption)
        self.sender.document.side_effect=inspect
        result,_=self.deliver()
        self.assertEqual(result['sent_count'],1)
        self.sender.document.assert_called_once();self.sender.close.assert_called_once()
        self.assertEqual(hashlib.sha256((self.data/SOURCE_NAME).read_bytes()).digest(),hashlib.sha256(self.original).digest())

    def test_missing_date_safely_skips_no_old_fallback_no_transport(self):
        result,factory=self.deliver('1405/07/11')
        self.assertEqual(result['status'],'waiting_for_data')
        self.assertEqual(result['reason'],'date_missing')
        self.sender.document.assert_not_called()
        self.sender.message.assert_called_once()
        factory.assert_called_once()

    def test_zero_recipients_skips_before_export(self):
        self.sql('UPDATE auth_user_roles SET active=0 WHERE user_id=?',(TARGET,))
        builder=Mock();result,factory=self.deliver(builder=builder)
        self.assertEqual(result['status'],'skipped');builder.assert_not_called();factory.assert_not_called()

    def test_revoked_role_capability_or_identity_during_export_no_send(self):
        for statement in ['UPDATE auth_user_roles SET active=0 WHERE user_id=?',
                          'DELETE FROM auth_role_capabilities WHERE role=?',
                          "UPDATE channel_users SET registration_status='revoked' WHERE user_id=?"]:
            backup=self.auth.backup(self.directory/'backups')
            try:
                def revoke(*a,**kw):
                    result=self.build(*a,**kw)
                    self.sql(statement,(METALWORK_STAFF if 'capabilities' in statement else TARGET,))
                    return result
                result,factory=self.deliver(builder=revoke)
                self.assertEqual(result['status'],'skipped');factory.assert_not_called()
            finally:
                with closing(sqlite3.connect(backup)) as src,closing(sqlite3.connect(self.path)) as dest:src.backup(dest)

    def test_multiple_recipients_dedup_and_delivery_failure_isolated(self):
        self.auth.migrate_metalwork(self.directory/'backups',assignments=['202'])
        self.sql("INSERT INTO auth_roles VALUES ('synthetic_push','fixture')")
        self.sql('INSERT INTO auth_role_capabilities VALUES (?,?)',('synthetic_push',METALWORK_DRIVER_RECEIVE))
        self.auth.assign_role(TARGET,'synthetic_push',actor='fixture')
        self.assertEqual(self.auth.resolve_active_recipients(METALWORK_DRIVER_RECEIVE).recipients,('202',TARGET))
        def fail_one(user,*args):
            if user=='202':
                from tools.scheduler.tasks import TransportUnavailable
                raise TransportUnavailable('synthetic definite pre-send failure')
        self.sender.document.side_effect=fail_one
        result,_=self.deliver()
        self.assertEqual([c.args[0] for c in self.sender.document.call_args_list],['202',TARGET])
        self.assertEqual(result['sent_count'],1);self.assertEqual(result['failed_count'],1)
        self.assertEqual(result['status'],'failed')

    def test_multiple_recipients_all_receive_once_without_read_grant(self):
        self.auth.migrate_metalwork(self.directory/'backups',assignments=['202'])
        result,_=self.deliver()
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['sent_count'],2)
        self.assertEqual([c.args[0] for c in self.sender.document.call_args_list],['202',TARGET])
        self.assertFalse(self.auth.function_scope('202'))

    def test_revocation_during_transport_initialization_prevents_upload(self):
        def initialize():
            self.sql('UPDATE auth_user_roles SET active=0 WHERE user_id=?',(TARGET,))
            return self.sender
        with patch('integrations.hermes.function_domain.driver_report.build_driver_pdf',side_effect=self.build), \
             patch('tools.scheduler.tasks.BaleSender',side_effect=initialize):
            result=metalwork_driver_daily('capability:'+METALWORK_DRIVER_RECEIVE,{'date':'1405/07/10'},authorization=self.auth)
        self.assertEqual(result['status'],'skipped')
        self.sender.document.assert_not_called();self.sender.close.assert_called_once()
        self.assertTrue(all(not Path(p).exists() for p in self.outputs))

    def test_nonprivate_and_unapproved_recipients_excluded(self):
        for statement in ["UPDATE channel_users SET chat_id='group' WHERE user_id=?",
                          "UPDATE channel_users SET registration_status='pending_approval' WHERE user_id=?"]:
            backup=self.auth.backup(self.directory/'backups')
            try:
                self.sql(statement,(TARGET,));result,factory=self.deliver()
                self.assertEqual(result['status'],'skipped');factory.assert_not_called()
            finally:
                with closing(sqlite3.connect(backup)) as src,closing(sqlite3.connect(self.path)) as dest:src.backup(dest)

    def test_wrong_date_count_or_mechanical_path_rejected(self):
        for change in ['date','count','path']:
            def invalid(*a,**kw):
                result=self.build(*a,**kw)
                if change=='date':result['report']['date']='1405/07/09'
                if change=='count':result['documents']*=2
                if change=='path':result['documents']=[str(Path(result['documents'][0]).with_name('driver-mechanical-1405-07-10.pdf'))]
                return result
            with self.assertRaises(ValueError):self.deliver(builder=invalid)
            self.sender.document.assert_not_called()
        self.assertTrue(all(not Path(p).exists() for p in self.outputs))

    def test_writer_lock_is_not_bypassed(self):
        with patch.object(self.reader,'snapshot',side_effect=PermissionError('synthetic writer lock')):
            with self.assertRaises(PermissionError):self.deliver()
        self.sender.document.assert_not_called()


class MetalworkSchedule(MetalworkFixture, unittest.TestCase):
    def test_previous_tehran_calendar_day_utc_and_rollover(self):
        for instant,expected in [(datetime(2026,10,6,5,30,tzinfo=timezone.utc),'1405/07/13'),
                                 (datetime(2026,10,5,21,0,tzinfo=timezone.utc),'1405/07/13')]:
            self.assertEqual(overflow_report_date(instant),expected)
        with self.assertRaises(ValueError):overflow_report_date(datetime(2026,10,6,9))

    def test_nine_tehran_due_date_and_persistent_dedup(self):
        config=self.directory/'schedule.yaml';state=self.directory/'runs.sqlite3'
        job=dict(id='driver_daily_metalwork',task='metalwork_driver_daily',recipient_capability=METALWORK_DRIVER_RECEIVE,
                 timezone='Asia/Tehran',trigger=dict(type='cron',hour=9,minute=0),params={})
        config.write_text(yaml.safe_dump(dict(version=1,timezone='Asia/Tehran',schedules=[job])),encoding='utf-8')
        calls=[]
        def safe_handler(recipient,params,*,authorization):
            calls.append((recipient,params['date']))
            return {'status':'succeeded'}
        registry={'metalwork_driver_daily':(lambda p:None,safe_handler)}
        for minute in [0,1]:
            self.assertEqual(tick(config,state,datetime(2026,10,6,9,minute,tzinfo=ZoneInfo('Asia/Tehran')),registry,authorization=self.auth),0)
        self.assertEqual(calls,[('capability:'+METALWORK_DRIVER_RECEIVE,'1405/07/13')])
        self.assertEqual(load_config(config,registry)[2][0]['trigger'].timezone,ZoneInfo('Asia/Tehran'))

    def test_fixed_recipient_wrong_capability_timezone_and_duplicate_job_rejected(self):
        for change in [dict(recipient=TARGET),dict(recipient_capability=MECH_DRIVER_RECEIVE),dict(timezone='UTC')]:
            job=dict(id='new',task='metalwork_driver_daily',recipient_capability=METALWORK_DRIVER_RECEIVE,
                     timezone='Asia/Tehran',trigger=dict(type='cron',hour=9,minute=0),params={})
            job.update(change)
            path=self.directory/'invalid.yaml'
            path.write_text(yaml.safe_dump(dict(version=1,schedules=[job])),encoding='utf-8')
            with self.assertRaises(ValueError):load_config(path)
        path.write_text(yaml.safe_dump(dict(version=1,schedules=[job,job])),encoding='utf-8')
        with self.assertRaises(ValueError):load_config(path)


if __name__=='__main__':unittest.main()
