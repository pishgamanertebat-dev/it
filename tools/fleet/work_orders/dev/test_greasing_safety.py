"""Greasing exercises the same attempt, lease and revision contracts as oil.

All stores, documents and transports are isolated. No operational deliveries.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.fleet.work_orders.dev import test_oil_p1_safety as shared
from tools.fleet.work_orders.dev.test_permissions import test_database
from tools.fleet.work_orders.core import delivery, service, staff_dispatch, review
from tools.fleet.work_orders.channels.bale import create_worker, staff_flow
from tools.fleet.work_orders.channels.bale.proposal_form import creation_metadata


class GreasingSafetyTests(unittest.TestCase):
    setUp = shared.OilSafetyTests.setUp
    prepare = shared.OilSafetyTests.prepare
    read = shared.OilSafetyTests.read
    resolve = shared.OilSafetyTests.resolve
    age = shared.OilSafetyTests.age
    crash = shared.OilSafetyTests.crash
    uncertain = shared.OilSafetyTests.uncertain

    def request(self):
        return dict(action='create',bale_id=shared.MANAGER,work_order_type='GREASING',
                    jalali_date='1405/07/25',shift='روزانه',machine_codes=['EX1252'])

    def create(self, request=None):
        self.fixture.test_today = (request or self.request())['jalali_date']
        result=create_worker.execute_request(request or self.request(),db_path=self.db_path)
        self.assertTrue(result['ok'],result)
        return result['order']['work_order_no']

    def revision_request(self, number):
        result=create_worker.execute_request(dict(action='edit',bale_id=shared.MANAGER,
            work_order_no=number),db_path=self.db_path)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['proposal']['revision_of'],number)
        return dict(self.request(),proposal=creation_metadata(result['proposal']))

    test_revision_history_and_old_callbacks = shared.OilSafetyTests.test_revision_preserves_history_and_blocks_old_review_approval_and_dispatch
    test_timeout_blocks_ordinary_and_direct_retry = shared.OilSafetyTests.test_timeout_after_acceptance_never_allows_ordinary_or_direct_retry
    test_bale_retry_requires_explicit_decision = shared.OilSafetyTests.test_bale_timeout_and_repeated_dispatch_only_resend_after_explicit_permission
    test_version_fence_before_transport = shared.OilSafetyTests.test_final_pretransfer_version_check_prevents_upload
    test_invalid_tokens = shared.OilSafetyTests.test_invalid_tokens_never_claim_transfer_complete_or_change_error_metadata
    test_legacy_sending_recovers_conservatively = shared.OilSafetyTests.test_legacy_sending_without_attempt_is_recovered_conservatively
    test_received_decision_audit_and_receipt = shared.OilSafetyTests.test_net_received_decision_is_audited_idempotent_and_allows_receipt
    test_late_completion_and_tokens = shared.OilSafetyTests.test_resend_decision_late_completion_and_old_callback_are_fenced
    test_crash_lease_and_restart = shared.OilSafetyTests.test_crash_before_and_after_transfer_restart_and_lease
    test_pretransfer_failure_can_retry = shared.OilSafetyTests.test_definite_pretransfer_failure_is_retryable
    test_revision_cannot_race_claim = shared.OilSafetyTests.test_revision_cannot_race_claim_and_approval_cannot_bypass_version
    test_unauthorized_resolution_denied = shared.OilSafetyTests.test_unauthorized_or_wrong_chat_cannot_resolve
    test_actual_menu_recovery_and_repeat = shared.OilSafetyTests.test_actual_manager_menu_recovery_after_restart_and_repeated_message

    def test_concurrent_duplicate_request_reuses_order(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            numbers=list(pool.map(lambda _:self.create(),range(2)))
        self.assertEqual(numbers[0],numbers[1])
        self.assertEqual(len(self.read('SELECT * FROM service_work_orders')),1)

    def test_failed_revision_preserves_previous(self):
        old=self.create()
        with patch('tools.fleet.work_orders.types.greasing.builder.build_document',side_effect=ValueError('test failure')):
            result=create_worker.execute_request(self.revision_request(old),db_path=self.db_path)
        self.assertFalse(result['ok'])
        self.assertIsNone(service.get_work_order(old)['superseded_by'])
        self.assertEqual(len(self.read('SELECT * FROM service_work_orders')),1)

    def test_sent_revision_requires_intent_and_preserves_delivery(self):
        old=self.create();self.prepare(old)
        delivery.send_work_order(work_order_no=old,sender=SimpleNamespace(send_document=lambda **kw:{'ok':True}))
        before=service.get_work_order(old)
        with self.assertRaises(ValueError):
            service.create_work_order(work_order_type='GREASING',jalali_date=before['jalali_date'],
                shift='روزانه',machine_codes=['EX1252'],created_by=before['created_by'],revision_of=old)
        req=dict(action='reissue',bale_id=shared.MANAGER,work_order_no=old,
                 reason='Test manager intentional correction',operation_id='gr-reissue-test')
        # The source resolver is isolated to the original persisted machine identities.
        items=before['items']
        with patch('tools.fleet.work_orders.types.greasing.builder.get_items',return_value=items):
            result=create_worker.execute_request(req,db_path=self.db_path)
            again=create_worker.execute_request(req,db_path=self.db_path)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['orders'][0]['work_order_no'],again['orders'][0]['work_order_no'])
        self.assertEqual(again['orders'][0]['creation_outcome'],'reused')
        after=service.get_work_order(old)
        self.assertEqual(after['status'],'SENT')
        self.assertEqual(after['sent_at'],before['sent_at'])
        self.assertEqual(after['send_attempts'],before['send_attempts'])
        new=service.get_work_order(result['orders'][0]['work_order_no'])
        from openpyxl import load_workbook
        wb=load_workbook(new['excel_path'])
        try:self.assertIn('اصلاحیه حکم: '+old,wb.active['A1'].value)
        finally:wb.close()

    def test_uncertain_parent_blocks_replacement(self):
        old,_=self.uncertain()
        before=service.get_work_order(old)
        with self.assertRaisesRegex(ValueError,'تعیین تکلیف'):
            service.create_work_order(work_order_type='GREASING',jalali_date=before['jalali_date'],
                shift='روزانه',machine_codes=['EX1252'],created_by=before['created_by'],revision_of=old)
        self.assertEqual(len(self.read('SELECT * FROM service_work_orders')),1)

    def test_pdf_change_delete_and_regeneration_require_fresh_review(self):
        number=self.create();self.prepare(number)
        path=Path(service.get_work_order(number)['pdf_path']); original=path.read_bytes()
        sender=SimpleNamespace(send_document=lambda **kw:self.fail('Changed PDF sent'))
        for changed in (b'%PDF-1.4\nREPLACED',None,b'%PDF-1.7\nREGENERATED'):
            if changed is None:path.unlink()
            else:path.write_bytes(changed)
            with self.assertRaises(review.DocumentReviewRequired):
                delivery.send_work_order(work_order_no=number,sender=sender)
        self.assertEqual(self.read('SELECT state FROM service_work_order_dispatch')[0]['state'],'FAILED')
        delivery.store_manager_pdf(number,path)
        review.confirm_document_review(number,shared.MANAGER)
        captured=[]
        delivery.send_work_order(work_order_no=number,sender=SimpleNamespace(send_document=lambda **kw:captured.append(kw['document_bytes'])))
        self.assertEqual(captured,[path.read_bytes()])
        self.assertNotEqual(captured[0],original)

    def test_preview_cannot_be_swapped_before_confirmation(self):
        number=self.create();order=service.get_work_order(number)
        path=Path(order['excel_path']).with_suffix('.pdf');path.write_bytes(b'%PDF-original')
        delivery.store_manager_pdf(number,path)
        path.write_bytes(b'%PDF-replaced')
        with self.assertRaises(review.DocumentReviewRequired):
            review.confirm_document_review(number,shared.MANAGER)

    def test_actual_bale_upload_uses_approved_bytes_even_if_path_changes(self):
        number=self.create();self.prepare(number)
        path=Path(service.get_work_order(number)['pdf_path']);approved=path.read_bytes();received=[]
        async def upload(**kwargs):
            path.write_bytes(b'%PDF-replaced during upload')
            received.append(kwargs['document'].read())
            return SimpleNamespace(message_id=5)
        gateway=SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=SimpleNamespace(send_document=upload))})
        asyncio.run(staff_flow.dispatch(gateway,number,shared.MANAGER,shared.MANAGER,self.staff,self.roster))
        self.assertEqual(received,[approved])

    def test_excel_change_also_blocks_delivery(self):
        number=self.create();self.prepare(number)
        path=Path(service.get_work_order(number)['excel_path']);path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaises(review.DocumentReviewRequired):
            delivery.send_work_order(work_order_no=number,sender=None)

    def test_missing_legacy_pdf_is_actionable_without_regeneration_or_db_change(self):
        number=self.create();self.prepare(number)
        delivery.send_work_order(work_order_no=number,sender=SimpleNamespace(send_document=lambda **kw:{'ok':True}))
        with test_database(self.db_path) as con:
            con.execute('UPDATE service_work_orders SET pdf_path=NULL WHERE work_order_no=?',(number,))
        before=self.read('SELECT * FROM service_work_orders');messages=[]
        async def message(**kw):messages.append(kw['text'])
        gateway=SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=SimpleNamespace(send_message=message))})
        asyncio.run(staff_flow.show_order(gateway,shared.TECH,shared.TECH,number))
        self.assertIn('مسئول NET',messages[0])
        self.assertEqual(before,self.read('SELECT * FROM service_work_orders'))
