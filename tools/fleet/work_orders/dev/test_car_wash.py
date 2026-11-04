import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from openpyxl import load_workbook

from tools.fleet.car_wash.test_proposal import make_source
from tools.fleet.work_orders.channels.bale import create_worker
from tools.fleet.work_orders.channels.bale.message_handler import WorkOrderMenuHandler
from tools.fleet.work_orders.core import service, oil_safety
from tools.fleet.work_orders.core.assignment import assign_work_order
from tools.fleet.work_orders.core.approval import approve_work_order
from tools.fleet.work_orders.core.delivery import send_work_order
from tools.fleet.work_orders.dev.test_permissions import PermissionDatabaseTestCase, test_database


class WashCoreTests(PermissionDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.test_today = '1405/01/09'
        self.source = self.db_path.parent / 'source.xlsx'
        make_source(self.source)
        self.original = self.source.read_bytes()
        schema = importlib.import_module('tools.fleet.work_orders.migrations.001_create_work_order_schema_v1')
        with test_database(self.db_path) as con:
            con.execute('CREATE TABLE machines (id INTEGER PRIMARY KEY, canonical_code TEXT)')
            con.executemany('INSERT INTO machines(canonical_code) VALUES (?)', [('HD719',), ('S1',)])
            schema.create_schema(con)
            oil_safety.migrate(con)
        for target, value in [
            ('tools.fleet.work_orders.core.db.DB_PATH', self.db_path),
            ('tools.fleet.work_orders.core.permissions.DB_PATH', self.db_path),
            ('tools.fleet.work_orders.core.service.WORK_ORDER_OUTPUT_ROOT', self.db_path.parent / 'orders'),
            ('tools.fleet.car_wash.source.SOURCE', self.source),
            ('tools.fleet.car_wash.source.completed_cutoff', lambda: (1, 8, 1)),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def propose(self):
        result = create_worker.execute_request({'action': 'propose', 'work_order_type': 'CAR_WASH',
                                               'bale_id': '455740857', 'chat_id': '455740857'}, db_path=self.db_path)
        self.assertTrue(result['ok'], result)
        return result['proposal']

    def request(self, proposal=None, codes=None):
        proposal = proposal or self.propose()
        return {'action': 'create', 'work_order_type': 'CAR_WASH', 'bale_id': '455740857',
                'chat_id': '455740857', 'jalali_date': self.test_today, 'shift': 'روزانه',
                'machine_codes': codes or [i['machine_code'] for i in proposal['items']], 'proposal': proposal}

    def test_worker_creates_one_group_order_and_reuses_duplicate_request(self):
        request = self.request()
        first = create_worker.execute_request(request, db_path=self.db_path)
        second = create_worker.execute_request(request, db_path=self.db_path)
        self.assertTrue(first['ok'], first)
        self.assertEqual(first['order']['work_order_no'], second['order']['work_order_no'])
        order = service.get_work_order(first['order']['work_order_no'])
        self.assertEqual(order['status'], 'FILE_READY')
        self.assertIsNone(order['assigned_staff_id'])
        with test_database(self.db_path) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM service_work_order_dispatch').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM service_work_orders').fetchone()[0], 1)
        book = load_workbook(order['excel_path'])
        sheet = book.active
        self.assertEqual(sheet['F1'].value, 'تاریخ: ۱۴۰۵/۰۱/۰۹')
        self.assertEqual(sheet['B3'].value, 'دامپتراک کوماتسو 7-785')
        self.assertTrue(all(sheet.cell(row, col).value is None for row in range(3, 13) for col in range(4, 8)))
        self.assertEqual(sheet.page_setup.orientation, 'portrait')
        self.assertEqual(str(sheet.page_setup.paperSize), sheet.PAPERSIZE_A4)
        book.close()
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_manual_add_can_use_review_unit_or_device_outside_workbook(self):
        proposal = self.propose()
        proposal['manual_items'] = {'CWNEW1': 'دستگاه آزمایشی مسئول نت'}
        result = create_worker.execute_request(self.request(proposal, ['EX231', 'CWNEW1', 's1']), db_path=self.db_path)
        self.assertTrue(result['ok'], result)
        order = service.get_work_order(result['order']['work_order_no'])
        self.assertEqual(order['items'][1]['machine_name'], 'دستگاه آزمایشی مسئول نت')
        self.assertEqual(order['items'][2]['machine_code'], 's1')
        self.assertIsNone(order['items'][2]['machine_id'])  # Must not link to S1.

    def test_changed_source_empty_selection_and_unknown_automatic_code_are_blocked(self):
        proposal = self.propose()
        for codes in ([], ['UNKNOWN1']):
            request = self.request(proposal)
            request['machine_codes'] = codes
            self.assertFalse(create_worker.execute_request(request, db_path=self.db_path)['ok'])
        proposal['source_sha256'] = 'old'
        result = create_worker.execute_request(self.request(proposal), db_path=self.db_path)
        self.assertEqual(result['error'], 'INVALID_INPUT')
        with test_database(self.db_path) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM service_work_orders').fetchone()[0], 0)

    def test_owner_private_chat_and_permission_are_enforced(self):
        request = self.request()
        request['chat_id'] = '9999'
        self.assertFalse(create_worker.execute_request(request, db_path=self.db_path)['ok'])
        request['bale_id'], request['chat_id'] = '9999', '9999'
        self.assertEqual(create_worker.execute_request(request, db_path=self.db_path)['error'], 'DENIED')

    def test_staff_assignment_approval_and_delivery_are_all_rejected(self):
        result = create_worker.execute_request(self.request(), db_path=self.db_path)
        number = result['order']['work_order_no']
        for call in [lambda: assign_work_order(work_order_no=number, staff_id=1),
                     lambda: approve_work_order(work_order_no=number, approved_by='bale:455740857'),
                     lambda: send_work_order(work_order_no=number, sender=SimpleNamespace())]:
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(service.list_eligible_staff('CAR_WASH'), [])
        self.assertEqual(service.get_work_order(number)['send_attempts'], 0)

    def test_revision_preserves_old_workbook_and_prevents_old_preview(self):
        first = create_worker.execute_request(self.request(), db_path=self.db_path)['order']
        number = first['work_order_no']
        old_bytes = Path(first['file_path']).read_bytes()
        edit = create_worker.execute_request({'action': 'edit', 'bale_id': '455740857', 'work_order_no': number}, db_path=self.db_path)
        self.assertTrue(edit['ok'], edit)
        revised = create_worker.execute_request(self.request(edit['proposal'], ['HD719']), db_path=self.db_path)
        self.assertTrue(revised['ok'], revised)
        self.assertNotEqual(revised['order']['work_order_no'], number)
        self.assertEqual(Path(first['file_path']).read_bytes(), old_bytes)
        self.assertEqual(service.get_work_order(number)['superseded_by'], revised['order']['work_order_no'])


