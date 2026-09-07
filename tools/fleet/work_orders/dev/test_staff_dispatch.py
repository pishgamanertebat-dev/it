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

    def test_dispatch_requires_owner_current_review_and_active_staff(self):
        order = self.create()
        number = order['work_order_no']
        staff = core.staff_options()[0]
        with self.assertRaises(ValueError):
            core.prepare_dispatch(number, '455740857', '455740857', staff['id'])
        confirm_document_review(number, '455740857')
        with self.assertRaises(ValueError):
            core.prepare_dispatch(number, '1006', '1006', staff['id'])
        with test_database(self.db_path) as con:
            con.execute('UPDATE service_staff SET active=0')
        with self.assertRaises(ValueError):
            core.prepare_dispatch(number, '455740857', '455740857', staff['id'])

    def test_send_failure_retry_and_receipt_authorization(self):
        order = self.create()
        number = order['work_order_no']
        confirm_document_review(number, '455740857')
        core.prepare_dispatch(number, '455740857', '455740857', core.staff_options()[0]['id'])
        self.assertTrue(core.claim_send(number))
        self.assertFalse(core.claim_send(number))
        class Fail:
            def send_document(self, **kwargs):
                raise RuntimeError('TEST FAILURE')
        with self.assertRaises(RuntimeError):
            send_work_order(work_order_no=number, sender=Fail())
        core.finish_send(number, 'FAILED')
        self.assertEqual(core.recipient_orders('85539397'), [])
        self.assertTrue(core.claim_send(number))
        class Success:
            def send_document(self, **kwargs):
                return {'ok': True}
        send_work_order(work_order_no=number, sender=Success())
        core.finish_send(number, 'SENT')
        with self.assertRaises(ValueError):
            core.acknowledge(number, '9999')
        core.acknowledge(number, '85539397')
        first = core.recipient_orders('85539397')[0]['acknowledged_at']
        core.acknowledge(number, '85539397')
        self.assertEqual(core.recipient_orders('85539397')[0]['acknowledged_at'], first)
        self.assertFalse(core.claim_send(number))
