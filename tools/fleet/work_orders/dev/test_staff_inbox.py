import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.fleet.work_orders.channels.bale import staff_flow as flow
from tools.fleet.work_orders.dev.test_permissions import PermissionDatabaseTestCase


class StaffInboxTests(PermissionDatabaseTestCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        flow.receipt_choices.clear()
        flow.busy.clear()
        self.replies = []
        pdf = self.db_path.parent / 'order.pdf'
        pdf.write_bytes(b'%PDF-test')
        self.orders = [dict(work_order_no=f'OC-1405-06-18-00{i}', work_order_type='OIL_CHANGE',
                            jalali_date='1405/06/18', acknowledged_at=None, notified_at=None,
                            pdf_path=str(pdf)) for i in (1, 2)]
        self.bot = SimpleNamespace(send_document=AsyncMock(), send_message=AsyncMock())
        self.gateway = SimpleNamespace(adapters={'bale': SimpleNamespace(_bot=self.bot)})
        self.receipt = AsyncMock(side_effect=lambda *args: flow.busy.discard(args[1]))
        for p in [patch('tools.fleet.work_orders.core.db.DB_PATH', self.db_path),
                  patch('tools.fleet.work_orders.core.permissions.DB_PATH', self.db_path),
                  patch.object(flow.core, 'is_recipient', side_effect=lambda actor: actor == '85539397'),
                  patch.object(flow.core, 'recipient_orders', side_effect=lambda actor: self.orders if actor == '85539397' else []),
                  patch.object(flow, 'receipt', self.receipt)]:
            p.start()
            self.addCleanup(p.stop)

    def message(self, text, actor='85539397', chat=None):
        event = SimpleNamespace(text=text, source=SimpleNamespace(platform='bale', chat_type='dm', user_id=actor, chat_id=chat or actor))
        return flow.handle_staff_receipt(event, self.gateway, send=lambda g, c, t: self.replies.append(t))

    async def settle(self):
        await asyncio.gather(*list(flow.tasks))

    async def test_list_selection_replays_pdf_and_requires_explicit_confirmation(self):
        self.assertEqual(self.message('حکم کار')['reason'], 'staff-orders-list')
        original = self.orders[1]['work_order_no']
        self.orders.insert(0, {**self.orders[0], 'work_order_no': 'OC-1405-06-18-003'})
        self.assertEqual(self.message('۲')['reason'], 'staff-order-preview')
        await self.settle()
        self.receipt.assert_not_called()
        self.assertIn(original, self.bot.send_document.call_args.kwargs['caption'])
        self.assertIsNone(self.message('پیام متفرقه'))
        flow.receipt_choices.clear()  # Simulate process/session loss.
        self.message('تایید')
        await self.settle()
        self.assertEqual(self.receipt.call_args.args[-1], original)

    async def test_empty_single_and_manager_routing(self):
        self.assertIsNone(self.message('حکم کار', actor='455740857'))
        self.assertIsNone(self.message('حکم کار', actor='9999'))
        self.orders.pop()
        self.assertEqual(self.message('حکم کار')['reason'], 'staff-order-preview')
        await self.settle()
        self.orders[0]['acknowledged_at'] = 'done'
        self.assertEqual(self.message('حکم کار')['reason'], 'staff-orders-empty')
        self.assertEqual(self.replies[-1], 'شما حکم کار تایید نشده ندارید.')

    async def test_failed_pdf_does_not_allow_bare_confirmation(self):
        self.orders.pop()
        self.bot.send_document.side_effect = RuntimeError('upload failed')
        with self.assertLogs(flow.logger, level='ERROR'):
            self.message('حکم کار')
            await self.settle()
        self.assertEqual(self.message('تایید')['reason'], 'staff-order-needs-preview')
        self.receipt.assert_not_called()

    async def test_stale_selection_is_rejected(self):
        self.message('حکم کار')
        self.orders[0]['acknowledged_at'] = 'done'
        self.assertEqual(self.message('۱')['reason'], 'staff-order-stale')
        self.bot.send_document.assert_not_called()

    async def test_installed_plugin_routes_inbox_before_registration(self):
        import importlib.util
        import sys
        from pathlib import Path
        from types import ModuleType
        plugin_path = Path('C:/Users/win-10/AppData/Local/hermes/plugins/komatso-bale-registry/__init__.py')
        pairing = ModuleType('gateway.pairing')
        pairing.PairingStore = object
        spec = importlib.util.spec_from_file_location('_inbox_plugin_test', plugin_path)
        plugin = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'gateway.pairing': pairing}):
            spec.loader.exec_module(plugin)
        self.orders.clear()
        event = SimpleNamespace(text='حکم کار', source=SimpleNamespace(platform='bale', chat_type='dm', user_id='85539397', chat_id='85539397'))
        with patch.object(plugin, '_handle_overflow_report', return_value=None), patch.object(plugin, '_send', side_effect=lambda g,c,t: self.replies.append(t)):
            self.assertEqual(plugin._handle_bale(event, self.gateway)['reason'], 'staff-orders-empty')
        self.assertEqual(self.replies[-1], 'شما حکم کار تایید نشده ندارید.')
