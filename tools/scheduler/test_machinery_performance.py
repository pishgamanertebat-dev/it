"""Missing-day FIFO, recipient isolation, late delivery and authorization, no live sends."""
from contextlib import closing
from datetime import datetime,timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfo

import yaml

from tools.authorization.machinery_performance import CAPABILITY,REMINDER_CAPABILITY,REPORT_USERS,REMINDER_USER,READ,migrate
from tools.authorization.store import RecipientSetResolution,MECHANICAL_STAFF
from tools.authorization.test_authorization import Fixture
from tools.fleet.machinery_performance import queue,delivery
from tools.scheduler.completion_reminder import run_reminders
from tools.scheduler.delivery_alert import _operators
from tools.scheduler.misfire import due_storage_key
from tools.scheduler.runner import connect,load_config,tick
from tools.scheduler.tasks import TransportUnavailable,overflow_report_date

TEHRAN=ZoneInfo('Asia/Tehran')
TASK='machinery_performance_daily_report'


def configuration():
    return dict(version=1,timezone='Asia/Tehran',machinery_performance_reminders=dict(hours=[14,17]),
                schedules=[dict(id=TASK,task=TASK,enabled=True,recipient_capability=CAPABILITY,timezone='Asia/Tehran',
                                misfire=dict(policy='replay',horizon_days=7),
                                trigger=dict(type='cron',hour=15,minute=0,start_date='2026-11-01T00:00:00+03:30'),params={})])


class Harness:
    def setUp(self):
        root=Path('runtime/machinery-performance-tests');root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name);self.state=self.directory/'state.sqlite3'
        self.config=self.directory/'schedules.yaml';self.config.write_text(yaml.safe_dump(configuration()),encoding='utf-8')
        self.job=load_config(self.config)[2][0]
        self.conn=connect(self.state);self.addCleanup(self.conn.close)
        self.old=datetime(2026,11,1,15,tzinfo=TEHRAN)
        self.new=self.old+timedelta(days=1)

    def seed(self,due=None,status='waiting_for_data',error='date_missing'):
        due=due or self.old
        with self.conn:
            self.conn.execute('INSERT INTO runs(schedule_id,due,recipient,task,status,started,error,data_state) VALUES (?,?,?,?,?,?,?,?)',
                              (TASK,due_storage_key(due),'capability:'+CAPABILITY,TASK,status,due_storage_key(due),error,'missing'))

    def rows(self):
        return {r['due']:dict(r) for r in self.conn.execute('SELECT * FROM runs')}

    def plan(self,now,ready=()):
        return queue.plan(self.conn,self.job,now,self.rows(),checker=lambda target:target in ready)


