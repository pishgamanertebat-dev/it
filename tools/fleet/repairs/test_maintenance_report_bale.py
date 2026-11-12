import asyncio
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from unittest.mock import patch

from .maintenance_report_bale import MaintenanceReportHandler


class AutomaticMaintenanceBaleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3]/'runtime')
        self.addCleanup(temp.cleanup)
        self.runtime=Path(temp.name)
        self.policy=dict(scope='all')
        self.run=AsyncMock(return_value=dict(ok=True,result=dict(date='1405/08/19',recorder='محسن کرمعلی',
            records=[dict(code='1253')],delivery=dict(status='no_recorded_delivery'),remaining_text='')))
        self.followup=Mock();self.send=Mock()
        self.handler=MaintenanceReportHandler(run=self.run,authorize=lambda *args:self.policy,
                                            runtime=self.runtime,followup=self.followup)

    def event(self,**changes):
        event=SimpleNamespace(text='گزارش مکانیکی 19 آبان 1405\n-بیل1003: تعمیر.',message_id='9001',
            source=SimpleNamespace(platform='bale',chat_type='dm',user_id='1732374823',chat_id='1732374823'),raw_message={})
        for key,value in changes.items():setattr(event,key,value)
        return event

    async def finish(self):
        if self.handler.tasks:await asyncio.gather(*self.handler.tasks)

    async def test_raw_is_durable_before_worker_and_success_has_no_approval_buttons(self):
        event=self.event()
        self.assertEqual(self.handler.handle(event,None,send=self.send)['reason'],'maintenance-report')
        with closing(sqlite3.connect(self.runtime/'audit.sqlite3')) as db:
            self.assertEqual(db.execute('select raw_text from maintenance_report_messages').fetchone()[0],event.text)
        self.run.assert_not_awaited()
        await self.finish()
        self.run.assert_awaited_once()
        self.assertIn('ثبت شد',self.send.call_args.args[2])
        self.assertNotIn('تأیید',self.send.call_args.args[2])

    async def test_same_event_while_busy_is_not_processed_twice(self):
        event=self.event();self.handler.handle(event,None,send=self.send)
        self.assertEqual(self.handler.handle(event,None,send=self.send)['reason'],'maintenance-report-in-progress')
        await self.finish();self.run.assert_awaited_once()

    async def test_questions_confirmation_and_callbacks_never_become_writes(self):
        for event in [self.event(text='چرا بیل1003 خراب است؟'),self.event(text='گزارش ۱۸ آبان قابل تایید است'),
                      self.event(raw_message={'bale_inline_callback':True})]:
            self.assertIsNone(self.handler.handle(event,None,send=self.send))
        self.run.assert_not_awaited()
        self.assertFalse((self.runtime/'audit.sqlite3').exists())

    async def test_other_accounts_groups_missing_message_ids_fail_closed(self):
        for event in [self.event(source=SimpleNamespace(platform='telegram',chat_type='dm',user_id='1',chat_id='1')),
                      self.event(source=SimpleNamespace(platform='bale',chat_type='group',user_id='1',chat_id='1')),
                      self.event(source=SimpleNamespace(platform='bale',chat_type='dm',user_id='1',chat_id='2'))]:
            self.assertIsNone(self.handler.handle(event,None,send=self.send))
        self.assertIn('message-id',self.handler.handle(self.event(message_id=''),None,send=self.send)['reason'])
        self.run.assert_not_awaited()

    async def test_failure_has_no_success_and_mixed_sadeghian_preserves_coded_followup(self):
        self.policy['scope']='miscellaneous'
        self.assertIsNone(self.handler.handle(self.event(),None,send=self.send))
        event=self.event(text='گزارش آهنگری 19 آبان 1405\n-بیل1003: تعمیر.\n-جوشکاری میز')
        self.run.return_value=dict(ok=False,message='کد نامعتبر')
        self.handler.handle(event,None,send=self.send);await self.finish()
        self.assertNotIn('ثبت شد',self.send.call_args.args[2]);self.followup.assert_not_called()
        self.run.return_value=dict(ok=True,result=dict(date='1405/08/19',recorder='صادق صادقیان',records=[dict(code='متفرقه')],
            delivery=dict(status='correction_required'),remaining_text='گزارش آهنگری 19 آبان 1405\n-بیل1003: تعمیر.'))
        self.handler.handle(event,None,send=self.send);await self.finish()
        self.followup.assert_called_once()
        self.assertIn('بیل1003',self.followup.call_args.args[3])

    async def test_composition_routes_declared_report_before_manual_code_form(self):
        from tools.bale_ui.runtime import dispatch
        from tools.fleet.repairs.entry_bale import _handler as old_repairs
        from tools.fleet.repairs.maintenance_bale import _handler as manual
        from tools.fleet.work_orders.channels.bale.message_handler import _handler as orders
        with patch('tools.bale_ui.runtime.reply_menu_step',return_value=None), \
             patch('tools.fleet.repairs.entry_extract.enabled',return_value=False), \
             patch('tools.fleet.repairs.maintenance_report_policy.enabled',return_value=True), \
             patch('tools.fleet.repairs.maintenance_report_bale._handler',self.handler), \
             patch.object(orders,'pending',{}), patch.object(manual,'sessions',{}), \
             patch.object(old_repairs,'sessions',{}):
            result=dispatch(self.event(),None,send=self.send,bale_approved=True)
            self.assertEqual(result['reason'],'maintenance-report')
            await self.finish()
        self.run.assert_awaited_once()


if __name__=='__main__':unittest.main()
