"""AF uses the shared delivery/version contracts; no operational writes or sends."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.fleet.work_orders.dev import test_oil_p1_safety as shared
from tools.fleet.work_orders.dev.test_greasing_safety import GreasingSafetyTests as Contracts
from tools.fleet.work_orders.dev.test_permissions import test_database
from tools.fleet.work_orders.core import delivery, service, staff_dispatch, review, oil_safety
from tools.fleet.work_orders.channels.bale import create_worker, staff_flow
from tools.fleet.work_orders.channels.bale.proposal_form import creation_metadata, add_items
from tools.fleet.air_filter.rules import BOTH, OUTER


class AirFilterSafetyTests(unittest.TestCase):
    prepare = shared.OilSafetyTests.prepare
    read = shared.OilSafetyTests.read
    resolve = shared.OilSafetyTests.resolve
    age = shared.OilSafetyTests.age
    crash = shared.OilSafetyTests.crash
    uncertain = shared.OilSafetyTests.uncertain
    create = Contracts.create
    revision_request = Contracts.revision_request

    def setUp(self):
        shared.OilSafetyTests.setUp(self)
        self.catalog=[dict(code=c,name=n,daily=[],duplicate=False) for c,n in
            [('710','دامپتراک'),('717','دامپتراک'),('719','دامپتراک'),('720','دامپتراک'),
             ('721','دامپتراک'),('DG1','ژنراتور'),('EX1252','بیل مکانیکی')]]
        p=patch('tools.fleet.air_filter.proposal.read_source',return_value=(self.catalog,(7,24),(7,25),[],'fixed'))
        p.start(); self.addCleanup(p.stop)
        with test_database(self.db_path) as con:
            con.executemany('INSERT INTO machines(canonical_code) VALUES (?)',[(f'HD{c}',) for c in ('710','717','719','720','721')])

    def request(self):
        return dict(action='create',bale_id=shared.MANAGER,work_order_type='AIR_FILTER',
                    jalali_date='1405/07/25',shift='صبح-ظهر',machine_codes=['710','717'],
                    item_actions={'710':OUTER,'717':BOTH})

    test_revision_history_and_old_callbacks = Contracts.test_revision_history_and_old_callbacks
    test_timeout_blocks_ordinary_and_direct_retry = Contracts.test_timeout_blocks_ordinary_and_direct_retry
    test_bale_retry_requires_explicit_decision = Contracts.test_bale_retry_requires_explicit_decision
    test_version_fence_before_transport = Contracts.test_version_fence_before_transport
    test_invalid_tokens = Contracts.test_invalid_tokens
    test_legacy_sending_recovers_conservatively = Contracts.test_legacy_sending_recovers_conservatively
    test_received_decision_audit_and_receipt = Contracts.test_received_decision_audit_and_receipt
    test_late_completion_and_tokens = Contracts.test_late_completion_and_tokens
    test_crash_lease_and_restart = Contracts.test_crash_lease_and_restart
    test_pretransfer_failure_can_retry = Contracts.test_pretransfer_failure_can_retry
    test_revision_cannot_race_claim = Contracts.test_revision_cannot_race_claim
    test_unauthorized_resolution_denied = Contracts.test_unauthorized_resolution_denied
    test_actual_menu_recovery_and_repeat = Contracts.test_actual_menu_recovery_and_repeat
    test_concurrent_duplicate_request_reuses_order = Contracts.test_concurrent_duplicate_request_reuses_order
    test_pdf_change_delete_and_regeneration_require_fresh_review = Contracts.test_pdf_change_delete_and_regeneration_require_fresh_review
    test_preview_cannot_be_swapped_before_confirmation = Contracts.test_preview_cannot_be_swapped_before_confirmation
    test_actual_bale_upload_uses_approved_bytes_even_if_path_changes = Contracts.test_actual_bale_upload_uses_approved_bytes_even_if_path_changes
    test_excel_change_also_blocks_delivery = Contracts.test_excel_change_also_blocks_delivery
    test_missing_legacy_pdf_is_actionable_without_regeneration_or_db_change = Contracts.test_missing_legacy_pdf_is_actionable_without_regeneration_or_db_change

    def test_failed_revision_preserves_previous(self):
        old=self.create()
        with patch('tools.fleet.work_orders.types.air_filter.builder.build_document',side_effect=ValueError('test failure')):
            self.assertFalse(create_worker.execute_request(self.revision_request(old),db_path=self.db_path)['ok'])
        self.assertIsNone(service.get_work_order(old)['superseded_by'])
        self.assertEqual(len(self.read('SELECT * FROM service_work_orders')),1)
        self.prepare(old)
        delivery.send_work_order(work_order_no=old,sender=SimpleNamespace(send_document=lambda **kw:None))
        self.assertEqual(service.get_work_order(old)['status'],'SENT')

    def test_multi_machine_action_shift_and_revision_identity(self):
        old=self.create(); req=self.request()
        reordered=dict(req,machine_codes=['717','710'],shift='ظهر-صبح')
        self.assertEqual(self.create(reordered),old)
        self.assertNotEqual(self.create(dict(req,shift='شب')),old)
        changed=dict(req,item_actions={'710':BOTH,'717':BOTH})
        self.assertFalse(create_worker.execute_request(changed,db_path=self.db_path)['ok'])
        revision=self.revision_request(old)
        revision.update(machine_codes=['719','720'],item_actions={'719':BOTH,'720':OUTER})
        new=self.create(revision)
        self.assertEqual(service.get_work_order(old)['superseded_by'],new)
        self.assertEqual(service.get_work_order(new)['revision_of'],old)
        self.assertEqual(self.create(revision),new)

    def test_ambiguous_historical_duplicates_are_not_selected_or_rewritten(self):
        old=self.create()
        with test_database(self.db_path) as con:
            con.execute('UPDATE service_work_orders SET operation_key=NULL')
            con.execute("INSERT INTO service_work_orders(work_order_no,work_order_type,jalali_date,shift,status,created_by) SELECT 'LEGACY-DUP',work_order_type,jalali_date,shift,status,created_by FROM service_work_orders")
            con.execute("INSERT INTO service_work_order_items(work_order_id,item_no,machine_code,action_code,action_text) SELECT (SELECT id FROM service_work_orders WHERE work_order_no='LEGACY-DUP'),item_no,machine_code,action_code,action_text FROM service_work_order_items WHERE work_order_id=(SELECT id FROM service_work_orders WHERE work_order_no=?)",(old,))
        before=self.read('SELECT * FROM service_work_orders')
        self.assertFalse(create_worker.execute_request(self.request(),db_path=self.db_path)['ok'])
        self.assertEqual(before,self.read('SELECT * FROM service_work_orders'))

    def test_sent_reissue_preserves_history_and_requires_reason_and_stable_id(self):
        old=self.create(); self.prepare(old)
        delivery.send_work_order(work_order_no=old,sender=SimpleNamespace(send_document=lambda **kw:None))
        before=service.get_work_order(old)
        args=dict(work_order_type='AIR_FILTER',jalali_date=before['jalali_date'],shift=before['shift'],
                  machine_codes=['710','717'],item_actions=self.request()['item_actions'],created_by=before['created_by'],revision_of=old)
        for extra in ({},{'reissue_reason':'verified'}):
            with self.assertRaises(ValueError): service.create_work_order(**args,**extra)
        request=dict(action='reissue',bale_id=shared.MANAGER,work_order_no=old,reason='verified test correction',operation_id='af-reissue')
        result=create_worker.execute_request(request,db_path=self.db_path)
        self.assertTrue(result['ok'],result)
        repeated=create_worker.execute_request(request,db_path=self.db_path)
        self.assertEqual(result['orders'][0]['work_order_no'],repeated['orders'][0]['work_order_no'])
        self.assertEqual(repeated['orders'][0]['creation_outcome'],'reused')
        after=service.get_work_order(old)
        for key in ('status','sent_at','send_attempts','pdf_path','excel_path'):
            self.assertEqual(before[key],after[key])

    def test_sending_and_uncertain_parent_blocks_revision_and_shift_change(self):
        old=self.create(); self.prepare(old)
        token=staff_dispatch.claim_send(old)
        for state in ('SENDING','UNCERTAIN'):
            with test_database(self.db_path) as con:
                con.execute('UPDATE service_work_order_dispatch SET state=?',(state,))
            self.assertFalse(create_worker.execute_request(self.revision_request_for(old),db_path=self.db_path)['ok'])
        self.assertIsNone(service.get_work_order(old)['superseded_by'])

    def revision_request_for(self, number):
        return dict(self.request(),proposal=dict(cutoff='',plan_date='1405/07/25',source_sha256=None,revision_of=number))

    def test_legacy_excel_only_review_is_blocked_until_real_pdf_preview(self):
        number=self.create(); order=service.get_work_order(number)
        import hashlib
        fingerprint=hashlib.sha256(Path(order['excel_path']).read_bytes()).hexdigest()
        with test_database(self.db_path) as con:
            con.execute('UPDATE service_work_orders SET notes=? WHERE work_order_no=?',
                ('MANAGER_DOCUMENT_REVIEW: '+json.dumps({'bale_id':shared.MANAGER,'sha256':fingerprint}),number))
        with self.assertRaises(review.DocumentReviewRequired):
            review.confirm_document_review(number,shared.MANAGER)
        with self.assertRaises(review.DocumentReviewRequired):
            staff_dispatch.prepare_dispatch(number,shared.MANAGER,shared.MANAGER,self.staff,self.roster)
        self.prepare(number)
        self.assertTrue(review.approved_pdf_bytes(service.get_work_order(number)))

    def test_source_devices_manual_add_identity_and_generator_policy(self):
        from tools.fleet.work_orders.types.air_filter.builder import get_items
        request=dict(action='validate_add',bale_id=shared.MANAGER,work_order_type='AIR_FILTER',machine_codes=['717','719','720','721'])
        result=create_worker.execute_request(request,db_path=self.db_path)
        self.assertTrue(result['ok'],result)
        proposal={'items':[],'warnings':[]}
        add_items(proposal,result['items'],BOTH)
        items=proposal['items']
        number=self.create(dict(self.request(),machine_codes=[i['machine_code'] for i in items],item_actions={i['machine_code']:i['action_code'] for i in items}))
        stored=service.get_work_order(number)['items']
        self.assertEqual(len({i['machine_id'] for i in stored}),4)
        self.assertTrue(all(i['machine_name']=='دامپتراک' and i['action_code']==BOTH for i in stored))
        with self.assertRaises(ValueError): get_items(['DG1'])
        result=create_worker.execute_request(dict(request,machine_codes=['DG1']),db_path=self.db_path)
        self.assertFalse(result['ok'])
        # Ambiguous IDs must be reviewed, never silently merged by LIMIT 1.
        with test_database(self.db_path) as con:
            con.execute("INSERT INTO machines(canonical_code) VALUES ('719')")
        with self.assertRaisesRegex(ValueError,'مبهم'):
            self.fixture.test_today='1405/07/26'
            service.create_work_order(work_order_type='AIR_FILTER',jalali_date='1405/07/26',shift='صبح',machine_codes=['719'],created_by='bale:'+shared.MANAGER)

    def test_no_baseline_is_manual_addable_without_fabricating_automatic_proposal(self):
        from tools.fleet.air_filter.proposal import evaluate_machine
        for code in ('717','719','720'):
            r=evaluate_machine(dict(code=code,name='دامپتراک',daily=[]),(7,24),(7,25))
            self.assertIsNone(r['action_code'])
            self.assertEqual(r['components']['inner']['state'],'NEEDS_REVIEW')
        self.assertEqual(len(create_worker.execute_request(dict(action='validate_add',bale_id=shared.MANAGER,work_order_type='AIR_FILTER',machine_codes=['717','719','720']),db_path=self.db_path)['items']),3)

    def test_stale_token_cannot_damage_new_attempt_even_error_metadata(self):
        number,_=self.uncertain(); old=self.read('SELECT attempt_id FROM service_work_order_dispatch')[0]['attempt_id']
        self.resolve(number,old,'RESEND_ALLOWED'); token=staff_dispatch.claim_send(number)
        before=self.read('SELECT * FROM service_work_orders')
        with self.assertRaises(ValueError):
            delivery.send_work_order(work_order_no=number,dispatch_token=old,sender=None)
        self.assertEqual(before,self.read('SELECT * FROM service_work_orders'))
        self.assertEqual(self.read('SELECT attempt_id FROM service_work_order_dispatch')[0]['attempt_id'],token)

    def test_receipt_is_traced_to_actor_without_reusing_wrong_dispatch_name(self):
        number=self.create(); self.prepare(number)
        delivery.send_work_order(work_order_no=number,sender=SimpleNamespace(send_document=lambda **kw:None))
        with test_database(self.db_path) as con:
            con.execute("UPDATE service_work_order_dispatch SET staff_name='WRONG HISTORICAL PERSON'")
        messages=[]
        async def send(**kw): messages.append(kw)
        gateway=SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=SimpleNamespace(send_message=send))})
        asyncio.run(staff_flow.receipt(gateway,shared.TECH,shared.TECH,number))
        notice=next(m['text'] for m in messages if m['chat_id']==shared.MANAGER)
        self.assertIn(shared.TECH,notice); self.assertNotIn('WRONG HISTORICAL PERSON',notice)
        order=service.get_work_order(number)
        self.assertIn('STAFF_ACKNOWLEDGED',order['notes']); self.assertIn(shared.TECH,order['notes'])
        self.assertEqual(self.read('SELECT staff_name FROM service_work_order_dispatch')[0]['staff_name'],'WRONG HISTORICAL PERSON')


if __name__=='__main__': unittest.main()
