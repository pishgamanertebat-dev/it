"""Required office parity tests: real auth fixtures and fake Bale only."""
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
import yaml
from tools.authorization.test_authorization import Fixture
from tools.authorization.store import BUSINESS_ADMIN, OFFICE_SUPERVISOR, DAILY_RECEIVE, DRIVER_RECEIVE
from tools.authorization.office_delivery import migrate, OFFICE_ROLE, COPY_ROLE, COPY_CAPABILITY
from tools.authorization.maintenance import migrate as maintenance_migrate, CAPABILITY as MAINTENANCE_CAP
from .runner import tick, connect, _save_receipt, load_config
from .tasks import TransportUnavailable, TransportUncertain

DUE = datetime(2026,10,8,10,0,tzinfo=ZoneInfo('Asia/Tehran'))
TARGET = '1405/07/15'


class OfficeDeliveryTests(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.auth.migrate_admin(self.directory/'backups')
        self.auth.migrate_mechanical(self.directory/'backups')
        self.auth.migrate_business_admin_profile(self.directory/'backups', assignments=(('101',BUSINESS_ADMIN),('202',BUSINESS_ADMIN)))
        self.assign()
        self.before = {u:(self.auth.roles(u),self.auth.resolve_profile(u,u)) for u in ('101','202')}
        migrate(self.auth,self.directory/'backups',assignments=(('202',OFFICE_ROLE),('202',COPY_ROLE)))
        maintenance_migrate(self.auth,self.directory/'backups',assignments=('101','202'))
        self.config=self.directory/'schedules.yaml';self.state=self.directory/'runs.db'
        self.present=True;self.calls=[];self.failures={}
        self.sender=Mock()
        self.sender.document.side_effect=lambda u,p,c,**kw:self.send(u,Path(p).stem,Path(p).read_bytes(),c)
        self.sender.photo.side_effect=lambda u,p,c:self.send(u,Path(p).stem,Path(p).read_bytes(),c)
        self.sender.message.side_effect=lambda u,t:self.send(u,'notice',b'',t)
        for target,value in [('tools.scheduler.tasks.BaleSender',self.sender),]:
            p=patch(target,return_value=value);p.start();self.addCleanup(p.stop)
        p=patch('tools.scheduler.tasks.build_report',self.overflow);p.start();self.addCleanup(p.stop)
        p=patch('integrations.hermes.function_domain.driver_report.build_driver_pdf',self.driver);p.start();self.addCleanup(p.stop)
        self.job('driver_daily')

    def job(self,task):
        self.task=task
        cap=DRIVER_RECEIVE if task=='driver_daily' else DAILY_RECEIVE
        self.config.write_text(yaml.safe_dump({'version':1,'timezone':'Asia/Tehran','schedules':[
            dict(id='office',enabled=True,task=task,recipient_capability=cap,timezone='Asia/Tehran',
                 misfire=dict(policy='replay',horizon_days=7),trigger=dict(type='cron',hour=10,minute=0),params={})]}),encoding='utf-8')

    def send(self,u,artifact,data,caption):
        failure=self.failures.pop((u,artifact),None)
        if failure:raise failure
        self.calls.append((u,artifact,data,caption))
        return str(len(self.calls))

    def driver(self,date,directory,**kw):
        self.assertEqual(date,TARGET)
        report=dict(date=date,latest_date='1405/07/14',sections={s:dict(title=s,status='ready' if self.present else 'date_missing') for s in ('mechanical','metalwork')})
        docs=[]
        if self.present:
            for s in report['sections']:
                p=Path(directory)/(s+'.pdf');p.write_bytes(b'%PDF-1.7 '+s.encode());docs.append(str(p))
        return dict(report=report,documents=docs)

    async def overflow(self,date,directory):
        self.assertEqual(date,TARGET)
        if not self.present:return dict(ok=False,reason='date_missing',latest_date='1405/07/14')
        p=Path(directory)/'overflow.png';p.write_bytes(b'fixture')
        return dict(ok=True,report=dict(date=date),images=[p])

    def run_at(self,seconds=0):
        return tick(self.config,self.state,DUE+timedelta(seconds=seconds),authorization=self.auth)

    def rows(self,table):
        with closing(sqlite3.connect(self.state)) as c:
            c.row_factory=sqlite3.Row
            return [dict(r) for r in c.execute('SELECT * FROM '+table)]

    def test_both_pdfs_each_recipient_one_generation_same_bytes(self):
        with patch('integrations.hermes.function_domain.driver_report.build_driver_pdf',side_effect=self.driver) as generator:
            self.run_at();self.run_at(61)
        generator.assert_called_once()
        self.assertEqual([(u,a) for u,a,d,c in self.calls],[('101','mechanical'),('101','metalwork'),('202','mechanical'),('202','metalwork')])
        self.assertEqual(self.calls[0][2],self.calls[2][2]);self.assertEqual(self.calls[1][2],self.calls[3][2])
        self.assertEqual(len(self.rows('office_artifact_receipts')),4)
        self.assertEqual(self.rows('runs')[0]['status'],'succeeded')

    def test_overflow_two_recipients_no_duplicate(self):
        self.job('overflow');self.run_at();self.run_at(61)
        self.assertEqual([(u,a) for u,a,d,c in self.calls],[('101','overflow'),('202','overflow')])

    def test_missing_notice_independent_then_late_data_for_both(self):
        for task in ('driver_daily','overflow'):
            with self.subTest(task=task):
                self.state=self.directory/(task+'.db');self.job(task);self.calls=[];self.present=False
                self.run_at();self.run_at(61)
                self.assertEqual([(u,a) for u,a,d,c in self.calls],[('101','notice'),('202','notice')])
                self.assertTrue(all(TARGET in c for u,a,d,c in self.calls))
                self.assertEqual(self.rows('runs')[0]['status'],'waiting_for_data')
                self.present=True;self.run_at(182);self.run_at(483)
                count=4 if task=='driver_daily' else 2
                self.assertEqual(sum(a!='notice' for u,a,d,c in self.calls),count)
                self.assertEqual(self.rows('runs')[0]['status'],'succeeded')

    def test_failed_second_pdf_retries_only_that_artifact(self):
        self.failures['202','metalwork']=TransportUnavailable()
        self.run_at();self.assertEqual(self.rows('runs')[0]['status'],'partial')
        self.run_at(61);self.run_at(182)
        self.assertEqual([(u,a) for u,a,d,c in self.calls],[('101','mechanical'),('101','metalwork'),('202','mechanical'),('202','metalwork')])
        self.assertEqual(self.rows('runs')[0]['status'],'succeeded')

    def test_failed_recipient_does_not_repeat_other_success(self):
        self.job('overflow');self.failures['202','overflow']=TransportUnavailable()
        self.run_at();self.run_at(61)
        self.assertEqual([(u,a) for u,a,d,c in self.calls],[('101','overflow'),('202','overflow')])

    def test_uncertain_artifact_never_blind_resends_other_failure_can_retry(self):
        self.failures['202','mechanical']=TransportUncertain()
        self.failures['202','metalwork']=TransportUnavailable()
        self.run_at();self.assertEqual(self.rows('runs')[0]['status'],'partial')
        self.run_at(61);self.run_at(182)
        self.assertEqual([(u,a) for u,a,d,c in self.calls],[('101','mechanical'),('101','metalwork'),('202','metalwork')])
        self.assertEqual(self.rows('runs')[0]['status'],'uncertain')

    def test_restart_crash_after_first_pdf_preserves_pending_second(self):
        self.run_at();self.calls=[]
        with closing(connect(self.state)) as c,c:
            c.execute("UPDATE office_artifact_receipts SET status='pending',message_id=NULL WHERE recipient_id='202' AND artifact_id='metalwork'")
            c.execute("UPDATE receipts SET status='sending' WHERE recipient_id='202' AND delivery_kind='report'")
            c.execute("UPDATE runs SET status='running',next_attempt_at=NULL")
        self.run_at(901)
        self.assertEqual([(u,a) for u,a,d,c in self.calls],[('202','metalwork')])

    def test_stale_sending_freezes_one_artifact_without_blocking_other(self):
        self.run_at();self.calls=[]
        with closing(connect(self.state)) as c,c:
            c.execute("UPDATE office_artifact_receipts SET status='sending' WHERE recipient_id='202' AND artifact_id='mechanical'")
            c.execute("UPDATE office_artifact_receipts SET status='pending',message_id=NULL WHERE recipient_id='202' AND artifact_id='metalwork'")
            c.execute("UPDATE receipts SET status='sending' WHERE recipient_id='202' AND delivery_kind='report'")
            c.execute("UPDATE runs SET status='running',next_attempt_at=NULL")
        self.run_at(901);self.run_at(962)
        self.assertEqual([(u,a) for u,a,d,c in self.calls],[('202','metalwork')])
        self.assertEqual(self.rows('runs')[0]['status'],'uncertain')

    def test_revocation_between_two_uploads(self):
        original=self.sender.document.side_effect
        def send(u,p,c,**kw):
            result=original(u,p,c,**kw)
            if u=='202':self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id='202' AND role=?",(OFFICE_ROLE,))
            return result
        self.sender.document.side_effect=send;self.run_at()
        self.assertEqual([(u,a) for u,a,d,c in self.calls],[('101','mechanical'),('101','metalwork'),('202','mechanical')])
        self.assertFalse(self.auth.has_capability('202',DRIVER_RECEIVE))

    def test_legacy_sent_receipt_honored(self):
        self.run_at();self.calls=[]
        with closing(connect(self.state)) as c,c:
            c.execute('DELETE FROM office_artifact_receipts')
            c.execute("UPDATE receipts SET status='pending' WHERE recipient_id='202' AND delivery_kind='report'")
            c.execute("UPDATE runs SET status='partial',next_attempt_at=NULL")
        self.run_at(61)
        self.assertEqual([(u,a) for u,a,d,c in self.calls],[('202','mechanical'),('202','metalwork')])

    def test_roles_profiles_existing_grants_and_maintenance_preserved(self):
        self.assertEqual(self.auth.resolve_profile('101','101'),'admin')
        self.assertEqual(self.auth.resolve_profile('202','202'),'admin')
        self.assertIn(OFFICE_SUPERVISOR,self.auth.roles('101'));self.assertNotIn(OFFICE_SUPERVISOR,self.auth.roles('202'))
        self.assertEqual(self.auth.capabilities_for_role(OFFICE_ROLE),(DRIVER_RECEIVE,DAILY_RECEIVE))
        self.assertEqual(self.auth.capabilities_for_role(COPY_ROLE),(COPY_CAPABILITY,))
        for cap in ['work_orders.create','work_orders.approve','work_orders.send','repairs.driver_report.edit','reports.driver_daily.read','reports.overflow.read']:
            self.assertFalse(self.auth.has_capability('202',cap))
        self.assertEqual(set(self.auth.resolve_active_recipients(MAINTENANCE_CAP).recipients),{'101','202'})
        events=self.sql("SELECT COUNT(*) FROM auth_events")
        migrate(self.auth,self.directory/'backups',assignments=(('202',OFFICE_ROLE),('202',COPY_ROLE)))
        self.assertEqual(events,self.sql("SELECT COUNT(*) FROM auth_events"))
        jobs=load_config()[2]
        self.assertEqual({j['id'] for j in jobs if not j.get('enabled',True)},{'repairs_daily','metalwork_daily'})
        self.assertEqual(sum(j['task']=='maintenance_daily_report' for j in jobs),1)
        self.assertEqual(self.sql("SELECT role FROM auth_role_profiles WHERE role IN (?,?)",(OFFICE_ROLE,COPY_ROLE)),[])

    def test_authorization_outage_retries_without_revoking_pending_delivery(self):
        self.failures['202','metalwork']=TransportUnavailable()
        self.run_at()
        original=self.auth.resolve_active_recipients
        with patch.object(self.auth,'resolve_active_recipients',return_value=SimpleNamespace(status='store_unavailable',recipients=())):
            self.run_at(61)
        self.assertEqual(self.rows('runs')[0]['status'],'partial')
        self.assertFalse(any(r['status']=='revoked' for r in self.rows('receipts') if r['delivery_kind']=='report'))
        self.run_at(182)
        self.assertEqual(sum(u=='202' and a=='metalwork' for u,a,d,c in self.calls),1)
        self.assertEqual(sum(u=='101' for u,a,d,c in self.calls),2)
