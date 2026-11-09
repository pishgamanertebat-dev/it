"""Existing form commands/callbacks must work with all external networking disabled."""
import asyncio
import os
from unittest.mock import AsyncMock, patch

from . import test_entry_bale as drivers_tests, test_maintenance_bale as repair_tests


class DriversOfflineTests(drivers_tests.EntryBaleTests):
    async def test_no_model_or_network_required_and_unknown_result_retries_same_operation(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': '', 'ANTHROPIC_API_KEY': ''}), \
             patch('socket.socket.connect', side_effect=AssertionError('external API forbidden')):
            self.assertIsNone(await self.deliver(self.event('گزارش آزاد تعمیر 710')))
            self.assertEqual(self.calls, [])
            await self.start_description()
            await self.deliver(self.event('ثبت قطعی مکانیکی'))
            operation = self.handler.sessions[self.key]['request']['operation']
            self.handler.run = AsyncMock(side_effect=TimeoutError('unknown outcome'))
            await self.deliver(self.action_event('replace'))
            self.assertFalse(any('✅' in message['text'] for message in self.messages))
            self.assertEqual(self.handler.sessions[self.key]['request']['operation'], operation)
            self.handler.run = self.run_worker
            await self.deliver(self.event('تأیید'))
            self.assertEqual(self.calls[-1]['request']['operation'], operation)
            self.assertIn('✅', self.messages[-1]['text'])


class RepairsOfflineTests(repair_tests.MaintenanceBaleTests):
    async def test_no_model_or_network_required_and_unknown_result_retries_same_operation(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': '', 'ANTHROPIC_API_KEY': ''}), \
             patch('socket.socket.connect', side_effect=AssertionError('external API forbidden')):
            self.assertIsNone(await self.deliver(self.event('تعمیر دستگاه انجام شد')))
            self.assertEqual(self.calls, [])
            await self.form()
            await self.deliver(self.button('no_parts'))
            operation = self.handler.sessions[self.key]['request']['operation']
            self.handler.run = AsyncMock(side_effect=TimeoutError('unknown outcome'))
            await self.deliver(self.button('confirm'))
            self.assertFalse(any('✅' in message['text'] for message in self.messages))
            self.assertEqual(self.handler.sessions[self.key]['request']['operation'], operation)
            self.handler.run = self.worker
            await self.deliver(self.event('تأیید'))
            self.assertEqual(self.calls[-1]['request']['operation'], operation)
            self.assertIn('✅', self.messages[-1]['text'])
