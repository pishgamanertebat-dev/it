"""Manual input remains usable with the entire AI network disabled."""
import asyncio
import os
import unittest
from unittest.mock import patch

from . import test_entry_bale as fixtures
from . import entry_extract, entry_draft


class ManualTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.EntryBaleTests.asyncSetUp
    asyncTearDown=fixtures.EntryBaleTests.asyncTearDown
    make_handler=fixtures.EntryBaleTests.make_handler
    run_worker=fixtures.EntryBaleTests.run_worker
    event=fixtures.EntryBaleTests.event
    fallback=fixtures.EntryBaleTests.fallback
    deliver=fixtures.EntryBaleTests.deliver
    action_event=fixtures.EntryBaleTests.action_event
    start_description=fixtures.EntryBaleTests.start_description

    async def test_model_off_manual_date_choice_capture_append_and_replace(self):
        self.handler.safe_capture=True
        with patch.dict(os.environ,{'OPENAI_API_KEY':'','ANTHROPIC_API_KEY':'','OPENROUTER_API_KEY':''}), \
             patch('socket.socket.connect',side_effect=AssertionError('AI API forbidden')), \
             patch.object(entry_extract,'extract',side_effect=AssertionError('Model forbidden')):
            for mode in ('append','replace'):
                await self.deliver(self.event('شرح خرابی'))
                await self.deliver(self.action_event('mechanical'))
                await self.deliver(self.action_event('dates'))
                s=self.handler.sessions[self.key]
                self.assertEqual(len(s['date_choices']),8)
                await self.deliver(self.action_event('date_'+s['date_choices'][-1].replace('/','')))
                await self.deliver(self.event('710'))
                await self.deliver(self.event('تعمیر کامل موتور'))
                self.assertEqual(self.handler.sessions[self.key]['stage'],'CAPTURE')
                self.assertNotIn('request',self.handler.sessions[self.key])
                await self.deliver(self.action_event('use_text'))
                await self.deliver(self.action_event(mode))
                self.assertEqual(self.calls[-1]['action'],'commit')
                self.assertEqual(self.calls[-1]['request']['write_mode'],mode)
                self.assertIn('✅',self.messages[-1]['text'])

    async def test_chat_inside_manual_form_does_not_become_a_description(self):
        self.handler.safe_capture=True
        await self.start_description()
        await self.deliver(self.event('چرا موتور داغ می‌کند؟'))
        self.assertEqual(self.handler.sessions[self.key]['stage'],'CAPTURE')
        self.assertNotIn('request',self.handler.sessions[self.key])
        await self.deliver(self.action_event('discard_text'))
        self.assertFalse(self.handler.active_for(*self.key))
        self.assertIsNone(await self.deliver(self.event('چرا موتور داغ می‌کند؟')))
        self.assertFalse(any(c['action']=='commit' for c in self.calls))

    async def test_disabled_ai_worker_is_fail_closed(self):
        with patch.object(entry_extract,'enabled',return_value=False):
            with self.assertRaisesRegex(ValueError,'فعال نشده'):
                entry_draft.execute(dict(action='ai_create',actor='641220453',chat='641220453',extraction={}))


if __name__=='__main__':unittest.main()
