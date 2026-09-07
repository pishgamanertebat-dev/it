from unittest.mock import patch
from tools.fleet.work_orders.dev.test_bale_creation_flow import CreationFlowTests


class ProposalFormTests(CreationFlowTests):
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