class WashConversationTests(WashCoreTests, unittest.IsolatedAsyncioTestCase):
    async def test_text_entry_manual_add_delete_back_restart_and_self_pdf(self):
        from tools.bale_ui import StateStore
        sent, deliveries = [], []
        async def worker(request):
            return create_worker.execute_request(request, db_path=self.db_path)
        async def send_message(**kwargs):
            sent.append(kwargs)
            return SimpleNamespace(message_id=len(sent))
        async def edit_message_text(**kwargs):
            message = sent[int(kwargs['message_id']) - 1]
            message['text'] = kwargs['text']
            message['reply_markup'] = kwargs.get('reply_markup', {})
        async def document(gateway, chat, order):
            deliveries.append((chat, order))
        bot = SimpleNamespace(send_message=send_message,
                              edit_message_reply_markup=AsyncMock(), delete_message=AsyncMock(),
                              edit_message_text=edit_message_text)
        gateway = SimpleNamespace(adapters={'bale': SimpleNamespace(_bot=bot)})
        store = StateStore(self.db_path.parent / 'wash-ui.json')
        handler = WorkOrderMenuHandler(db_path=self.db_path, worker=worker, document_sender=document, state_store=store)
        sequence = 0
        async def enter(text='', action=None):
            nonlocal sequence
            sequence += 1
            raw = None
            if action:
                index = next(i for i in range(len(sent) - 1, -1, -1)
                             if any(b['callback_data'].endswith(':' + action)
                                    for row in sent[i].get('reply_markup', {}).get('inline_keyboard', []) for b in row))
                callback = next(b['callback_data'] for row in sent[index]['reply_markup']['inline_keyboard']
                                for b in row if b['callback_data'].endswith(':' + action))
                raw = {'bale_inline_callback': True, 'data': callback, 'origin_message_id': str(index + 1)}
            event = SimpleNamespace(text=text, message_id=str(sequence), raw_message=raw,
                                    source=SimpleNamespace(platform='bale', chat_type='dm', user_id='455740857', chat_id='455740857'))
            handler.handle(event, gateway, send=lambda *a: None)
            while handler.tasks or handler.lifecycle.tasks:
                await asyncio.gather(*list(handler.tasks | handler.lifecycle.tasks))
        with patch('tools.fleet.work_orders.channels.bale.message_handler.review_context', return_value=None):
            await enter('کارواش')
            session = handler.pending[('bale', '455740857', '455740857')]
            self.assertEqual(session.stage, 'PROPOSAL')
            await enter(action='wash_review')
            self.assertIn('EX231', sent[-1]['text'])
            await enter(action='wash_back')
            await enter(action='wash_edit')
            await enter('CWNEW1')
            await enter('لودر آزمایشی')
            self.assertNotIn('CWNEW1', [i['machine_code'] for i in session.proposal['items']])
            handler = WorkOrderMenuHandler(db_path=self.db_path, worker=worker, document_sender=document, state_store=store)
            session = handler.pending[('bale', '455740857', '455740857')]
            self.assertEqual(session.stage, 'WASH_CONFIRM')
            await enter(action='wash_apply')
            await enter(action='wash_back')
            await enter(action='remove')
            await enter(action='pick_1')  # MZ1, the second initial proposal.
            await enter(action='select_confirm')
            self.assertNotIn('MZ1', [i['machine_code'] for i in session.proposal['items']])
            await enter(action='wash_create')
            self.assertEqual(session.stage, 'WASH_RESULT')
            self.assertEqual(len(deliveries), 1)
            self.assertEqual(deliveries[0][0], '455740857')
            self.assertEqual(deliveries[0][1]['work_order_type'], 'CAR_WASH')
            self.assertFalse(handler.reviews)
            self.assertNotIn('staff_', str(sent[-1]))
