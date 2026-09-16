import asyncio
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.bale_ui import MultiSelect, StateStore, KeyboardLifecycle
from tools.fleet.work_orders.channels.bale.message_handler import FormSession, WorkOrderMenuHandler
from tools.fleet.work_orders.dev.test_permissions import PermissionDatabaseTestCase, test_database


class MultiSelectTests(unittest.TestCase):
    def test_generic_selection_and_pagination_have_stable_ids(self):
        picker = MultiSelect([{'id':f'report-{i}','label':f'Report {i}'} for i in range(23)])
        picker.apply('pick_0')
        picker.apply('select_next')
        picker.apply('pick_12')
        picker.apply('select_next')
        picker.apply('pick_22')
        picker = MultiSelect(**picker.to_dict())
        self.assertEqual(picker.confirmed_ids(f'report-{i}' for i in range(23)), {'report-0','report-12','report-22'})
        with self.assertRaises(ValueError):
            picker.confirmed_ids(['changed'])
        with self.assertRaises(ValueError):
            picker.apply('pick_0')
        picker.apply('select_prev')
        picker.apply('pick_12')
        self.assertEqual(picker.selected, ['report-0','report-22'])

    def test_builder_permission_and_refresh_actions(self):
        picker = MultiSelect([{'id':'A','label':'A'}])
        builder = picker.keyboard('reports', permission='report.read', stage='SELECT', roles=frozenset({'READER'}))
        context = dict(stage='SELECT',role='READER',permits=lambda p:True)
        self.assertTrue(builder.actions['pick_0'].refresh)
        self.assertTrue(builder.actions['select_confirm'].refresh)
        self.assertFalse(builder.actions['select_back'].refresh)
        self.assertEqual(builder.build('r', **{**context,'role':'OTHER'})['inline_keyboard'], [])
        with self.assertRaises(PermissionError):
            builder.resolve('ik:reports:r:pick_0','r', **{**context,'permits':lambda p:False})
        picker.apply('pick_0')
        self.assertFalse(picker.keyboard('reports',permission='x',stage='SELECT').actions['select_confirm'].refresh)

    def test_invalid_or_empty_selection_cannot_confirm(self):
        with self.assertRaises(ValueError):
            MultiSelect([{'id':'A','label':'A'}, {'id':'A','label':'A'}])
        with self.assertRaises(ValueError):
            MultiSelect([{'id':'A','label':'A'}], selected=['B'])
        picker = MultiSelect([])
        with self.assertRaises(ValueError):
            picker.confirmed_ids([])


