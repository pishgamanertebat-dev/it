import asyncio
import importlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from openpyxl import load_workbook

from tools.fleet.car_wash.test_proposal import make_source
from tools.fleet.work_orders.channels.bale import create_worker
from tools.fleet.work_orders.channels.bale.message_handler import WorkOrderMenuHandler
from tools.fleet.work_orders.core import service, oil_safety, staff_dispatch
from tools.fleet.work_orders.core.assignment import assign_work_order
from tools.fleet.work_orders.core.delivery import send_work_order, store_manager_pdf
from tools.fleet.work_orders.core.review import confirm_document_review, DocumentReviewRequired
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
            self.wash_staff_id = con.execute("""INSERT INTO service_staff(display_name,bale_id,service_role)
                VALUES ('مهدی کارگری','1028780913','CAR_WASH')""").lastrowid
            self.other_staff_id = con.execute("""INSERT INTO service_staff(display_name,bale_id,service_role)
                VALUES ('سرویسکار دیگر','9999','GENERAL')""").lastrowid
            con.execute("INSERT INTO service_staff_roster(display_name,staff_id) VALUES ('سرویسکار دیگر',?)", (self.other_staff_id,))
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

    def review_pdf(self, number, *, confirm=True):
        data = b'%PDF-1.4\nCAR WASH EXACT REVIEWED BYTES\n%%EOF\n'
        path = Path(service.get_work_order(number)['excel_path']).with_suffix('.pdf')
        path.write_bytes(data)
        store_manager_pdf(number, path, pdf_bytes=data)
        if confirm:
            confirm_document_review(number, '455740857')
        return data

    def test_only_wash_recipient_is_eligible_and_cannot_enter_general_menu(self):
        menu, options = staff_dispatch.staff_menu('CAR_WASH')
        self.assertIn('مسئول کارواش', menu)
        self.assertEqual([s['bale_id'] for s in options], ['1028780913'])
        self.assertEqual(service.list_eligible_staff('CAR_WASH'), options)
        for kind in ('AIR_FILTER', 'GREASING', 'OIL_CHANGE'):
            self.assertEqual([s['bale_id'] for s in staff_dispatch.staff_menu(kind)[1]], ['9999'])
        self.assertTrue(staff_dispatch.is_recipient('1028780913'))

    def test_wrong_recipient_and_unreviewed_pdf_are_rejected(self):
        result = create_worker.execute_request(self.request(), db_path=self.db_path)
        number = result['order']['work_order_no']
        with self.assertRaises(ValueError):
            assign_work_order(work_order_no=number, staff_id=self.other_staff_id)
        with self.assertRaises(DocumentReviewRequired):
            staff_dispatch.prepare_dispatch(number, '455740857', '455740857', self.wash_staff_id)
        self.assertEqual(service.get_work_order(number)['send_attempts'], 0)

    def test_dispatch_sends_exact_reviewed_pdf_once_and_only_recipient_can_acknowledge(self):
        number = create_worker.execute_request(self.request(), db_path=self.db_path)['order']['work_order_no']
        data = self.review_pdf(number)
        with self.assertRaises(ValueError):
            staff_dispatch.prepare_dispatch(number, '455740857', '455740857', self.other_staff_id, 1)
        staff_dispatch.prepare_dispatch(number, '455740857', '455740857', self.wash_staff_id)
        uploaded = []
        sender = SimpleNamespace(send_document=lambda **kw: uploaded.append(kw) or {'message_id': 123})
        with patch('tools.fleet.work_orders.core.delivery.export_staff_pdf', side_effect=AssertionError('Must reuse reviewed PDF')):
            self.assertEqual(send_work_order(work_order_no=number, sender=sender, actor='455740857')['status'], 'SENT')
            self.assertEqual(send_work_order(work_order_no=number, sender=sender, actor='455740857')['status'], 'ALREADY_SENT')
        self.assertEqual(len(uploaded), 1)
        self.assertEqual(uploaded[0]['chat_id'], '1028780913')
        self.assertEqual(uploaded[0]['document_bytes'], data)
        self.assertEqual(service.get_work_order(number)['send_attempts'], 1)
        with self.assertRaises(ValueError):
            staff_dispatch.acknowledge(number, '9999')
        staff_dispatch.acknowledge(number, '1028780913')
        self.assertIsNotNone(staff_dispatch.recipient_orders('1028780913')[0]['acknowledged_at'])

    def test_modified_pdf_and_inactive_recipient_block_dispatch(self):
        number = create_worker.execute_request(self.request(), db_path=self.db_path)['order']['work_order_no']
        data = self.review_pdf(number)
        path = Path(service.get_work_order(number)['pdf_path'])
        path.write_bytes(data + b'changed')
        with self.assertRaises(DocumentReviewRequired):
            staff_dispatch.prepare_dispatch(number, '455740857', '455740857', self.wash_staff_id)
        path.write_bytes(data)
        staff_dispatch.prepare_dispatch(number, '455740857', '455740857', self.wash_staff_id)
        with test_database(self.db_path) as con:
            con.execute('UPDATE service_staff SET active=0 WHERE id=?', (self.wash_staff_id,))
        sender = SimpleNamespace(send_document=Mock())
        with self.assertRaises(ValueError):
            send_work_order(work_order_no=number, sender=sender, actor='455740857')
        sender.send_document.assert_not_called()
        self.assertEqual(staff_dispatch.staff_options('CAR_WASH'), [])

    def test_uncertain_transfer_stays_blocked_and_appears_for_manager_review(self):
        number = create_worker.execute_request(self.request(), db_path=self.db_path)['order']['work_order_no']
        self.review_pdf(number)
        staff_dispatch.prepare_dispatch(number, '455740857', '455740857', self.wash_staff_id)
        token = staff_dispatch.claim_send(number)
        sender = SimpleNamespace(send_document=Mock(side_effect=TimeoutError('uncertain upload')))
        with self.assertRaises(TimeoutError):
            send_work_order(work_order_no=number, sender=sender, actor='455740857', dispatch_token=token)
        staff_dispatch.finish_send(number, 'FAILED')
        with test_database(self.db_path) as con:
            con.row_factory = sqlite3.Row
            pending = oil_safety.pending(con, '455740857', '455740857')
            self.assertEqual([r['work_order_no'] for r in pending], [number])
            self.assertEqual(con.execute('SELECT state FROM service_work_order_dispatch').fetchone()[0], 'UNCERTAIN')
            oil_safety.resolve(con, number, token, '455740857', '455740857', 'RESEND_ALLOWED', 'verified no delivery')
        self.assertTrue(staff_dispatch.claim_send(number))

    def test_abandoned_send_is_recovered_as_uncertain(self):
        number = create_worker.execute_request(self.request(), db_path=self.db_path)['order']['work_order_no']
        self.review_pdf(number)
        staff_dispatch.prepare_dispatch(number, '455740857', '455740857', self.wash_staff_id)
        with patch('tools.fleet.work_orders.core.oil_safety.time.time', return_value=1000):
            self.assertTrue(staff_dispatch.claim_send(number))
        with patch('tools.fleet.work_orders.core.oil_safety.time.time', return_value=1601):
            self.assertFalse(staff_dispatch.claim_send(number))
        with test_database(self.db_path) as con:
            self.assertEqual(con.execute('SELECT state FROM service_work_order_dispatch').fetchone()[0], 'UNCERTAIN')

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
    async def test_manager_and_wash_recipient_receive_identical_pdf_bytes_and_filename(self):
        from tools.fleet.work_orders.channels.bale.message_handler import send_manager_excel
        from tools.fleet.work_orders.channels.bale.staff_flow import dispatch
        order = create_worker.execute_request(self.request(), db_path=self.db_path)['order']
        uploads = []
        async def upload(**kw):
            uploads.append((kw['chat_id'], kw['filename'], kw['document'].read()))
            return SimpleNamespace(message_id=len(uploads))
        gateway = SimpleNamespace(adapters={'bale': SimpleNamespace(_bot=SimpleNamespace(send_document=upload))})
        def render(snapshot):
            pdf = Path(snapshot).with_suffix('.pdf')
            pdf.write_bytes(b'%PDF-1.4\nEXACT CAR WASH DOCUMENT\n%%EOF')
            return pdf
        with patch('tools.fleet.work_orders.core.delivery.export_staff_pdf', side_effect=render) as renderer:
            await send_manager_excel(gateway, '455740857', order)
            confirm_document_review(order['work_order_no'], '455740857')
            reply = await dispatch(gateway, order['work_order_no'], '455740857', '455740857', self.wash_staff_id)
            renderer.assert_called_once()
        self.assertIn('مهدی کارگری', reply)
        self.assertEqual([u[0] for u in uploads], ['455740857', '1028780913'])
        self.assertEqual(uploads[0][1:], uploads[1][1:])

    async def test_numbered_receipt_notifies_manager_without_resending_pdf(self):
        from tools.fleet.work_orders.channels.bale import staff_flow
        number = create_worker.execute_request(self.request(), db_path=self.db_path)['order']['work_order_no']
        self.review_pdf(number)
        staff_dispatch.prepare_dispatch(number, '455740857', '455740857', self.wash_staff_id)
        send_work_order(work_order_no=number, sender=SimpleNamespace(send_document=lambda **kw: {'message_id': 123}))
        bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
        gateway = SimpleNamespace(adapters={'bale': SimpleNamespace(_bot=bot)})
        event = SimpleNamespace(text='تایید ' + number,
            source=SimpleNamespace(platform='bale', chat_type='dm', user_id='1028780913', chat_id='1028780913'))
        with patch.object(staff_flow, 'review_context', return_value=None):
            result = staff_flow.handle_staff_receipt(event, gateway, send=lambda *a: None)
            self.assertEqual(result['reason'], 'staff-receipt')
            await asyncio.gather(*list(staff_flow.tasks))
        self.assertEqual([c.kwargs['chat_id'] for c in bot.send_message.await_args_list], ['1028780913', '455740857'])
        bot.send_document.assert_not_awaited()
        self.assertIsNotNone(staff_dispatch.recipient_orders('1028780913')[0]['acknowledged_at'])

    async def test_text_entry_edit_restart_review_select_wash_recipient_and_send_same_pdf(self):
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
            self.review_pdf(order['work_order_no'], confirm=False)
        async def upload(**kwargs):
            deliveries.append((str(kwargs['chat_id']), kwargs['document'].read()))
            return SimpleNamespace(message_id=321)
        bot = SimpleNamespace(send_message=send_message,
                              send_document=upload,
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
            self.assertEqual(session.stage, 'REVIEW')
            self.assertEqual(len(deliveries), 1)
            self.assertEqual(deliveries[0][0], '455740857')
            self.assertEqual(deliveries[0][1]['work_order_type'], 'CAR_WASH')
            self.assertTrue(handler.reviews)
            self.assertNotIn('staff_', str(sent[-1]))
            await enter(action='review_confirm')
            self.assertEqual(session.stage, 'STAFF')
            self.assertEqual([s['bale_id'] for s in session.staff_options], ['1028780913'])
            expected = Path(service.get_work_order(session.order_no)['pdf_path']).read_bytes()
            await enter(action='staff_1')
            self.assertEqual(deliveries[1], ('1028780913', expected))
            self.assertEqual(service.get_work_order(session.order_no)['status'], 'SENT')
