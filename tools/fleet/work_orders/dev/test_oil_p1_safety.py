"""Fault injection in temporary databases. All document transport is local."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.bale_ui import StateStore
from tools.fleet.work_orders.core import db, delivery, oil_safety, service, staff_dispatch
from tools.fleet.work_orders.core.approval import approve_work_order
from tools.fleet.work_orders.core.review import confirm_document_review
from tools.fleet.work_orders.channels.bale import create_worker, staff_flow
from tools.fleet.work_orders.channels.bale.message_handler import WorkOrderMenuHandler
from tools.fleet.work_orders.channels.bale.proposal_form import creation_metadata, edit_oil_interval
from tools.fleet.work_orders.dev.test_oil_service_edit import OilServiceEditTests
from tools.fleet.work_orders.dev.test_permissions import test_database

MANAGER = '455740857'
TECH = '999000111'


class OilSafetyTests(unittest.TestCase):
    def setUp(self):
        p = patch('tools.fleet.work_orders.dev.test_permissions.PROJECT_ROOT',
                  Path('E:/KomatsoAI/runtime/oil_p1_fix_20261017/fixtures'))
        p.start(); self.addCleanup(p.stop)
        self.fixture = OilServiceEditTests('test_repeated_override_retains_original_evidence_for_all_cycles')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db_path = self.fixture.db_path
        for target in ('tools.fleet.work_orders.core.permissions.DB_PATH',):
            p = patch(target,self.db_path); p.start(); self.addCleanup(p.stop)
        p = patch.dict('sys.modules', {'telegram':SimpleNamespace(InlineKeyboardMarkup=SimpleNamespace(de_json=lambda markup,bot:markup))})
        p.start(); self.addCleanup(p.stop)
        p = patch.object(delivery,'archive_delivered_order'); self.archive = p.start(); self.addCleanup(p.stop)
        with test_database(self.db_path) as con:
            oil_safety.migrate(con)
            self.staff = con.execute("INSERT INTO service_staff(display_name,bale_id,service_role,active) VALUES ('TEST ONLY',?,'OIL_CHANGE',1)",(TECH,)).lastrowid
            self.roster = con.execute("INSERT INTO service_staff_roster(display_name,staff_id) VALUES ('TEST ONLY',?)",(self.staff,)).lastrowid

    def create(self, request=None):
        result = create_worker.execute_request(request or self.fixture.request(),db_path=self.db_path)
        self.assertTrue(result['ok'],result)
        return result['orders'][0]['work_order_no']

    def prepare(self, number):
        path = self.db_path.parent / (number+'.pdf')
        path.write_bytes(b'%PDF-1.4\nTEST DOCUMENT\n')
        delivery.store_manager_pdf(number,path)
        confirm_document_review(number,MANAGER)
        staff_dispatch.prepare_dispatch(number,MANAGER,MANAGER,self.staff,self.roster)

    def read(self, sql, args=()):
        con = db.connect_db()
        try:
            return [dict(r) for r in con.execute(sql,args)]
        finally:
            con.close()

    def resolve(self, number, attempt, decision):
        result = create_worker.execute_request(dict(action='delivery_resolve',bale_id=MANAGER,chat_id=MANAGER,
            work_order_no=number,attempt_id=attempt,decision=decision,reason='Verified with TEST recipient'),db_path=self.db_path)
        self.assertTrue(result['ok'],result)
        return result

    def age(self, number):
        with test_database(self.db_path) as con:
            con.execute('UPDATE service_work_order_dispatch SET claimed_at=?,transfer_started_at=CASE WHEN transfer_started_at IS NULL THEN NULL ELSE ? END WHERE work_order_no=?',(time.time()-601,time.time()-601,number))

    def crash(self, number, transfer=False, sweep=False):
        script = f"""import os
from pathlib import Path
from tools.fleet.work_orders.core import db,staff_dispatch,oil_safety
db.DB_PATH=Path({str(self.db_path)!r})
if {sweep!r}:
    oil_safety.sweep()
else:
    token=staff_dispatch.claim_send({number!r})
    assert token
    if {transfer!r}:
        con=db.connect_db()
        with con:
            con.execute('BEGIN IMMEDIATE')
            oil_safety.start_transfer(con,{number!r},token,'TEST.pdf')
        con.close()
