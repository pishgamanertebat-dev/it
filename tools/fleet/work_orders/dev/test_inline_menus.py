import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.bale_ui import StateStore
from tools.fleet.work_orders.channels.bale.message_handler import FormSession, WorkOrderMenuHandler
from tools.fleet.work_orders.dev.test_permissions import PermissionDatabaseTestCase, test_database


class InlineMenusTests(PermissionDatabaseTestCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.key = ('bale', '455740857', '455740857')
        self.store = StateStore(self.db_path.parent / 'ui.json')
        self.sent, self.requests, self.removed = [], [], []
        self.sequence = 0
        self.result = {'ok':False, 'message':'test worker'}

        async def worker(request):
            self.requests.append(request)
            return self.result

        async def send_message(**kwargs):
            self.sent.append(kwargs)
            return SimpleNamespace(message_id=len(self.sent))

        async def edit(**kwargs):
            self.removed.append(kwargs['message_id'])

        self.worker = worker
        self.document_sender = AsyncMock()
        self.handler = self.new_handler()
        self.gateway = SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=SimpleNamespace(
            send_message=send_message, edit_message_reply_markup=edit))})
        for patcher in (
            patch.dict('sys.modules', {'telegram':SimpleNamespace(InlineKeyboardMarkup=SimpleNamespace(de_json=lambda m,b:m))}),
            patch('tools.fleet.work_orders.channels.bale.message_handler.review_context', return_value=None),
            patch('tools.fleet.work_orders.core.staff_dispatch.staff_menu', return_value=('انتخاب سرویسکار', [])),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def new_handler(self):
        return WorkOrderMenuHandler(db_path=self.db_path, worker=self.worker,
            document_sender=self.document_sender, state_store=self.store)

    async def settle(self):
        while self.handler.tasks or self.handler.lifecycle.tasks:
            await asyncio.gather(*list(self.handler.tasks | self.handler.lifecycle.tasks))

    def message(self, text, *, data=None, origin=None, user=None):
        self.sequence += 1
        event = SimpleNamespace(text=text, message_id=str(self.sequence),
            raw_message={'bale_inline_callback':True,'data':data,'origin_message_id':str(origin)} if data else None,
            source=SimpleNamespace(platform='bale', chat_type='dm', user_id=user or self.key[1], chat_id=self.key[2]))
        return self.handler.handle(event, self.gateway, send=lambda *a:None)

    def button(self, message, action):
        return next(b['callback_data'] for row in message['reply_markup']['inline_keyboard']
                    for b in row if b['callback_data'].endswith(':' + action))

    async def batch(self, numbers):
        self.result = {'ok':True, 'orders':[dict(work_order_no=n, item_summary='دستگاه آزمایشی') for n in numbers]}
        session = FormSession(expires=self.handler.clock()+600, stage='BUSY', work_order_type='OIL_CHANGE')
        self.handler.pending[self.key] = session
        await self.handler._run_request(self.key, session, {'action':'create'}, self.gateway, lambda *a:None)
        await self.settle()

    async def test_entry_four_buttons_and_all_type_callbacks(self):
        for action, work_type in [('oil','OIL_CHANGE'), ('greasing','GREASING'), ('air_filter','AIR_FILTER')]:
            self.message('حکم کار')
            await self.settle()
            entry = self.sent[-1]
            labels = [b['text'] for row in entry['reply_markup']['inline_keyboard'] for b in row]
            self.assertEqual(labels, ['تعویض روغن','گریس کاری','هواکش','🚪 انصراف'])
            self.assertNotIn('شماره', entry['text'])
            self.message('', data=self.button(entry, action), origin=len(self.sent))
            await self.settle()
            self.assertEqual(self.requests[-1]['work_order_type'], work_type)
            self.assertEqual(self.requests[-1]['action'], 'propose')
        self.assertEqual(len(self.removed), 3)

    async def test_entry_cancel_is_consumed_and_removed(self):
        self.message('حکم کار')
        await self.settle()
        self.message('', data=self.button(self.sent[-1], 'cancel'), origin=1)
        await self.settle()
        self.assertFalse(self.handler.pending)
        self.assertEqual(self.removed, [1])
        self.assertFalse(self.requests)

    async def test_batch_cards_are_independent_and_confirmation_targets_first_order(self):
        numbers = ['OC-1405-06-25-003', 'OC-1405-06-25-004']
        await self.batch(numbers)
        first, second = self.sent[:2]
        for index, message in enumerate((first, second)):
            self.assertIn(numbers[index], message['text'])
            self.assertEqual(sum(map(len, message['reply_markup']['inline_keyboard'])), 2)
            self.assertNotIn('ثبت تایید OC-', message['text'])
        self.assertFalse(self.removed)
        self.result = {'ok':True,'work_order_type':'OIL_CHANGE'}
        self.message('', data=self.button(first, 'review_confirm'), origin=1)
        await self.settle()
        self.assertEqual(self.requests[-1], {'action':'confirm_review','bale_id':self.key[1],'work_order_no':numbers[0]})
        self.assertEqual(self.handler.pending[self.key].stage, 'STAFF')
        self.assertEqual(self.removed, [1])
        self.assertTrue(self.handler.reviews[(*self.key, numbers[1])].keyboard_revision)
        self.assertNotIn('dispatch', [r['action'] for r in self.requests])

    async def test_edit_after_restore_uses_card_order_and_returns_proposal_buttons(self):
        number = 'OC-1405-06-25-003'
        await self.batch([number, 'OC-1405-06-25-004'])
        data = self.button(self.sent[0], 'review_edit')
        self.handler = self.new_handler()
        self.result = {'ok':True,'work_order_type':'OIL_CHANGE','proposal':{
            'work_order_type':'OIL_CHANGE','items':[], 'plan_date':'1405/06/25','cutoff':'test'}}
        self.message('', data=data, origin=1)
        await self.settle()
        self.assertEqual(self.requests[-1]['work_order_no'], number)
        self.assertEqual(self.requests[-1]['action'], 'edit')
        self.assertEqual(self.handler.pending[self.key].stage, 'PROPOSAL')
        self.assertEqual(sum(map(len,self.sent[-1]['reply_markup']['inline_keyboard'])), 4)
        self.assertEqual(self.removed, [1])

    async def test_failed_document_delivery_has_no_review_buttons(self):
        self.document_sender.side_effect = RuntimeError('upload failed')
        with self.assertLogs('tools.fleet.work_orders.channels.bale.message_handler', level='ERROR'):
            await self.batch(['OC-1405-06-25-003'])
        self.assertFalse(self.handler.reviews)
        self.assertTrue(all('reply_markup' not in m for m in self.sent))

    async def test_wrong_user_revoked_and_busy_do_not_consume_review_card(self):
        number = 'OC-1405-06-25-003'
        await self.batch([number])
        self.requests.clear()
        data = self.button(self.sent[0], 'review_confirm')
        self.message('', data=data, origin=1, user='1004')
        await self.settle()
        self.assertFalse(self.requests)
        self.handler.pending[self.key].stage = 'BUSY'
        self.assertEqual(self.message('', data=data, origin=1)['reason'], 'work-order-busy')
        self.handler.pending[self.key].stage = 'REVIEW'
        with test_database(self.db_path) as con:
            con.execute("UPDATE service_work_order_users SET active=0 WHERE bale_id='455740857'")
        self.message('', data=data, origin=1)
        await self.settle()
        self.assertFalse(self.requests)
        self.assertTrue(self.handler.reviews[(*self.key, number)].keyboard_revision)
        self.assertFalse(self.removed)

    async def test_single_preview_also_has_review_buttons(self):
        self.result = {'ok':True, 'order':{'work_order_no':'OC-1405-06-25-003',
            'work_order_type':'OIL_CHANGE','label':'تعویض روغن','item_count':1,'file_name':'test.xlsx'}}
        session = FormSession(expires=self.handler.clock()+600, stage='BUSY')
        self.handler.pending[self.key] = session
        await self.handler._run_request(self.key, session, {'action':'preview_latest'}, self.gateway, lambda *a:None)
        self.assertTrue(self.button(self.sent[-1], 'review_confirm'))
        self.assertTrue(self.button(self.sent[-1], 'review_edit'))

    async def test_typed_review_consumes_the_matching_card_only(self):
        numbers = ['OC-1405-06-25-003', 'OC-1405-06-25-004']
        await self.batch(numbers)
        self.result = {'ok':True,'work_order_type':'OIL_CHANGE'}
        self.message('ثبت تایید ' + numbers[0])
        await self.settle()
        self.assertEqual(self.removed, [1])
        self.assertFalse(self.handler.reviews[(*self.key, numbers[0])].keyboard_revision)
        self.assertTrue(self.handler.reviews[(*self.key, numbers[1])].keyboard_revision)

    async def test_old_menu_cannot_select_after_new_entry(self):
        self.message('حکم کار')
        await self.settle()
        old_data = self.button(self.sent[-1], 'oil')
        self.message('حکم کار')
        await self.settle()
        self.assertEqual(self.removed, [1])
        self.assertEqual(self.message('', data=old_data, origin=1)['reason'], 'inline-rejected')
        await self.settle()
        self.assertFalse(self.requests)

    async def test_disabled_type_not_shown_and_not_selected(self):
        from tools.fleet.work_orders.core.registry import list_work_order_types
        items = list_work_order_types(enabled_only=False)
        disabled = [{**item, 'enabled':False} if item['key'] == 'OIL_CHANGE' else item for item in items]
        with patch('tools.fleet.work_orders.core.registry.list_work_order_types',
                   side_effect=lambda enabled_only=True:[i for i in disabled if i['enabled'] or not enabled_only]):
            self.message('حکم کار')
            await self.settle()
            data = [b['callback_data'] for row in self.sent[-1]['reply_markup']['inline_keyboard'] for b in row]
            self.assertFalse(any(d.endswith(':oil') for d in data))
            forged = data[0].rsplit(':',1)[0] + ':oil'
            self.assertEqual(self.message('', data=forged, origin=1)['reason'], 'inline-rejected')
            await self.settle()
            self.assertFalse(self.requests)

    async def show_single(self, work_type, number):
        order = {'work_order_no':number, 'work_order_type':work_type,
                 'label':work_type, 'item_count':2, 'file_name':number+'.xlsx'}
        self.result = {'ok':True, 'order':order}
        session = FormSession(expires=self.handler.clock()+600, stage='BUSY', work_order_type=work_type)
        self.handler.pending[self.key] = session
        await self.handler._run_request(self.key, session, {'action':'create'}, self.gateway, lambda *a:None)
        await self.settle()
        return order

    async def test_air_filter_and_greasing_review_and_edit_are_inline(self):
        for kind, number in [('AIR_FILTER','AF-1405-06-25-003'), ('GREASING','GR-1405-06-25-003')]:
            await self.show_single(kind, number)
            message_id = len(self.sent)
            message = self.sent[-1]
            self.assertTrue(self.button(message, 'review_confirm'))
            self.assertTrue(self.button(message, 'review_edit'))
            self.assertNotIn('بنویسید', message['text'])
            self.assertEqual(self.handler.reviews[(*self.key,number)].work_order_type, kind)
            self.result = {'ok':True,'work_order_type':kind,'proposal':{
                'work_order_type':kind,'items':[],'plan_date':'1405/06/25','cutoff':'test'}}
            self.message('', data=self.button(message, 'review_edit'), origin=message_id)
            await self.settle()
            self.assertEqual(self.requests[-1]['action'], 'edit')
            self.assertEqual(self.requests[-1]['work_order_no'], number)
            self.assertEqual(self.handler.pending[self.key].work_order_type, kind)
            self.assertTrue(self.button(self.sent[-1], 'confirm'))
            self.assertIn(message_id, self.removed)

    async def staff_step(self, kind, number):
        order = await self.show_single(kind, number)
        review_message = self.sent[-1]
        review_id = len(self.sent)
        self.result = {'ok':True, 'work_order_type':kind}
        options = [
            {'id':4,'roster_id':1,'display_name':'محسن غضنفری'},
            {'id':2,'roster_id':2,'display_name':'حسین محمودی'},
            {'id':3,'roster_id':3,'display_name':'پوریا آسترکی'},
        ]
        with patch('tools.fleet.work_orders.core.staff_dispatch.staff_menu', return_value=('old numbered menu', options)):
            self.message('', data=self.button(review_message, 'review_confirm'), origin=review_id)
            await self.settle()
        self.assertIn(review_id, self.removed)
        return order, options

    async def test_three_staff_and_back_for_all_types_and_back_retains_order(self):
        for kind, number in [('AIR_FILTER','AF-1405-06-25-003'),
                             ('GREASING','GR-1405-06-25-003'), ('OIL_CHANGE','OC-1405-06-25-003')]:
            order, options = await self.staff_step(kind, number)
            staff_id = len(self.sent)
            staff_message = self.sent[-1]
            buttons = [b for row in staff_message['reply_markup']['inline_keyboard'] for b in row]
            self.assertEqual([b['text'] for b in buttons], [s['display_name'] for s in options] + ['↩️ بازگشت'])
            self.assertNotIn('old numbered menu', staff_message['text'])
            self.result = {'ok':True,'order':order}
            before = len(self.requests)
            self.message('', data=self.button(staff_message, 'staff_back'), origin=staff_id)
            await self.settle()
            self.assertEqual(self.requests[before:], [{'action':'preview','bale_id':self.key[1],'work_order_no':number}])
            self.assertEqual(self.handler.pending[self.key].order_no, number)
            self.assertEqual(self.handler.pending[self.key].stage, 'REVIEW')
            self.assertTrue(self.button(self.sent[-1], 'review_edit'))
            self.assertTrue(self.button(self.sent[-1], 'review_confirm'))
            self.assertIn(staff_id, self.removed)

    async def test_each_staff_button_passes_exact_ids_and_duplicate_does_not_send_twice(self):
        for choice, kind in enumerate(('AIR_FILTER','GREASING','OIL_CHANGE'), 1):
            number = {'AIR_FILTER':'AF','GREASING':'GR','OIL_CHANGE':'OC'}[kind] + '-1405-06-25-003'
            _order, options = await self.staff_step(kind, number)
            origin = len(self.sent)
            data = self.button(self.sent[-1], f'staff_{choice}')
            dispatch = AsyncMock(return_value='ارسال آزمایشی موفق')
            with patch('tools.fleet.work_orders.channels.bale.staff_flow.dispatch', dispatch):
                self.message('', data=data, origin=origin)
                self.message('', data=data, origin=origin)
                await self.settle()
                selected = options[choice-1]
                dispatch.assert_awaited_once_with(self.gateway, number, self.key[1], self.key[2], selected['id'], selected['roster_id'])
                self.message('', data=data, origin=origin)
                await self.settle()
                self.assertEqual(dispatch.await_count, 1)
            self.assertIn(origin, self.removed)

    async def test_staff_back_invalidates_old_staff_buttons_and_persists_review(self):
        order, options = await self.staff_step('GREASING','GR-1405-06-25-003')
        origin = len(self.sent)
        old_choice = self.button(self.sent[-1], 'staff_1')
        self.result = {'ok':True,'order':order}
        self.message('', data=self.button(self.sent[-1], 'staff_back'), origin=origin)
        await self.settle()
        self.handler = self.new_handler()
        with patch('tools.fleet.work_orders.channels.bale.staff_flow.dispatch', new=AsyncMock()) as dispatch:
            self.assertEqual(self.message('', data=old_choice, origin=origin)['reason'], 'inline-rejected')
            await self.settle()
            dispatch.assert_not_called()
        self.assertEqual(self.handler.pending[self.key].order_no, order['work_order_no'])
        self.assertEqual(self.handler.reviews[(*self.key,order['work_order_no'])].work_order_type, 'GREASING')

    async def test_staff_selection_revoked_permission_and_invalid_index_do_not_dispatch(self):
        await self.staff_step('AIR_FILTER','AF-1405-06-25-003')
        origin = len(self.sent)
        data = self.button(self.sent[-1], 'staff_1')
        with patch('tools.fleet.work_orders.channels.bale.staff_flow.dispatch', new=AsyncMock()) as dispatch:
            forged = data.rsplit(':',1)[0] + ':staff_99'
            self.assertEqual(self.message('', data=forged, origin=origin)['reason'], 'inline-rejected')
            with test_database(self.db_path) as con:
                con.execute("UPDATE service_work_order_users SET active=0 WHERE bale_id='455740857'")
            self.assertEqual(self.message('', data=data, origin=origin)['reason'], 'inline-rejected')
            await self.settle()
            dispatch.assert_not_called()

    async def test_staff_snapshot_survives_restart_and_preserves_recipient(self):
        order, options = await self.staff_step('OIL_CHANGE','OC-1405-06-25-003')
        origin = len(self.sent)
        data = self.button(self.sent[-1], 'staff_2')
        self.handler = self.new_handler()
        dispatch = AsyncMock(return_value='ارسال آزمایشی موفق')
        with patch('tools.fleet.work_orders.channels.bale.staff_flow.dispatch', dispatch):
            self.message('', data=data, origin=origin)
            await self.settle()
        dispatch.assert_awaited_once_with(self.gateway, order['work_order_no'], self.key[1], self.key[2],
                                         options[1]['id'], options[1]['roster_id'])

    async def test_empty_staff_roster_still_allows_back(self):
        order = await self.show_single('GREASING','GR-1405-06-25-003')
        self.result = {'ok':True, 'work_order_type':'GREASING'}
        self.message('', data=self.button(self.sent[-1], 'review_confirm'), origin=len(self.sent))
        await self.settle()
        buttons = [b for row in self.sent[-1]['reply_markup']['inline_keyboard'] for b in row]
        self.assertEqual([b['text'] for b in buttons], ['↩️ بازگشت'])
        self.assertIn('سرویسکار فعالی موجود نیست', self.sent[-1]['text'])