class RemovalTests(PermissionDatabaseTestCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.key = ('bale','455740857','455740857')
        self.now = 1000
        self.store = StateStore(self.db_path.parent / 'ui.json')
        self.messages, self.edits, self.removed = {}, [], []
        self.serial = 0
        self.callback_serial = 0
        self.bot = SimpleNamespace(send_message=self.send_message, edit_message_text=self.edit_message,
                                   edit_message_reply_markup=self.remove_markup)
        self.gateway = SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=self.bot)})
        self.handler = self.new_handler()
        patcher = patch.dict('sys.modules', {'telegram':SimpleNamespace(
            InlineKeyboardMarkup=SimpleNamespace(de_json=lambda m,b:m))})
        patcher.start()
        self.addCleanup(patcher.stop)

    def new_handler(self):
        return WorkOrderMenuHandler(db_path=self.db_path, clock=lambda:self.now, state_store=self.store,
                                    worker=AsyncMock(side_effect=AssertionError('No worker needed for selection')))

    async def send_message(self, **kwargs):
        self.serial += 1
        self.messages[self.serial] = kwargs
        return SimpleNamespace(message_id=self.serial)

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs['message_id'])
        self.messages[kwargs['message_id']] = kwargs

    async def remove_markup(self, **kwargs):
        self.removed.append(kwargs['message_id'])
        self.messages[kwargs['message_id']]['reply_markup'] = None

    async def settle(self):
        while self.handler.tasks or self.handler.lifecycle.tasks:
            await asyncio.gather(*list(self.handler.tasks | self.handler.lifecycle.tasks))

    async def start(self, kind='GREASING', count=5):
        self.session = FormSession(expires=1600, stage='PROPOSAL', work_order_type=kind,
            proposal={'work_order_type':kind, 'plan_date':'1405/06/25','cutoff':'test',
                'items':[{'machine_code':f'M{i}', 'machine_name':f'دستگاه {i}',
                    'action_code':'GREASING_FULL' if kind=='GREASING' else 'AIR_FILTER_OUTER',
                    'action_text':'عملیات آزمایشی', 'evidence':'کارکرد محرمانه برای تست'} for i in range(count)]})
        self.handler.pending[self.key] = self.session
        event = SimpleNamespace(text='حذف',source=SimpleNamespace(platform='bale',chat_type='dm',
                                                               user_id=self.key[1],chat_id=self.key[2]))
        self.handler.handle(event,self.gateway,send=lambda *a:None)
        await self.settle()
        return self.serial

    def data(self, message_id, action):
        return next(b['callback_data'] for row in self.messages[message_id]['reply_markup']['inline_keyboard']
                    for b in row if b['callback_data'].endswith(':'+action))

    def event(self, message_id, action=None, *, data=None, user=None):
        self.callback_serial += 1
        data = data or self.data(message_id, action)
        return SimpleNamespace(text=data,message_id=f'cb:{self.callback_serial}',
            source=SimpleNamespace(platform='bale',chat_type='dm',user_id=user or self.key[1],chat_id=self.key[2]),
            raw_message={'bale_inline_callback':True,'data':data,'origin_message_id':str(message_id)})

    def handle(self, event):
        return self.handler.handle(event,self.gateway,send=lambda *a:None)

    async def click(self, message_id, action):
        result = self.handle(self.event(message_id,action))
        await self.settle()
        return result

    async def test_pick_one_and_three_then_confirm_for_all_service_types(self):
        for kind in ('GREASING','AIR_FILTER','OIL_CHANGE'):
            origin = await self.start(kind)
            original = deepcopy(self.session.proposal)
            text = str(self.messages[origin])
            self.assertNotIn('کارکرد محرمانه', text)
            self.assertNotIn('عملیات آزمایشی', text)
            self.assertIn('M0', text)
            sent_count = self.serial
            await self.click(origin, 'pick_0')
            await self.click(origin, 'pick_2')
            self.assertEqual(self.serial, sent_count)
            self.assertEqual(self.session.proposal, original)
            buttons = [b for row in self.messages[origin]['reply_markup']['inline_keyboard'] for b in row]
            self.assertEqual([b['text'] for b in buttons if b['text'].startswith('🔴')], ['🔴 M0 — دستگاه 0','🔴 M2 — دستگاه 2'])
            self.assertIn('(2)', buttons[-2]['text'])
            await self.click(origin, 'select_confirm')
            self.assertEqual([i['machine_code'] for i in self.session.proposal['items']], ['M1','M3','M4'])
            self.assertEqual(self.session.stage,'PROPOSAL')
            self.assertIsNone(self.session.removal_selection)
            self.assertIn(origin,self.removed)
            self.handler.worker.assert_not_called()

    async def test_toggle_off_and_back_discards_without_deleting(self):
        origin = await self.start()
        original = deepcopy(self.session.proposal)
        await self.click(origin,'pick_0')
        await self.click(origin,'pick_0')
        self.assertEqual(self.session.removal_selection['selected'], [])
        await self.click(origin,'pick_2')
        await self.click(origin,'select_back')
        self.assertEqual(self.session.proposal, original)
        self.assertEqual(self.session.stage,'PROPOSAL')
        self.assertIsNone(self.session.removal_selection)

    async def test_empty_confirm_keeps_same_message_and_no_deletion(self):
        origin = await self.start()
        await self.click(origin,'select_confirm')
        self.assertEqual(self.serial,origin)
        self.assertEqual(len(self.session.proposal['items']),5)
        self.assertEqual(self.session.stage,'REMOVE')
        self.assertIn('حداقل',self.messages[origin]['text'])

    async def test_pagination_and_restart_preserve_selections(self):
        origin = await self.start(count=23)
        await self.click(origin,'pick_0')
        await self.click(origin,'select_next')
        await self.click(origin,'pick_12')
        self.handler = self.new_handler()
        self.session = self.handler.pending[self.key]
        await self.click(origin,'select_next')
        await self.click(origin,'pick_22')
        await self.click(origin,'select_confirm')
        self.assertEqual(len(self.session.proposal['items']),20)
        self.assertFalse({'M0','M12','M22'} & {i['machine_code'] for i in self.session.proposal['items']})

    async def test_duplicate_and_stale_callback_do_not_toggle_twice(self):
        origin = await self.start()
        event = self.event(origin,'pick_0')
        old_data = self.data(origin,'pick_2')
        self.handle(event)
        self.assertEqual(self.handle(event)['reason'],'inline-transition-pending')
        await self.settle()
        self.assertEqual(self.handle(event)['reason'],'inline-duplicate-callback')
        self.assertEqual(self.handle(self.event(origin,data=old_data))['reason'],'inline-rejected')
        await self.settle()
        self.assertEqual(self.session.removal_selection['selected'],['M0'])

    async def test_wrong_user_revoked_and_expired_do_not_change_draft(self):
        origin = await self.start()
        event = self.event(origin,'pick_0',user='1004')
        self.assertEqual(self.handle(event)['reason'],'inline-rejected')
        with test_database(self.db_path) as con:
            con.execute("UPDATE service_work_order_users SET active=0 WHERE bale_id='455740857'")
        self.assertEqual(self.handle(self.event(origin,'pick_0'))['reason'],'inline-rejected')
        await self.settle()
        self.assertEqual(self.session.removal_selection['selected'],[])
        self.assertEqual(len(self.session.proposal['items']),5)

    async def test_edit_failure_replaces_keyboard_preserving_selection(self):
        origin = await self.start()
        with patch.object(self.handler.lifecycle,'update_message',new=AsyncMock(side_effect=TimeoutError())), \
             self.assertLogs('tools.fleet.work_orders.channels.bale.message_handler',level='WARNING'):
            await self.click(origin,'pick_0')
        self.assertGreater(self.serial,origin)
        self.assertIn(origin,self.removed)
        self.assertEqual(self.session.removal_selection['selected'],['M0'])
        await self.click(self.serial,'select_confirm')
        self.assertEqual(len(self.session.proposal['items']),4)

    async def test_changed_proposal_cannot_delete_wrong_items(self):
        origin = await self.start()
        await self.click(origin,'pick_0')
        self.session.proposal['items'].reverse()
        await self.click(origin,'select_confirm')
        self.assertEqual(len(self.session.proposal['items']),5)
        self.assertEqual(self.session.stage,'REMOVE')

    async def test_expired_selection_is_rejected_without_deletion(self):
        origin = await self.start()
        await self.click(origin,'pick_0')
        event = self.event(origin,'select_confirm')
        self.now += 601
        self.assertEqual(self.handle(event)['reason'],'inline-rejected')
        await self.settle()
        self.assertEqual(len(self.session.proposal['items']),5)
        self.handler.worker.assert_not_called()

    async def test_empty_proposal_has_back_and_cannot_delete(self):
        origin = await self.start(count=0)
        await self.click(origin,'select_confirm')
        self.assertEqual(self.session.proposal['items'],[])
        await self.click(origin,'select_back')
        self.assertEqual(self.session.stage,'PROPOSAL')

    async def test_old_expiry_does_not_remove_refreshed_same_message(self):
        lifecycle = KeyboardLifecycle()
        lifecycle.pending.add('test')
        lifecycle.bind('test',self.gateway,self.key[2],'99',ttl=0)
        await asyncio.sleep(0.01)
        lifecycle.bind('test',self.gateway,self.key[2],'99',ttl=600)
        lifecycle.pending.remove('test')
        await asyncio.sleep(0.3)
        self.assertNotIn(99,self.removed)
        lifecycle.timers['test'].cancel()