os._exit(0)
"""
        subprocess.run([sys.executable,'-c',script],check=True,timeout=30)

    def test_duplicate_and_concurrent_requests_reuse_one_operation(self):
        req = self.fixture.request()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:self.create(req),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(len(self.read('SELECT * FROM service_work_orders')),1)

    def revision_request(self, number):
        opened = create_worker.execute_request(dict(action='edit',bale_id=MANAGER,work_order_no=number),db_path=self.db_path)
        self.assertTrue(opened['ok'],opened)
        proposal = opened['proposal']
        edit_oil_interval(proposal,'EX1252',1600)
        return dict(action='create',bale_id=MANAGER,work_order_type='OIL_CHANGE',jalali_date=proposal['plan_date'],
            shift='روزانه',machine_codes=['EX1252'],item_actions={'EX1252':proposal['items'][0]['action_code']},proposal=creation_metadata(proposal))

    def test_revision_preserves_history_and_blocks_old_review_approval_and_dispatch(self):
        old = self.create()
        request = self.revision_request(old)
        self.prepare(old)
        new = self.create(request)
        self.assertEqual(self.create(request),new)
        self.assertEqual(service.get_work_order(old)['superseded_by'],new)
        self.assertEqual(service.get_work_order(new)['revision_of'],old)
        for operation in (lambda:confirm_document_review(old,MANAGER),
                          lambda:approve_work_order(work_order_no=old,approved_by='bale:'+MANAGER),
                          lambda:staff_dispatch.prepare_dispatch(old,MANAGER,MANAGER,self.staff,self.roster),
                          lambda:delivery.send_work_order(work_order_no=old,sender=None)):
            with self.assertRaisesRegex(ValueError,'منسوخ'):
                operation()
        self.assertTrue(Path(service.get_work_order(old)['excel_path']).is_file())

    def test_failed_revision_does_not_obsolete_previous(self):
        old = self.create()
        req = self.revision_request(old)
        with patch('tools.fleet.work_orders.types.oil_change.builder.build_document',side_effect=ValueError('fault')):
            self.assertFalse(create_worker.execute_request(req,db_path=self.db_path)['ok'])
        self.assertIsNone(service.get_work_order(old)['superseded_by'])
        self.assertEqual(len(self.read('SELECT * FROM service_work_orders')),1)

    def test_changed_input_requires_revision_and_legacy_duplicates_are_not_decided(self):
        old = self.create()
        req = self.fixture.request()
        req['item_actions']['EX1252'] = 'OIL_CHANGE_PC1250-8_1600'
        req['proposal']['service_overrides']['EX1252'] = 1600
        self.assertFalse(create_worker.execute_request(req,db_path=self.db_path)['ok'])
        with test_database(self.db_path) as con:
            con.execute('UPDATE service_work_orders SET operation_key=NULL')
            con.execute("INSERT INTO service_work_orders(work_order_no,work_order_type,jalali_date,status,created_by) SELECT 'LEGACY-DUP',work_order_type,jalali_date,status,created_by FROM service_work_orders")
            con.execute("INSERT INTO service_work_order_items(work_order_id,item_no,machine_code,action_code,action_text) SELECT (SELECT id FROM service_work_orders WHERE work_order_no='LEGACY-DUP'),1,machine_code,action_code,action_text FROM service_work_order_items")
        before = self.read('SELECT * FROM service_work_orders')
        self.assertFalse(create_worker.execute_request(self.fixture.request(),db_path=self.db_path)['ok'])
        self.assertEqual(self.read('SELECT * FROM service_work_orders'),before)

    def test_intentional_reissue_requires_reason_and_is_idempotent_even_after_sent(self):
        old = self.create(); self.prepare(old)
        delivery.send_work_order(work_order_no=old,sender=SimpleNamespace(send_document=lambda **kw:{'ok':True}))
        request = dict(action='reissue',bale_id=MANAGER,work_order_no=old,reason='',operation_id='explicit-test-request')
        self.assertFalse(create_worker.execute_request(request,db_path=self.db_path)['ok'])
        request['reason']='Explicit replacement authorized by test manager'
        result = create_worker.execute_request(request,db_path=self.db_path)
        self.assertTrue(result['ok'],result)
        again = create_worker.execute_request(request,db_path=self.db_path)
        self.assertEqual(result['orders'][0]['work_order_no'],again['orders'][0]['work_order_no'])
        self.assertEqual(service.get_work_order(old)['status'],'SENT')
        self.assertIsNotNone(service.get_work_order(old)['superseded_by'])

    def uncertain(self):
        number = self.create(); self.prepare(number)
        accepted = []
        def upload(**kw):
            accepted.append(kw)
            raise TimeoutError('accepted then lost response')
        with self.assertRaises(TimeoutError):
            delivery.send_work_order(work_order_no=number,sender=SimpleNamespace(send_document=upload))
        return number,accepted

    def test_timeout_after_acceptance_never_allows_ordinary_or_direct_retry(self):
        number,accepted = self.uncertain()
        self.assertEqual(self.read('SELECT state FROM service_work_order_dispatch')[0]['state'],'UNCERTAIN')
        self.assertFalse(staff_dispatch.claim_send(number))
        with self.assertRaises(ValueError):
            delivery.send_work_order(work_order_no=number,sender=SimpleNamespace(send_document=lambda **kw:accepted.append(kw)))
        self.assertEqual(len(accepted),1)
        row = self.read('SELECT * FROM service_work_order_delivery_attempts')[0]
        self.assertIsNotNone(row['claimed_at']);self.assertIsNotNone(row['transfer_started_at']);self.assertIsNotNone(row['finished_at'])
        self.assertEqual(service.get_work_order(number)['send_attempts'],1)

    def test_bale_timeout_and_repeated_dispatch_only_resend_after_explicit_permission(self):
        number = self.create(); self.prepare(number)
        accepted = []
        async def upload(**kwargs):
            accepted.append(kwargs['document'].read())
            if len(accepted) == 1:
                raise TimeoutError('accepted then lost response')
            return SimpleNamespace(message_id=77)
        gateway = SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=SimpleNamespace(send_document=upload))})
        async def run():
            first = await staff_flow.dispatch(gateway,number,MANAGER,MANAGER,self.staff,self.roster)
            self.assertIn('بررسی ارسال',first)
            await staff_flow.dispatch(gateway,number,MANAGER,MANAGER,self.staff,self.roster)
            self.assertEqual(len(accepted),1)
            attempt = self.read('SELECT id FROM service_work_order_delivery_attempts')[0]['id']
            self.resolve(number,attempt,'RESEND_ALLOWED')
            await staff_flow.dispatch(gateway,number,MANAGER,MANAGER,self.staff,self.roster)
            self.assertEqual(len(accepted),2)
            self.assertEqual(service.get_work_order(number)['status'],'SENT')
        with patch.object(staff_flow.logger,'exception'):
            asyncio.run(run())

    def test_final_pretransfer_version_check_prevents_upload(self):
        number = self.create(); self.prepare(number)
        original = delivery.reviewed_staff_pdf
        def changed(*args):
            path = original(*args)
            with test_database(self.db_path) as con:
                con.execute("UPDATE service_work_orders SET superseded_by='NEWER' WHERE work_order_no=?",(number,))
            return path
        sent=[]
        with patch.object(delivery,'reviewed_staff_pdf',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'منسوخ'):
                delivery.send_work_order(work_order_no=number,sender=SimpleNamespace(send_document=lambda **kw:sent.append(kw)))
        self.assertEqual(sent,[])

    def test_legacy_sending_without_attempt_is_recovered_conservatively(self):
        number = self.create(); self.prepare(number)
        with test_database(self.db_path) as con:
            con.execute("UPDATE service_work_order_dispatch SET state='SENDING' WHERE work_order_no=?",(number,))
            con.execute("UPDATE service_work_orders SET updated_at='2000-01-01 00:00:00' WHERE work_order_no=?",(number,))
        self.assertEqual(oil_safety.sweep(),1)
        self.assertEqual(oil_safety.sweep(),0)
        row=self.read('SELECT * FROM service_work_order_dispatch')[0]
        self.assertEqual(row['state'],'UNCERTAIN')
        self.assertIsNotNone(row['attempt_id'])
        self.assertFalse(staff_dispatch.claim_send(number))

    def test_net_received_decision_is_audited_idempotent_and_allows_receipt(self):
        number,_ = self.uncertain()
        attempt = self.read('SELECT id FROM service_work_order_delivery_attempts')[0]['id']
        self.resolve(number,attempt,'RECEIVED')
        self.resolve(number,attempt,'RECEIVED')
        self.assertFalse(staff_dispatch.claim_send(number))
        staff_dispatch.acknowledge(number,TECH)
        self.assertIsNotNone(service.get_work_order(number)['acknowledged_at'])
        self.assertIn('DELIVERY_DECISION',service.get_work_order(number)['notes'])

    def test_resend_decision_late_completion_and_old_callback_are_fenced(self):
        number,_ = self.uncertain()
        attempt = self.read('SELECT id FROM service_work_order_delivery_attempts')[0]['id']
        self.resolve(number,attempt,'RESEND_ALLOWED')
        new = staff_dispatch.claim_send(number)
        self.assertNotEqual(new,attempt)
        self.resolve(number,attempt,'RESEND_ALLOWED')
        con = db.connect_db()
        with con:
            self.assertFalse(oil_safety.complete(con,number,attempt))
        con.close()
        self.assertEqual(self.read('SELECT attempt_id FROM service_work_order_dispatch')[0]['attempt_id'],new)
        sent=[]
        delivery.send_work_order(work_order_no=number,dispatch_token=new,sender=SimpleNamespace(send_document=lambda **kw:sent.append(kw)))
        self.assertEqual(len(sent),1)
        self.assertFalse(staff_dispatch.claim_send(number))

    def test_crash_before_and_after_transfer_restart_and_lease(self):
        number = self.create()
        for transfer in (False,True):
            with self.subTest(transfer=transfer):
                self.prepare(number)
                self.crash(number,transfer)
                oil_safety.sweep()
                self.assertEqual(self.read('SELECT state FROM service_work_order_dispatch WHERE work_order_no=?',(number,))[0]['state'],'SENDING')
                self.assertFalse(staff_dispatch.claim_send(number))
                self.age(number)
                self.crash(number,sweep=True)
                row = self.read('SELECT * FROM service_work_order_dispatch WHERE work_order_no=?',(number,))[0]
                self.assertEqual(row['state'],'UNCERTAIN')
                self.assertEqual(row['transfer_started_at'] is not None,transfer)
                self.resolve(number,row['attempt_id'],'RESEND_ALLOWED')
                delivery.send_work_order(work_order_no=number,sender=SimpleNamespace(send_document=lambda **kw:{'ok':True}))
                # Each loop uses a separate authorized replacement operation.
                if not transfer:
                    result=create_worker.execute_request(dict(action='reissue',bale_id=MANAGER,work_order_no=number,reason='test next crash',operation_id='second-crash'),db_path=self.db_path)
                    self.assertTrue(result['ok'],result)
                    number = result['orders'][0]['work_order_no']

    def test_definite_pretransfer_failure_is_retryable(self):
        number = self.create(); self.prepare(number)
        with patch.object(delivery,'reviewed_staff_pdf',side_effect=ValueError('before transport')):
            with self.assertRaises(ValueError):
                delivery.send_work_order(work_order_no=number,sender=None)
        self.assertEqual(self.read('SELECT state FROM service_work_order_dispatch')[0]['state'],'FAILED')
        self.assertTrue(staff_dispatch.claim_send(number))

    def test_revision_cannot_race_claim_and_approval_cannot_bypass_version(self):
        old = self.create(); req = self.revision_request(old); self.prepare(old)
        self.assertTrue(staff_dispatch.claim_send(old))
        self.assertFalse(create_worker.execute_request(req,db_path=self.db_path)['ok'])
        self.assertIsNone(service.get_work_order(old)['superseded_by'])

    def test_unauthorized_or_wrong_chat_cannot_resolve(self):
        number,_ = self.uncertain()
        attempt = self.read('SELECT id FROM service_work_order_delivery_attempts')[0]['id']
        for actor,chat in (('1003','1003'),(MANAGER,TECH),('999000222','999000222')):
            result=create_worker.execute_request(dict(action='delivery_resolve',bale_id=actor,chat_id=chat,
                work_order_no=number,attempt_id=attempt,decision='RESEND_ALLOWED',reason='bad actor'),db_path=self.db_path)
            self.assertFalse(result['ok'],result)
        self.assertEqual(self.read('SELECT state FROM service_work_order_dispatch')[0]['state'],'UNCERTAIN')

    def test_actual_manager_menu_recovery_after_restart_and_repeated_message(self):
        number,_ = self.uncertain()
        async def run():
            messages=[]
            bot=SimpleNamespace(send_message=lambda **kw:None)
            async def sender(**kw):
                messages.append(kw);return SimpleNamespace(message_id=len(messages))
            bot.send_message=sender
            gateway=SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=bot)})
            async def worker(request):
                return create_worker.execute_request(request,db_path=self.db_path)
            handler=WorkOrderMenuHandler(db_path=self.db_path,worker=worker,state_store=StateStore(self.db_path.parent/'ui.json'))
            async def send(text,mid):
                event=SimpleNamespace(text=text,message_id=mid,raw_message=None,source=SimpleNamespace(platform='bale',chat_type='dm',user_id=MANAGER,chat_id=MANAGER))
                result=handler.handle(event,gateway,send=lambda *a:None)
                while handler.tasks or handler.lifecycle.tasks:
                    await asyncio.gather(*(handler.tasks|handler.lifecycle.tasks))
                return result
            await send('بررسی ارسال','1')
            self.assertTrue(any(number in m['text'] and 'رسیده است' in m['text'] for m in messages))
            attempt=self.read('SELECT id FROM service_work_order_delivery_attempts')[0]['id']
            command=f'ارسال مجدد مجاز است {number} {attempt} verified in isolated test'
            await send(command,'2')
            self.assertEqual((await send(command,'2'))['reason'],'work-order-duplicate-message')
            await send('ادامه ارسال '+number,'3')
            self.assertEqual(handler.pending[('bale',MANAGER,MANAGER)].stage,'STAFF')
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
