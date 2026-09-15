from unittest.mock import patch
import asyncio
from types import SimpleNamespace
from tools.fleet.work_orders.dev.test_bale_creation_flow import CreationFlowTests


class ProposalFormTests(CreationFlowTests):
    async def test_work_order_reply_uses_plain_text_bale_delivery(self):
        delivered = []
        async def send_message(**kwargs):
            delivered.append(kwargs)
        class Adapter:
            _bot = SimpleNamespace(send_message=send_message)
            async def send(self, *_):
                raise AssertionError('Markdown delivery must not be used')
        gateway = SimpleNamespace(adapters={'bale':Adapter()})
        reply = '1) EX802 PC800-7\nساعت‌کار: 6539.5'
        self.handler._send_reply(gateway, '455740857', reply, lambda *_: None)
        await asyncio.gather(*list(self.handler.tasks))
        self.assertEqual(delivered, [{'chat_id':'455740857', 'text':reply, 'parse_mode':None}])

    async def test_long_reply_chunks_are_delivered_in_order(self):
        delivered=[]
        calls=0
        class Adapter:
            async def send(adapter_self, chat, text):
                nonlocal calls
                call=calls
                calls += 1
                if call == 0:
                    await asyncio.sleep(.02)
                delivered.append(text)
        gateway=SimpleNamespace(adapters={'bale':Adapter()})
        reply=''.join(f'{i:03d} پیشنهاد دستگاه\n' for i in range(400))
        self.handler._send_reply(gateway,'455740857',reply,lambda *_:None)
        await asyncio.gather(*list(self.handler.tasks))
        self.assertGreater(len(delivered),1)
        self.assertEqual(''.join(delivered),reply)

    def test_greasing_render_places_proposals_first_and_commands_last(self):
        from tools.fleet.work_orders.channels.bale.proposal_form import render
        proposal={'work_order_type':'GREASING','plan_date':'1405/06/16','cutoff':'1405/06/15 - روز',
            'items':[{'machine_code':'HD715','action_code':'GREASING_FULL','action_text':'گریسکاری کامل',
                      'components':{'greasing':{'state':'DUE','value':61,'threshold':60,'unit':'ساعت','last_service':'1405/06/10 - روز'}}}],
            'source_warnings':['دادهٔ شب ثبت نشده'], 'warnings':['HD468 نزدیک موعد'],
            'review':[{'code':'EX801','reason':'مبنا مشخص نیست'}]}
        text=render(proposal)
        self.assertLess(text.index('1) دستگاه HD715'),text.index('⚠️ وضعیت اطلاعات:'))
        self.assertLess(text.index('⚠️ وضعیت اطلاعات:'),text.index('⚠️ نزدیک موعد'))
        self.assertLess(text.index('⚠️ نزدیک موعد'),text.index('🔎 نیازمند بررسی'))
        self.assertLess(text.index('🔎 نیازمند بررسی'),text.index('حذف:'))
        self.assertIn('تایید: ساخت اکسل',text)
        self.assertNotIn('تایید: انتخاب شیفت',text)

    def test_oil_render_explains_meter_and_overdue_hours_plainly(self):
        from tools.fleet.work_orders.channels.bale.proposal_form import render
        proposal = {'work_order_type':'OIL_CHANGE', 'plan_date':'1405/06/23', 'cutoff':'1405/06/22 - شب',
            'items':[{'machine_code':'EX802', 'action_code':'OIL_CHANGE_PC800-7_1200',
                      'action_text':'PC800-7 — سرویس 1200 ساعتی',
                      'components':{'oil_change':{'current_meter':6539.5, 'target_meter':6520,
                          'remaining':-19.5, 'last_interval':1000, 'next_interval':1200,
                          'last_service':'1405/06/01 - روز', 'state':'DUE'}}}],
            'source_warnings':['اطلاعات کارکرد قدیمی است؛ آخرین ثبت: 1405/06/22 - شب؛ مانده فقط از داده‌های ثبت‌شده محاسبه شده است.'],
            'warnings':['W473 : تا موعد بعدی 28 ساعت، برای سرویس 2000 ساعتی'], 'review':[]}
        text = render(proposal)
        self.assertIn('1) دستگاه: EX802', text)
        self.assertIn('مدل: PC800-7', text)
        self.assertIn('سرویس مورد نیاز: سرویس 1200 ساعتی', text)
        self.assertIn('ساعت‌کار فعلی دستگاه: 6539.5 ساعت', text)
        self.assertIn('ساعت‌کار موعد تعویض: 6520 ساعت', text)
        self.assertIn('19.5 ساعت از موعد تعویض گذشته است', text)
        self.assertIn('⚠️ نزدیک موعد:\nW473 : تا موعد بعدی 28 ساعت، برای سرویس 2000 ساعتی', text)
        self.assertNotIn('داخل حکم نیستند', text)
        self.assertNotIn('اطلاعات کارکرد قدیمی است', text)
        self.assertNotIn('مانده فقط از داده‌های ثبت‌شده', text)
        self.assertNotIn('\\', text)
        self.assertNotIn('&#x20;', text)

    async def start_proposal(self):
        async def worker(request):
            self.requests.append(request)
            if request['action'] == 'propose':
                return {'ok':True,'proposal':{'plan_date':'1405/06/09','cutoff':'1405/06/08','source_sha256':None,
                    'items':[{'machine_code':c,'machine_name':'دامپتراک','action_code':'AIR_FILTER_OUTER','action_text':'بیرونی'} for c in ('710','712','465')], 'warnings':[], 'review':[]}}
            if request['action'] == 'validate_add':
                return {'ok':False,'error':'INVALID_INPUT','message':'کد ناشناخته'}
            return {'ok':False,'error':'CREATE_FAILED','message':'TEST'}
        self.handler.worker=worker
        self.message('حکم کار')
        self.message('۱')
        await self.settle()
        return next(iter(self.handler.pending.values()))

    async def test_delete_validation_empty_draft_and_return(self):
        session=await self.start_proposal()
        self.message('حذف')
        self.assertEqual(self.message('1 9')['reason'],'work-order-input-rejected')
        self.assertEqual(len(session.proposal['items']),3)
        self.message('۱ ۳')
        self.assertEqual([i['machine_code'] for i in session.proposal['items']],['712'])
        self.message('حذف'); self.message('1')
        self.assertEqual(self.message('تایید')['reason'],'work-order-input-rejected')
        self.assertFalse(any(r['action']=='create' for r in self.requests))
        self.message('اضافه'); self.message('UNKNOWN')
        await self.settle()
        self.assertEqual(session.stage,'ADD_CODES')
        self.message('برگشت')
        self.assertEqual(session.stage,'PROPOSAL')

    async def test_confirm_carries_exact_actions_date_and_prevents_double_create(self):
        session=await self.start_proposal()
        session.proposal['items'][0]['action_code']='AIR_FILTER_INNER_OUTER'
        self.message('تایید')
        self.message('صبح ظهر',message_id='create-once')
        self.message('صبح ظهر',message_id='create-once')
        await self.settle()
        creates=[r for r in self.requests if r['action']=='create']
        self.assertEqual(len(creates),1)
        self.assertEqual(creates[0]['jalali_date'],'1405/06/09')
        self.assertEqual(creates[0]['item_actions']['710'],'AIR_FILTER_INNER_OUTER')

    async def test_added_vehicle_action_constraints(self):
        from tools.fleet.work_orders.channels.bale.proposal_form import add_items
        from tools.fleet.air_filter.rules import OUTER,BOTH
        proposal={'items':[]}
        with self.assertRaises(ValueError):
            add_items(proposal,[{'machine_code':'TR1','machine_name':'خاور'}],OUTER)
        with self.assertRaises(ValueError):
            add_items(proposal,[{'machine_code':'MZ1','machine_name':'مزدا'}],BOTH)
        self.assertEqual(proposal['items'],[])
        add_items(proposal,[{'machine_code':'TR1','machine_name':'خاور'}],BOTH)
        self.assertEqual(proposal['items'][0]['action_code'],BOTH)

    def test_manual_add_removes_machine_from_near_due_warnings(self):
        from tools.fleet.work_orders.channels.bale.proposal_form import add_items
        proposal = {'work_order_type':'OIL_CHANGE', 'items':[],
                    'warnings':['W470 : تا موعد بعدی 12 ساعت، برای سرویس 1200 ساعتی',
                                'HD714 : تا موعد بعدی 20 ساعت، برای سرویس 800 ساعتی']}
        item = {'machine_code':'W470', 'machine_name':'لودر', 'action_code':'OIL_CHANGE_WA470-3_1200',
                'action_text':'WA470-3 — سرویس 1200 ساعتی'}
        add_items(proposal, [item], 'GREASING_FULL')
        self.assertEqual([i['machine_code'] for i in proposal['items']], ['W470'])
        self.assertEqual(proposal['warnings'], ['HD714 : تا موعد بعدی 20 ساعت، برای سرویس 800 ساعتی'])
