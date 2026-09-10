import asyncio
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from tools.fleet.work_orders.channels.bale import staff_flow as flow
from tools.fleet.work_orders.core import staff_dispatch as core
from tools.fleet.work_orders.core.review import confirm_document_review
from tools.fleet.work_orders.dev.test_staff_dispatch import StaffDispatchTests
from tools.fleet.work_orders.dev.test_permissions import test_database


class RosterTests(StaffDispatchTests):
    def test_common_roster_and_selected_name_snapshot(self):
        with test_database(self.db_path) as con:
            importlib.import_module('tools.fleet.work_orders.migrations.004_staff_roster').migrate(con)
        menus = [core.staff_menu(t)[0] for t in ('AIR_FILTER','GREASING','OIL_CHANGE')]
        self.assertEqual(menus[0], menus[1])
        self.assertEqual(menus[1], menus[2])
        options = core.staff_options()
        self.assertEqual([o['display_name'] for o in options],['محسن غضنفری','حسین محمودی','پوریا آسترکی'])
        self.assertEqual({o['bale_id'] for o in options},{'85539397'})
        order = self.create()
        number = order['work_order_no']
        confirm_document_review(number,'455740857')
        result = core.prepare_dispatch(number,'455740857','455740857',options[1]['id'],options[1]['roster_id'])
        self.assertEqual(result['staff_name'],'حسین محمودی')
        with self.assertRaises(ValueError):
            core.prepare_dispatch(number,'455740857','455740857',options[2]['id'],options[2]['roster_id'])
        with test_database(self.db_path) as con:
            con.execute("UPDATE service_work_orders SET status='SENT' WHERE work_order_no=?",(number,))
            con.execute("UPDATE service_staff_roster SET display_name='RENAMED' WHERE id=2")
        self.assertEqual(core.recipient_orders('85539397')[0]['display_name'],'حسین محمودی')


class ReceiptChoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        flow.receipt_choices.clear()
        flow.busy.clear()
        self.replies=[]
        self.received=[]
        context_patch = patch.object(flow, 'review_context', return_value=None)
        context_patch.start()
        self.addCleanup(context_patch.stop)
        self.orders=[dict(work_order_no=n,work_order_type='AIR_FILTER',jalali_date='1405/06/09',acknowledged_at=None,notified_at=None) for n in ('AF-1405-06-09-002','AF-1405-06-09-001')]
        async def receipt(gateway,actor,chat,number):
            self.received.append(number)
            flow.busy.discard(actor)
        for target,value in [('core.recipient_orders',lambda actor:self.orders),('receipt',receipt)]:
            p=patch.object(flow.core,'recipient_orders',value) if target.startswith('core.') else patch.object(flow,target,value)
            p.start()
            self.addCleanup(p.stop)

    def message(self,text,actor='85539397',chat='85539397'):
        event=SimpleNamespace(text=text,source=SimpleNamespace(platform='bale',chat_type='dm',user_id=actor,chat_id=chat))
        return flow.handle_staff_receipt(event,None,send=lambda g,c,t:self.replies.append(t))

    async def settle(self):
        await asyncio.gather(*list(flow.tasks))

    async def test_numbers_keep_displayed_mapping_when_new_orders_arrive(self):
        self.assertEqual(self.message('تایید')['reason'],'staff-receipt-select')
        original=[o['work_order_no'] for o in self.orders]
        self.orders.insert(0,{**self.orders[0],'work_order_no':'AF-1405-06-09-003'})
        self.message('۱')
        await self.settle()
        self.message('١')
        await self.settle()
        self.message('2')
        await self.settle()
        self.assertEqual(self.received,[original[0],original[0],original[1]])

    async def test_invalid_expired_and_other_chat_do_not_acknowledge(self):
        self.message('تایید')
        self.assertIsNone(self.message('1',chat='other'))
        self.assertIsNone(self.message('1',actor='9999'))
        self.assertEqual(self.message('9')['reason'],'staff-receipt-invalid-choice')
        flow.receipt_choices[('85539397','85539397')]['expires']=0
        self.assertEqual(self.message('1')['reason'],'staff-receipt-expired')
        self.assertEqual(self.received,[])