class QueueTests(Harness,unittest.TestCase):
    def test_waits_until_full_36_hours_even_when_newer_ready(self):
        self.seed()
        self.assertEqual(self.plan(self.old+timedelta(hours=35,minutes=59),[overflow_report_date(self.new)]),[self.old])
        self.assertEqual(self.rows()[due_storage_key(self.old)]['status'],'waiting_for_data')

    def test_missing_old_is_retired_at_36_hours_only_with_newer_ready(self):
        self.seed()
        self.assertEqual(self.plan(self.old+timedelta(hours=36),[overflow_report_date(self.new)]),[self.new])
        row=self.rows()[due_storage_key(self.old)]
        self.assertEqual((row['status'],row['error']),('no_data_final','superseded_missing_date'))

    def test_no_newer_complete_day_keeps_old_after_seven_days(self):
        self.seed()
        self.assertEqual(self.plan(self.old+timedelta(days=10)),[self.old])
        self.assertEqual(self.rows()[due_storage_key(self.old)]['status'],'waiting_for_data')

    def test_late_old_day_is_delivered_first(self):
        self.seed()
        self.assertEqual(self.plan(self.old+timedelta(hours=40),[overflow_report_date(self.old),overflow_report_date(self.new)]),[self.old])

    def test_sent_or_uncertain_recipient_never_discarded(self):
        for status in ('sent','sending','uncertain'):
            with self.subTest(status=status):
                self.conn.execute('DELETE FROM runs');self.conn.execute('DELETE FROM receipts');self.conn.commit();self.seed()
                with self.conn:
                    self.conn.execute('INSERT INTO receipts VALUES (?,?,?,?,?,?,?)',(TASK,due_storage_key(self.old),REPORT_USERS[0],'report',status,'test-mid',due_storage_key(self.old)))
                self.assertEqual(self.plan(self.old+timedelta(hours=36),[overflow_report_date(self.new)]),[self.old])

    def test_source_error_cannot_supersede_missing_day(self):
        self.seed()
        with patch.object(queue,'Readiness',return_value=Mock(side_effect=RuntimeError('locked'))):
            self.assertEqual(queue.plan(self.conn,self.job,self.old+timedelta(hours=36),self.rows()),[self.old])
        self.assertEqual(self.rows()[due_storage_key(self.old)]['status'],'waiting_for_data')

    def test_initial_activation_does_not_dump_historical_reports(self):
        self.assertEqual(self.plan(self.new+timedelta(hours=4)),[self.new])

    def test_fifo_does_not_advance_around_delivery_backoff(self):
        self.seed(status='retry_wait',error='delivery_failed')
        now=self.old+timedelta(hours=36)
        with self.conn:self.conn.execute('UPDATE runs SET next_attempt_at=?',(due_storage_key(now+timedelta(minutes=5)),))
        self.assertEqual(self.plan(now,[overflow_report_date(self.new)]),[])

    def test_tick_sends_late_exact_target_and_reuses_receipts(self):
        store=Mock();store.resolve_active_recipients.return_value=RecipientSetResolution('ready',REPORT_USERS)
        store.identity_label.side_effect=lambda u:u
        def execute(recipient,params,**kwargs):
            self.assertEqual(params['date'],overflow_report_date(self.old))
            self.assertEqual(params['occurrence'],self.old.isoformat())
            for u in REPORT_USERS:kwargs['on_receipt'](u,'sent','synthetic-'+u)
            return {'status':'succeeded'}
        from tools.scheduler.tasks import TASKS
        registry=dict(TASKS);registry[TASK]=(delivery.validate_params,execute)
        with patch.object(queue,'Readiness',return_value=lambda target:True):
            tick(self.config,self.state,self.old+timedelta(hours=4),registry,store)
            tick(self.config,self.state,self.old+timedelta(hours=5),registry,store)
        self.assertEqual(len(list(self.conn.execute('SELECT * FROM receipts'))),2)


class ReminderTests(Harness,unittest.TestCase):
    def test_reminders_only_at_14_and_17_only_to_hedayati(self):
        store=Mock()
        store.resolve_active_recipients.return_value=RecipientSetResolution('ready',(*REPORT_USERS,'999'))
        sender=Mock();sender.message.return_value='test-reminder'
        for hour in (13,14,14,15,17,17,19):
            run_reminders(self.conn,[self.job],self.old.replace(hour=hour),store,
                          checker=lambda target:{'status':'date_missing'},sender_factory=lambda:sender)
        self.assertEqual(sender.message.call_count,2)
        self.assertEqual({c.args[0] for c in sender.message.call_args_list},{REMINDER_USER})

    def test_second_slot_rereads_and_stops_when_complete(self):
        store=Mock();store.resolve_active_recipients.return_value=RecipientSetResolution('ready',(REMINDER_USER,))
        sender=Mock();sender.message.return_value='test-reminder'
        for hour,state in ((14,'date_missing'),(17,'ready')):
            run_reminders(self.conn,[self.job],self.old.replace(hour=hour),store,
                          checker=lambda target,s=state:{'status':s},sender_factory=lambda:sender)
        self.assertEqual(sender.message.call_count,1)

    def test_technical_alerts_are_also_only_for_hedayati(self):
        store=Mock();store.resolve_active_recipients.return_value=RecipientSetResolution('ready',(*REPORT_USERS,'999'))
        self.assertEqual(_operators(store,self.job),(REMINDER_USER,))
        store.resolve_active_recipients.assert_called_once_with(REMINDER_CAPABILITY)


