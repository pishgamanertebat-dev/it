"""Preflight uses isolated state and fake transport; no reports are uploaded."""
from datetime import datetime,timedelta
import unittest
from unittest.mock import Mock,patch
from apscheduler.triggers.cron import CronTrigger
from .runner import connect,load_config
from .preflight import run_preflights,probe,validate_config
from .report_errors import ReportFailure,public_reason
from .test_delivery_alert import Directory,TZ
from . import test_delivery_alert as harness


class PreflightTests(unittest.TestCase):
    setUp=harness.AlertTests.setUp
    sender=harness.AlertTests.sender

    def job(self,hour=9,task='mechanical_driver_daily',name='morning'):
        return dict(id=name,task=task,enabled=True,preflight_hours=(8,11),
                    trigger=CronTrigger(hour=hour,minute=0,timezone=TZ))

    def run_check(self,now,jobs=None,checker=None,connection=None):
        c=connect(self.state)
        try:
            run_preflights(c,jobs or [self.job()],now,self.store,checker=checker or self.checker,
                           sender_factory=self.sender,connection_probe=connection or (lambda:None))
        finally:c.close()

    def at(self,hour,minute=0):return self.moment.replace(hour=hour,minute=minute)

    def setup_checker(self):
        self.checker=Mock(return_value={'status':'passed','sample_date':'1405/07/12'})

    def test_eight_and_eleven_only_no_report_or_success_messages(self):
        self.setup_checker();jobs=[self.job(),self.job(12,'maintenance_daily_report','noon')]
        self.run_check(self.at(7,59),jobs);self.checker.assert_not_called()
        self.run_check(self.at(8),jobs);self.run_check(self.at(8,1),jobs)
        self.assertEqual(self.checker.call_count,1)
        self.run_check(self.at(11),jobs);self.run_check(self.at(11,1),jobs)
        self.assertEqual(self.checker.call_count,2);self.assertEqual(self.sent,[])
        c=connect(self.state)
        self.assertEqual(c.execute('SELECT count(*) FROM runs').fetchone()[0],0)
        self.assertEqual(c.execute('SELECT count(*) FROM receipts').fetchone()[0],0);c.close()

    def test_missing_target_and_older_sample_do_not_warn(self):
        self.checker=Mock(return_value={'status':'no_data'})
        self.run_check(self.at(8));self.assertEqual(self.sent,[])
        self.checker.return_value={'status':'passed','sample_date':'1405/07/01'}
        self.run_check(self.at(11),[self.job(12,'maintenance_daily_report','noon')])
        self.assertEqual(self.sent,[])

    def test_layout_failure_named_location_once_and_retries_after_fix(self):
        self.checker=Mock(side_effect=ReportFailure('pdf_layout',stage='layout',sheet='461',row=11,column=3))
        self.run_check(self.at(11),[self.job(12,'maintenance_daily_report','noon')])
        self.run_check(self.at(11,1),[self.job(12,'maintenance_daily_report','noon')])
        self.assertEqual(len(self.sent),1)
        text=self.sent[0][1]
        self.assertIn('بررسی پیش از موعد',text);self.assertIn('12:00',text)
        self.assertIn('شیت 461',text);self.assertIn('ردیف 11',text)
        self.checker.side_effect=None;self.checker.return_value={'status':'passed'}
        self.run_check(self.at(11,5),[self.job(12,'maintenance_daily_report','noon')])
        self.assertEqual(len(self.sent),1)
        c=connect(self.state)
        self.assertEqual(c.execute('SELECT status FROM report_preflight_runs').fetchone()[0],'passed');c.close()

    def test_operator_send_failure_retried_and_does_not_touch_staff(self):
        self.checker=Mock(side_effect=ReportFailure('source_invalid',stage='source'))
        self.fail_send=True;self.run_check(self.at(8));self.assertEqual(self.sent,[])
        self.fail_send=False;self.run_check(self.at(8,1));self.run_check(self.at(8,2))
        self.assertEqual(len(self.sent),1);self.assertEqual(self.sent[0][0],'900')

    def test_driver_family_is_generated_once_for_multiple_schedules(self):
        self.setup_checker()
        jobs=[self.job(task=task,name=task) for task in ('driver_daily','mechanical_driver_daily','metalwork_driver_daily')]
        self.run_check(self.at(8),jobs);self.assertEqual(self.checker.call_count,1)

    def test_late_or_disabled_checks_never_run(self):
        self.setup_checker();self.run_check(self.at(9));self.checker.assert_not_called()
        job=self.job();job['enabled']=False
        self.run_check(self.at(8),[job]);self.checker.assert_not_called()

    def test_safe_diagnostics_do_not_expose_exception_messages(self):
        self.checker=Mock(side_effect=RuntimeError('token=private E:/secret/file'))
        self.run_check(self.at(8));self.assertEqual(len(self.sent),1)
        self.assertNotIn('private',self.sent[0][1]);self.assertNotIn('secret',self.sent[0][1])
        self.assertIsNone(public_reason('unknown'))
        self.assertNotIn('secret',public_reason('pdf_layout','{"sheet":"E:/secret"}'))

    def test_config_is_strict(self):
        self.assertEqual(validate_config({'enabled':True,'hours':[8,11]}),(8,11))
        for raw in [{'hours':[8,8]},{'hours':['8']},{'enabled':'yes'},{'typo':True}]:
            with self.assertRaises(ValueError):validate_config(raw)

    def test_missing_maintenance_day_uses_latest_sample_without_upload(self):
        from contextlib import contextmanager
        from tools.fleet.maintenance_daily import report as m
        from tools.fleet.maintenance_daily.test_report import workbook,package
        w,layout=workbook([('801',[['1405/07/12',801,'مکانیک','فعالیت',None]])]);data=package(w);w.close()
        real_scan=m.scan
        @contextmanager
        def snapshot():yield data,'fixture'
        with patch.object(m,'source_snapshot',snapshot),patch.object(m,'scan',side_effect=lambda d,t:real_scan(d,t,layout=layout)),patch.object(m,'render') as render:
            result=probe(self.job(task='maintenance_daily_report'),'1405/07/13',self.dir)
        self.assertEqual(result,{'status':'passed','sample_date':'1405/07/12'})
        self.assertEqual(render.call_args.args[0]['date'],'1405/07/12')

    def test_missing_driver_day_tests_older_sample_without_missing_data_warning(self):
        from integrations.hermes.function_domain import driver_report as driver
        with patch.object(driver,'load_driver',return_value={'latest_date':'1405/07/12'}),patch.object(driver,'build_driver_pdf') as build:
            result=probe(self.job(),'1405/07/13',self.dir)
        self.assertEqual(result,{'status':'passed','sample_date':'1405/07/12'})
        self.assertEqual(build.call_args.args[0],'1405/07/12')

    def test_missing_overflow_day_tests_older_sample_without_missing_data_warning(self):
        from tools.fleet.overflow import report as overflow
        missing=overflow.ReportError('missing',reason='date_missing')
        with patch.object(overflow,'load_report',side_effect=[missing,{'date':'1405/07/12'}]),patch.object(overflow,'render_report') as render:
            result=probe(self.job(task='overflow'),'1405/07/13',self.dir)
        self.assertEqual(result,{'status':'passed','sample_date':'1405/07/12'})
        self.assertEqual(render.call_args.args[0]['date'],'1405/07/12')


if __name__=='__main__':unittest.main()
