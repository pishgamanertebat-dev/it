import importlib
from tools.fleet.work_orders.dev.test_bale_work_order_create import WorkOrderCreateTests
from tools.fleet.work_orders.dev.test_permissions import test_database
from tools.fleet.work_orders.core import staff_dispatch as core
from tools.fleet.work_orders.core.review import confirm_document_review
from tools.fleet.work_orders.core.delivery import send_work_order


class StaffDispatchTests(WorkOrderCreateTests):
    def setUp(self):
        super().setUp()
        with test_database(self.db_path) as con:
            importlib.import_module('tools.fleet.work_orders.migrations.003_staff_dispatch').migrate(con)
            con.execute("INSERT INTO service_work_order_users(bale_id,role,active) VALUES ('1006','MAINTENANCE_MANAGER',1)")
        import unittest.mock
        patcher = unittest.mock.patch('tools.fleet.work_orders.core.permissions.DB_PATH', self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        archive_patcher = unittest.mock.patch('tools.fleet.work_orders.core.delivery.archive_delivered_order')
        archive_patcher.start()
        self.addCleanup(archive_patcher.stop)

    def test_dispatch_requires_owner_current_review_and_active_staff(self):
        order = self.create()
        number = order['work_order_no']
        staff = core.staff_options()[0]
        with self.assertRaises(ValueError):
            core.prepare_dispatch(number, '455740857', '455740857', staff['id'])
        self.review_pdf(number)
        with self.assertRaises(ValueError):
            core.prepare_dispatch(number, '1006', '1006', staff['id'])
        with test_database(self.db_path) as con:
            con.execute('UPDATE service_staff SET active=0')
        with self.assertRaises(ValueError):
            core.prepare_dispatch(number, '455740857', '455740857', staff['id'])

    def test_send_failure_retry_and_receipt_authorization(self):
        order = self.create()
        number = order['work_order_no']
        self.review_pdf(number)
        core.prepare_dispatch(number, '455740857', '455740857', core.staff_options()[0]['id'])
        token=core.claim_send(number)
        self.assertTrue(token)
        self.assertFalse(core.claim_send(number))
        class Fail:
            def send_document(self, **kwargs):
                raise RuntimeError('TEST FAILURE')
        with self.assertRaises(RuntimeError):
            send_work_order(work_order_no=number, sender=Fail(),dispatch_token=token)
        core.finish_send(number, 'FAILED')
        self.assertEqual(core.recipient_orders('85539397'), [])
        self.assertFalse(core.claim_send(number))
        from tools.fleet.work_orders.channels.bale.create_worker import execute_request
        decision=execute_request(dict(action='delivery_resolve',bale_id='455740857',chat_id='455740857',
            work_order_no=number,attempt_id=token,decision='RESEND_ALLOWED',reason='Verified test-only failure'),db_path=self.db_path)
        self.assertTrue(decision['ok'],decision)
        next_token=core.claim_send(number)
        self.assertTrue(next_token)
        class Success:
            def send_document(self, **kwargs):
                return {'ok': True}
        send_work_order(work_order_no=number, sender=Success(),dispatch_token=next_token)
        core.finish_send(number, 'SENT')
        with self.assertRaises(ValueError):
            core.acknowledge(number, '9999')
        core.acknowledge(number, '85539397')
        first = core.recipient_orders('85539397')[0]['acknowledged_at']
        core.acknowledge(number, '85539397')
        self.assertEqual(core.recipient_orders('85539397')[0]['acknowledged_at'], first)
        self.assertFalse(core.claim_send(number))