class DeliveryTests(unittest.TestCase):
    def test_retry_does_not_resend_to_closed_recipient_or_use_other_subscribers(self):
        store=Mock();store.resolve_active_recipients.return_value=RecipientSetResolution('ready',(*REPORT_USERS,'999'))
        sender=Mock();sender.document.return_value='test-pdf'
        report=dict(date='1405/08/09',comparison='average_gt_limit',alert_codes=[])
        with patch.object(delivery,'prepare',return_value=dict(status='ready',report=report,pdf='unused.pdf')), \
             patch.object(delivery,'validate_pdf'),patch('tools.scheduler.tasks.BaleSender',return_value=sender):
            result=delivery.deliver('capability:'+CAPABILITY,{'date':report['date']},authorization=store,
                                    closed={'sent':{REPORT_USERS[0]}})
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(sender.document.call_count,1)
        self.assertEqual(sender.document.call_args.args[0],REPORT_USERS[1])

    def test_uncertain_closed_recipient_is_not_retried(self):
        store=Mock();store.resolve_active_recipients.return_value=RecipientSetResolution('ready',REPORT_USERS)
        with patch('tools.scheduler.tasks.BaleSender') as sender:
            result=delivery.deliver('capability:'+CAPABILITY,{'date':'1405/08/09'},authorization=store,
                                    closed={'sent':{REPORT_USERS[0]},'uncertain':{REPORT_USERS[1]}})
        self.assertEqual(result['status'],'uncertain');sender.assert_not_called()


class AuthorizationTests(Fixture,unittest.TestCase):
    def setUp(self):
        super().setUp();self.auth.migrate_admin(self.directory/'backups')
        self.auth.migrate_mechanical(self.directory/'backups',assignments=[('101',MECHANICAL_STAFF)])
        for user in REPORT_USERS:self.sql('INSERT INTO channel_users VALUES (?,?,?,?)',('bale',user,user,'approved'))
        self.sql('INSERT INTO auth_roles VALUES (?,?)',('net_staff','نیروی نت'))
        self.auth.assign_roles([('202','net_staff')],actor='fixture')

    def test_adds_only_exact_file_and_named_subscriptions_without_profile_change(self):
        profile=self.auth.resolve_profile('101','101')
        original=self.auth.function_scope('101').files
        migrate(self.auth,self.directory/'backups')
        self.assertEqual(self.auth.resolve_active_recipients(CAPABILITY).recipients,REPORT_USERS)
        self.assertEqual(self.auth.resolve_active_recipients(REMINDER_CAPABILITY).recipients,(REMINDER_USER,))
        self.assertEqual(self.auth.resolve_profile('101','101'),profile)
        self.assertEqual(set(self.auth.function_scope('101').files),set(original)|{'عملکرد ماشین آلات.xlsx'})
        self.assertEqual(self.auth.function_scope('202').files,('عملکرد ماشین آلات.xlsx',))
        self.assertFalse(self.auth.function_scope('202').all)
        self.assertFalse(self.auth.has_capability('101',CAPABILITY))
        self.auth.migrate_mechanical(self.directory/'backups')  # Earlier migration remains replayable.
        before=self.sql('SELECT * FROM auth_events')
        migrate(self.auth,self.directory/'backups')
        self.assertEqual(self.sql('SELECT * FROM auth_events'),before)

    def test_wrong_private_identity_rolls_back(self):
        self.sql("UPDATE channel_users SET chat_id='999' WHERE user_id=?",(REMINDER_USER,))
        with self.assertRaises(ValueError):migrate(self.auth,self.directory/'backups')
        self.assertFalse(self.auth.has_capability('101',READ))


if __name__=='__main__':unittest.main()
