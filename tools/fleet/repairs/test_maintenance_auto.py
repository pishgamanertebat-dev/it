"""Exercise the Bale routing boundary and the real Writer on private files."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from . import maintenance_report_auto as auto, maintenance_report_flow as flow
from .maintenance_report_bale import MaintenanceReportHandler
from .maintenance_report_parse import work_items
from . import test_maintenance_report_flow as fixtures
from .test_maintenance_report_flow import ACTOR, DAY
import unittest


class AutoTests(unittest.TestCase):
    def setUp(self):
        fixtures.PartialReportTests.setUp(self)
        policy = self.kwargs['config']
        config = json.loads(policy.read_text('utf8'))
        config['reporters'][ACTOR]['confirmation_mode'] = 'auto_validated'
        other = '641220453'
        config['reporters'][other] = {**config['reporters'][ACTOR]}
        policy.write_text(json.dumps(config),'utf8')
        entry = self.kwargs['entry_config']
        config = json.loads(entry.read_text('utf8'))
        config['allowed_users'].append(other)
        entry.write_text(json.dumps(config),'utf8')

    def event(self, text, actor=ACTOR, message_id='799'):
        return SimpleNamespace(text=text,message_id=message_id,raw_message={},
            source=SimpleNamespace(platform='bale',chat_type='dm',user_id=actor,chat_id=actor,user_name='محسن کرمعلی'))

    async def dispatch(self,event,**changes):
        async def run(payload):
            try:
                return dict(ok=True,result=flow.process(**payload,**self.kwargs))
            except Exception as exc:
                return dict(ok=False,message=str(exc))
        ui = MaintenanceReportHandler(run=run,runtime=self.kwargs['runtime'],
            authorize=lambda a,c: flow.permission(a,c,config=self.kwargs['config'],entry_config=self.kwargs['entry_config']))
        async def extract(raw,actor,**kwargs):
            return dict(report_date=DAY,unresolved=[],items=[dict(raw_code=i['code'],raw_description=i['raw'],
                source_span=i['span']) for i in work_items(raw,independent=True)])
        ui.partial.present = AsyncMock(return_value='900')
        send = Mock()
        with patch.object(auto.entry_extract,'extract',side_effect=extract) as model:
            action = ui.handle(event,None,send=send)
            if ui.tasks:
                await asyncio.gather(*ui.tasks)
        return send,ui.partial.present,action,model

    def test_plain_gateway_commit_and_crash_after_commit(self):
        event = self.event('گزارش مکانیکی '+DAY+'\n-بیل 801: تعمیر پمپ')
        with patch.object(flow,'_complete',side_effect=RuntimeError('lost response')):
            send,present,_,model = asyncio.run(self.dispatch(event))
            self.assertTrue(model.called)
            self.assertTrue(any(c.args[1]==auto.OWNER for c in send.call_args_list))
            self.assertFalse(any('ثبت شد' in c.args[2] for c in send.call_args_list))
        committed = self.source.read_bytes()
        self.assertNotEqual(committed,self.initial)
        send,present,_,_=asyncio.run(self.dispatch(event))
        self.assertIn('Function',send.call_args.args[2])
        present.assert_not_awaited()
        self.assertEqual(self.source.read_bytes(),committed)

    def test_other_verified_actor_even_with_same_policy_needs_buttons(self):
        ev=self.event('گزارش مکانیکی '+DAY+'\n-بیل 801: تعمیر پمپ','641220453')
        _,present,_,model=asyncio.run(self.dispatch(ev))
        model.assert_not_called()
        self.assertIn('تأیید گزارش',str(present.await_args.args[4]))
        self.assertEqual(self.source.read_bytes(),self.initial)

    def test_question_forward_and_writer_failure(self):
        ev=self.event('چرا بیل خراب شد؟')
        _,_,_,model=asyncio.run(self.dispatch(ev))
        model.assert_not_called()
        ev.text='گزارش مکانیکی '+DAY+'\n-بیل 801: تعمیر پمپ'
        ev.raw_message={'forward_origin':'forwarded'}
        _,_,_,model=asyncio.run(self.dispatch(ev))
        model.assert_not_called()
        ev.raw_message={}
        with patch.object(flow,'_commit',side_effect=OSError('Writer unavailable')):
            send,_,_,_=asyncio.run(self.dispatch(ev))
            self.assertTrue(any(c.args[2]==auto.TECHNICAL for c in send.call_args_list))
            self.assertFalse(any('ثبت شد' in c.args[2] for c in send.call_args_list))
        self.assertEqual(self.source.read_bytes(),self.initial)
