"""Daily lifecycle acceptance on disposable DBs and captured transport only."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.fleet.work_orders.core import db, service, delivery, oil_safety, operational, staff_dispatch
from tools.fleet.work_orders.core.review import confirm_document_review
from tools.fleet.work_orders.channels.bale import create_worker
from tools.fleet.work_orders.channels.bale.message_handler import FormSession, WorkOrderMenuHandler
from tools.fleet.work_orders.channels.bale.proposal_form import creation_metadata, edit_oil_interval
from tools.fleet.work_orders.dev import test_oil_p1_safety as shared
from tools.fleet.work_orders.dev.test_permissions import test_database

REAL_TODAY = operational.operational_today


class MinimalLifecycleTests(unittest.IsolatedAsyncioTestCase):
    setUp = shared.OilSafetyTests.setUp
    create = shared.OilSafetyTests.create
    prepare = shared.OilSafetyTests.prepare
    read = shared.OilSafetyTests.read
    uncertain = shared.OilSafetyTests.uncertain

    def tomorrow(self):
        self.fixture.test_today = '1405/06/24'

    def sent(self):
        number = self.create()
        self.prepare(number)
        delivery.send_work_order(work_order_no=number,sender=SimpleNamespace(send_document=lambda **kw:{'ok':True}))
        return number

    def reissue(self, number):
        return create_worker.execute_request(dict(action='reissue',bale_id=shared.MANAGER,
            work_order_no=number,reason='TEST correction',operation_id='daily-test-revision'),db_path=self.db_path)

    def test_tehran_midnight_uses_operational_jalali_date(self):
        from tools.fleet.report_caption import jalali_today
        with patch.object(operational,'operational_today', wraps=operational.operational_today):
            # Exercise the real date converter, independent of the fixture clock.
            local_before = datetime(2026,10,20,20,29,59,tzinfo=timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Tehran'))
            local_after = datetime(2026,10,20,20,30,0,tzinfo=timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Tehran'))
            self.assertEqual(jalali_today(local_before.date()),'1405/07/28')
            self.assertEqual(jalali_today(local_after.date()),'1405/07/29')
            self.assertEqual(REAL_TODAY(datetime(2026,10,20,20,29,59,tzinfo=timezone.utc)),'1405/07/28')
            self.assertEqual(REAL_TODAY(datetime(2026,10,20,20,30,0,tzinfo=timezone.utc)),'1405/07/29')

    def test_dependency_free_calendar_matches_jdatetime(self):
        from datetime import timedelta
        from tools.fleet.report_caption import jalali_today
        start=datetime(2020,1,1,12,tzinfo=timezone.utc)
        for index in range(0,5000,3):
            value=start+timedelta(days=index)
            self.assertEqual(REAL_TODAY(value),jalali_today(value.date()))

    def test_expired_draft_rejects_all_backend_operations(self):
        number = self.create()
        before = service.get_work_order(number)
        self.tomorrow()
        for action in ('edit','preview','confirm_review','resume_dispatch','reissue','revision_draft'):
            result = create_worker.execute_request(dict(action=action,bale_id=shared.MANAGER,chat_id=shared.MANAGER,
                work_order_no=number,reason='TEST',operation_id='old'),db_path=self.db_path)
            self.assertFalse(result['ok'],(action,result))
            self.assertEqual(result['message'],operational.EXPIRED_MESSAGE)
        with self.assertRaises(operational.OperationalDayExpired):
            delivery.send_work_order(work_order_no=number,sender=SimpleNamespace(send_document=AsyncMock()))
        with self.assertRaises(operational.OperationalDayExpired):
            service.cancel_draft(number,shared.MANAGER,'TEST')
        self.assertEqual(service.get_work_order(number),before)

    def test_expired_uncertain_is_absent_and_cannot_retry(self):
        number, uploads = self.uncertain()
        self.tomorrow()
        con=db.connect_db()
        try:
            self.assertEqual(oil_safety.pending(con,shared.MANAGER,shared.MANAGER),[])
            attempt=con.execute('SELECT id FROM service_work_order_delivery_attempts').fetchone()[0]
            with self.assertRaises(operational.OperationalDayExpired):
                oil_safety.resolve(con,number,attempt,shared.MANAGER,shared.MANAGER,'RESEND_ALLOWED','TEST')
        finally:con.close()
        with self.assertRaises(operational.OperationalDayExpired):staff_dispatch.claim_send(number)
        self.assertEqual(self.read('SELECT state FROM service_work_order_dispatch')[0]['state'],'UNCERTAIN')
        self.assertEqual(len(uploads),1)

    def test_yesterday_does_not_block_same_machine_today(self):
        old=self.create()
        self.tomorrow()
        new=service.create_work_order(work_order_type='OIL_CHANGE',jalali_date=self.fixture.test_today,shift='روزانه',
            machine_codes=['EX1252'],item_actions={'EX1252':'OIL_CHANGE_PC1250-8_1800'},created_by='bale:'+shared.MANAGER)
        self.assertNotEqual(old,new['work_order_no'])
        self.assertIsNone(service.get_work_order(old)['superseded_by'])

    def test_old_callback_cannot_reactivate_form(self):
        number=self.create()
        handler=WorkOrderMenuHandler(db_path=self.db_path,worker=AsyncMock())
        key=('bale',shared.MANAGER,shared.MANAGER)
        card=handler._review_card(key,number)
        markup=handler._keyboard(card,handler.reviews[card])
        data=markup['inline_keyboard'][0][0]['callback_data']
        self.tomorrow()
        replies=[]
        event=SimpleNamespace(text='',message_id='old',raw_message={'bale_inline_callback':True,'data':data},
            source=SimpleNamespace(platform='bale',chat_type='dm',user_id=shared.MANAGER,chat_id=shared.MANAGER))
        result=handler.handle(event,None,send=lambda g,c,t:replies.append(t))
        self.assertEqual(result['reason'],'work-order-expired')
        self.assertEqual(replies,[operational.EXPIRED_MESSAGE])
        handler.worker.assert_not_called()

    def test_cancel_requires_confirmation_and_keeps_audit_and_files(self):
        number=self.create()
        old=service.get_work_order(number)
        req=dict(action='cancel_draft',bale_id=shared.MANAGER,chat_id=shared.MANAGER,work_order_no=number,reason='TEST cancelled')
        self.assertFalse(create_worker.execute_request(req,db_path=self.db_path)['ok'])
        self.assertTrue(create_worker.execute_request(dict(req,confirmed=True),db_path=self.db_path)['ok'])
        row=service.get_work_order(number)
        self.assertEqual(row['status'],'CANCELLED')
        self.assertIn('DRAFT_CANCELLED',row['notes'])
        self.assertEqual(Path(old['excel_path']).read_bytes(),Path(row['excel_path']).read_bytes())
        self.assertNotEqual(self.create(),number)

    def test_same_day_draft_edit_creates_new_number(self):
        old=self.create()
        result=create_worker.execute_request(dict(action='edit',bale_id=shared.MANAGER,work_order_no=old),db_path=self.db_path)
        p=result['proposal'];edit_oil_interval(p,'EX1252',1600)
        req=self.fixture.request();req.update(proposal=creation_metadata(p),item_actions={'EX1252':p['items'][0]['action_code']})
        new=self.create(req)
        self.assertEqual(service.get_work_order(new)['revision_of'],old)
        self.assertEqual(service.get_work_order(old)['superseded_by'],new)

    def test_sent_revision_keeps_parent_until_successful_delivery(self):
        old=self.sent();before=service.get_work_order(old)
        result=self.reissue(old);self.assertTrue(result['ok'],result)
        new=result['orders'][0]['work_order_no']
        self.assertEqual(service.get_work_order(new)['revision_of'],old)
        self.assertIsNone(service.get_work_order(old)['superseded_by'])
        self.assertIsNone(service.get_work_order(new)['assigned_staff_id'])
        self.prepare(new)
        delivery.send_work_order(work_order_no=new,sender=SimpleNamespace(send_document=lambda **kw:{'ok':True}))
        self.assertEqual(service.get_work_order(old)['superseded_by'],new)
        self.assertEqual(service.get_work_order(old)['pdf_path'],before['pdf_path'])

    def test_uncertain_revision_does_not_supersede_parent(self):
        old=self.sent();new=self.reissue(old)['orders'][0]['work_order_no'];self.prepare(new)
        def lost(**kw):raise TimeoutError('TEST')
        with self.assertRaises(TimeoutError):delivery.send_work_order(work_order_no=new,sender=SimpleNamespace(send_document=lost))
        self.assertIsNone(service.get_work_order(old)['superseded_by'])
        with self.assertRaises(ValueError):service.cancel_draft(new,shared.MANAGER,'TEST')

    def test_pending_revision_can_be_edited_then_delivered(self):
        old=self.sent();first=self.reissue(old)['orders'][0]['work_order_no']
        req=self.fixture.request();p=create_worker.execute_request(dict(action='edit',bale_id=shared.MANAGER,work_order_no=first),db_path=self.db_path)['proposal']
        edit_oil_interval(p,'EX1252',1600)
        req.update(proposal=creation_metadata(p),item_actions={'EX1252':p['items'][0]['action_code']})
        last=self.create(req);self.prepare(last)
        delivery.send_work_order(work_order_no=last,sender=SimpleNamespace(send_document=lambda **kw:{'ok':True}))
        self.assertEqual(service.get_work_order(old)['superseded_by'],last)

    async def test_sent_reuse_preserves_pdf_and_makes_no_review_card(self):
        number=self.sent();order=service.get_work_order(number)
        before=Path(order['pdf_path']).read_bytes()
        result=create_worker.execute_request(self.fixture.request(),db_path=self.db_path)
        self.assertTrue(result['ok'],result);self.assertFalse(result['orders'][0]['reviewable'])
        sender=AsyncMock()
        handler=WorkOrderMenuHandler(db_path=self.db_path,worker=AsyncMock(return_value=result),document_sender=sender)
        key=('bale',shared.MANAGER,shared.MANAGER);session=FormSession(expires=handler.clock()+600)
        handler.pending[key]=session
        with patch('tools.fleet.work_orders.core.delivery.export_staff_pdf') as export:
            await handler._run_request(key,session,{'action':'create'},None,lambda *a:None)
            sender.assert_not_called();export.assert_not_called()
            with self.assertRaises(ValueError):delivery.prepare_manager_pdf(number,shared.MANAGER)
            export.assert_not_called()
        self.assertEqual(handler.reviews,{})
        self.assertEqual(Path(order['pdf_path']).read_bytes(),before)
        self.assertEqual(hashlib.sha256(before).hexdigest(),__import__('tools.fleet.work_orders.core.review',fromlist=['last_record']).last_record(order,'MANAGER_DOCUMENT_REVIEW')['pdf_sha256'])

    def test_slow_pdf_export_holds_no_write_lock_and_cannot_overwrite_concurrent_sent(self):
        number=self.create();row=service.get_work_order(number)
        original=Path(row['excel_path']).with_suffix('.pdf');original.write_bytes(b'%PDF-HISTORICAL ORIGINAL')
        def render(snapshot):
            self.assertNotEqual(Path(snapshot),Path(row['excel_path']))
            con=db.connect_db()
            try:
                with con:
                    con.execute('BEGIN IMMEDIATE')
                    con.execute("UPDATE service_work_orders SET status='SENT' WHERE work_order_no=?",(number,))
            finally:con.close()
            path=Path(snapshot).with_suffix('.pdf');path.write_bytes(b'%PDF-NEW RENDER')
            return path
        with patch.object(delivery,'export_staff_pdf',side_effect=render):
            with self.assertRaisesRegex(ValueError,'SENT'):delivery.prepare_manager_pdf(number,shared.MANAGER)
        self.assertEqual(original.read_bytes(),b'%PDF-HISTORICAL ORIGINAL')

    def test_pdf_render_crossing_midnight_does_not_replace_original(self):
        number=self.create();row=service.get_work_order(number)
        original=Path(row['excel_path']).with_suffix('.pdf');original.write_bytes(b'%PDF-ORIGINAL')
        def render(snapshot):
            self.tomorrow()
            path=Path(snapshot).with_suffix('.pdf');path.write_bytes(b'%PDF-NEW RENDER');return path
        with patch.object(delivery,'export_staff_pdf',side_effect=render):
            with self.assertRaises(operational.OperationalDayExpired):delivery.prepare_manager_pdf(number,shared.MANAGER)
        self.assertEqual(original.read_bytes(),b'%PDF-ORIGINAL')

    async def test_resume_sent_reports_status_without_selecting_recipient(self):
        number=self.sent();before=service.get_work_order(number)
        request=dict(action='resume_dispatch',bale_id=shared.MANAGER,chat_id=shared.MANAGER,work_order_no=number)
        result=create_worker.execute_request(request,db_path=self.db_path)
        self.assertTrue(result['already_sent']);self.assertEqual(result['status'],'SENT')
        handler=WorkOrderMenuHandler(db_path=self.db_path,worker=AsyncMock(return_value=result))
        key=('bale',shared.MANAGER,shared.MANAGER);s=FormSession(expires=handler.clock()+600,order_no=number)
        handler.pending[key]=s
        await handler._run_request(key,s,request,None,lambda *a:None)
        self.assertEqual(s.stage,'RESULT');self.assertEqual(handler.reviews,{})
        self.assertEqual(service.get_work_order(number)['send_attempts'],before['send_attempts'])

    def test_cross_owner_conflict_is_explicit_without_silent_selection_changes(self):
        number=self.create()
        req=self.fixture.request();before=deepcopy(req)
        with test_database(self.db_path) as con:con.execute("UPDATE service_work_orders SET created_by='bale:1294822197' WHERE work_order_no=?",(number,))
        result=create_worker.execute_request(req,db_path=self.db_path)
        self.assertFalse(result['ok']);self.assertIn(number,result['message']);self.assertIn('1294822197',result['message'])
        self.assertEqual(req,before);self.assertEqual(len(self.read('SELECT id FROM service_work_orders')),1)
        with self.assertRaises(ValueError):service.cancel_draft(number,shared.MANAGER,'TEST')

    def test_commit_race_returns_partial_success_and_specific_failure(self):
        second=__import__('tools.fleet.oil_change.test_proposal',fromlist=['sample_source']).sample_source('HD785-7','HD708')
        self.fixture.source['machines']+=second['machines'];self.fixture.source['plans']+=second['plans']
        req=self.fixture.request();real=service.create_work_order;calls=[]
        req['machine_codes']=list(req['item_actions'])
        def race(**kwargs):
            calls.append(kwargs['machine_codes'][0])
            if len(calls)==2:raise ValueError('تعارض: OC-1405-06-23-999 | مالک: bale:1294822197')
            return real(**kwargs)
        with patch.object(service,'create_work_order',side_effect=race):result=create_worker.execute_request(req,db_path=self.db_path)
        self.assertFalse(result['ok'],result);self.assertTrue(result['partial']);self.assertEqual(len(result['orders']),1)
        self.assertEqual(len(result['failures']),1);self.assertIn('OC-1405-06-23-999',result['failures'][0]['reason'])
        self.assertEqual(len(self.read('SELECT id FROM service_work_orders')),1)


    def test_claim_before_midnight_cannot_start_after_midnight(self):
        number=self.create();self.prepare(number);token=staff_dispatch.claim_send(number);self.tomorrow()
        con=db.connect_db()
        try:
            with self.assertRaises(operational.OperationalDayExpired):oil_safety.start_transfer(con,number,token,'TEST.pdf')
        finally:con.close()

    def test_started_transfer_can_record_late_evidence_without_new_attempt(self):
        number=self.create();self.prepare(number);uploads=[]
        def upload(**kw):uploads.append(kw);self.tomorrow();return {'ok':True}
        result=delivery.send_work_order(work_order_no=number,sender=SimpleNamespace(send_document=upload))
        self.assertEqual(result['status'],'SENT');self.assertEqual(len(uploads),1)
        self.assertIn('LATE_TRANSFER_RESULT',service.get_work_order(number)['notes'])
        with self.assertRaises(operational.OperationalDayExpired):staff_dispatch.claim_send(number)

    def ui(self):
        sent=[];sequence=[0];uploads=[]
        async def send_message(**kwargs):
            sequence[0]+=1;sent.append(kwargs);return SimpleNamespace(message_id=sequence[0])
        async def edit(**kwargs):return SimpleNamespace(message_id=kwargs['message_id'])
        async def upload(**kwargs):
            uploads.append(kwargs['document'].read());return SimpleNamespace(message_id=len(uploads))
        bot=SimpleNamespace(send_message=send_message,send_document=upload,edit_message_reply_markup=edit,edit_message_text=edit,delete_message=edit)
        gateway=SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=bot)})
        async def worker(request):return create_worker.execute_request(request,db_path=self.db_path)
        handler=WorkOrderMenuHandler(db_path=self.db_path,worker=worker,document_sender=AsyncMock())
        def enter(text,data=None):
            event=SimpleNamespace(text=text,message_id=str(sequence[0])+text+str(data),
                raw_message={'bale_inline_callback':True,'data':data,'origin_message_id':str(sequence[0])} if data else None,
                source=SimpleNamespace(platform='bale',chat_type='dm',user_id=shared.MANAGER,chat_id=shared.MANAGER))
            return handler.handle(event,gateway,send=lambda *a:None)
        async def settle():
            while handler.tasks or handler.lifecycle.tasks:await asyncio.gather(*list(handler.tasks|handler.lifecycle.tasks))
        async def click(action):
            key=('bale',shared.MANAGER,shared.MANAGER);s=handler.pending[key]
            markup=handler._keyboard(key,s)
            data=next(b['callback_data'] for row in markup['inline_keyboard'] for b in row if b['callback_data'].endswith(':'+action))
            enter('',data);await settle()
        return handler,enter,settle,click,uploads

    async def test_sent_edit_uses_existing_command_reason_form_and_new_review(self):
        old=self.sent();before=service.get_work_order(old)
        handler,enter,settle,click,uploads=self.ui()
        key=('bale',shared.MANAGER,shared.MANAGER)
        enter('ویرایش '+old);await settle()
        self.assertEqual(handler.pending[key].stage,'EDIT_REASON')
        self.assertEqual(len(self.read('SELECT id FROM service_work_orders')),1)
        enter('حکم کار');await settle();self.assertEqual(handler.pending[key].stage,'MENU')
        enter('ویرایش '+old);await settle();enter('TEST correction');await settle()
        s=handler.pending[key];self.assertEqual(s.stage,'PROPOSAL')
        self.assertEqual(s.proposal['revision_of'],old)
        self.assertEqual(s.proposal['reissue_reason'],'TEST correction')
        self.assertEqual(len(self.read('SELECT id FROM service_work_orders')),1)
        edit_oil_interval(s.proposal,'EX1252',1600)
        async def reviewed_file(gateway,chat,order):
            path=self.db_path.parent/(order['work_order_no']+'.pdf')
            path.write_bytes(b'%PDF-TEST REVISION')
            delivery.store_manager_pdf(order['work_order_no'],path)
        handler.document_sender=reviewed_file
        await click('confirm');new=handler.pending[key].order_no
        self.assertNotEqual(new,old)
        new_order=service.get_work_order(new)
        self.assertEqual(new_order['revision_of'],old)
        self.assertIsNone(new_order['assigned_staff_id'])
        self.assertIsNone(service.get_work_order(old)['superseded_by'])
        card=handler.reviews[(*key,new)]
        markup=handler._keyboard((*key,new),card)
        data=next(b['callback_data'] for row in markup['inline_keyboard'] for b in row if b['callback_data'].endswith(':review_confirm'))
        enter('',data);await settle()
        self.assertEqual(handler.pending[key].stage,'STAFF')
        from tools.fleet.work_orders.core.review import last_record
        self.assertEqual(last_record(service.get_work_order(new),'MANAGER_DOCUMENT_REVIEW')['bale_id'],shared.MANAGER)
        self.assertEqual(Path(before['pdf_path']).read_bytes(),b'%PDF-1.4\nTEST DOCUMENT\n')

    async def test_draft_cancel_stays_in_edit_flow_and_requires_reason_and_callback(self):
        number=self.create();before=service.get_work_order(number)
        handler,enter,settle,click,uploads=self.ui();key=('bale',shared.MANAGER,shared.MANAGER)
        enter('ویرایش '+number);await settle()
        self.assertEqual(handler.pending[key].stage,'PROPOSAL')
        await click('draft_cancel');self.assertEqual(handler.pending[key].stage,'EDIT_CANCEL_REASON')
        enter('برگشت');await settle();self.assertEqual(handler.pending[key].stage,'PROPOSAL')
        await click('draft_cancel');enter('TEST cancellation');await settle()
        self.assertEqual(handler.pending[key].stage,'EDIT_CANCEL_CONFIRM')
        enter('تایید لغو پیش نویس');await settle()
        self.assertEqual(service.get_work_order(number)['status'],'FILE_READY')
        await click('draft_cancel_confirm')
        self.assertEqual(service.get_work_order(number)['status'],'CANCELLED')
        self.assertEqual(len(self.read('SELECT id FROM service_work_orders')),1)
        self.assertEqual(Path(before['excel_path']).read_bytes(),Path(service.get_work_order(number)['excel_path']).read_bytes())

    async def test_retired_history_record_recovers_individually_and_entry_is_not_date_input(self):
        from dataclasses import asdict
        from tools.bale_ui import StateStore
        key=('bale',shared.MANAGER,shared.MANAGER)
        healthy=(*key,'OC-1405-06-23-001')
        stale=asdict(FormSession(expires=__import__('time').time()+600,stage='HISTORY_DATE'))
        stale.update(managed_orders=[],management_date='',management_history=True)
        saved=asdict(FormSession(expires=__import__('time').time()+600,stage='REVIEW',order_no=healthy[-1]))
        store=StateStore(self.db_path.parent/'legacy-ui.json');store.save([[list(key),stale],[list(healthy),saved]])
        h=WorkOrderMenuHandler(db_path=self.db_path,state_store=store,worker=AsyncMock())
        self.assertEqual(h.pending[key].stage,'MENU');self.assertIn(healthy,h.reviews)
        event=SimpleNamespace(text='حکم کار',message_id='entry',raw_message=None,
            source=SimpleNamespace(platform='bale',chat_type='dm',user_id=shared.MANAGER,chat_id=shared.MANAGER))
        replies=[];h.handle(event,None,send=lambda g,c,t:replies.append(t))
        self.assertEqual(h.pending[key].stage,'MENU');self.assertIn(healthy,h.reviews)
        self.assertNotIn('نامعتبر',''.join(replies));h.worker.assert_not_called()

    def test_historical_af_proposal_and_source_dates_are_not_operational_order_dates(self):
        from tools.fleet.air_filter import proposal as af
        from tools.fleet.work_orders.core.operational import operational_today
        copy=Path('E:/KomatsoAI/runtime/net_minimal_recovery_20261021/source_copies/هواکش.xlsx')
        before=hashlib.sha256(copy.read_bytes()).hexdigest()
        proposed=af.build_proposal(path=copy)
        with patch.object(af,'build_proposal',return_value=proposed):
            result=create_worker.execute_request(dict(action='propose',bale_id=shared.MANAGER,
                work_order_type='AIR_FILTER'),db_path=self.db_path)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['proposal'],proposed)
        self.assertNotEqual(proposed['plan_date'],operational_today())
        self.assertEqual(result['operational_date'],operational_today())
        self.assertTrue(proposed['items'])
        self.assertEqual(hashlib.sha256(copy.read_bytes()).hexdigest(),before)

    async def test_main_entry_exits_every_idle_form_and_no_extra_entry_actions_exist(self):
        handler,enter,settle,click,uploads=self.ui();key=('bale',shared.MANAGER,shared.MANAGER)
        for stage in ('DATE','MACHINES','SHIFT','PROPOSAL','REMOVE','ADD_CODES','ADD_ACTION',
                      'EDIT_CODE','EDIT_INTERVAL','EDIT_CONFIRM','EDIT_REASON',
                      'EDIT_CANCEL_REASON','EDIT_CANCEL_CONFIRM','RESULT'):
            handler.pending[key]=FormSession(expires=handler.clock()+600,stage=stage)
            enter('حکم کار');await settle()
            self.assertEqual(handler.pending[key].stage,'MENU',stage)
            from tools.fleet.work_orders.channels.bale.keyboards import entry_keyboard
            self.assertEqual(set(entry_keyboard().actions),{'oil','greasing','air_filter','delivery_review','cancel'})
        handler.worker=AsyncMock()
        enter('تاریخچه احکام');await settle();handler.worker.assert_not_called()

    async def test_af_source_proposal_opens_then_creates_today_for_day_evening_and_night(self):
        from tools.fleet.air_filter import proposal as af
        copy=Path('E:/KomatsoAI/runtime/net_minimal_recovery_20261021/source_copies/هواکش.xlsx')
        proposed=af.build_proposal(path=copy)
        handler,enter,settle,click,uploads=self.ui();key=('bale',shared.MANAGER,shared.MANAGER)
        from tools.fleet.work_orders.channels.bale.proposal_form import creation_metadata
        with patch.object(af,'build_proposal',return_value=proposed),patch.object(af,'SOURCE',copy):
            enter('حکم کار');await settle();await click('air_filter')
            session=handler.pending[key]
            self.assertEqual(session.stage,'PROPOSAL')
            self.assertEqual(session.jalali_date,self.fixture.test_today)
            self.assertEqual(session.proposal,proposed)
            for shift in ('صبح','عصر','شب'):
                result=create_worker.execute_request(dict(action='create',bale_id=shared.MANAGER,
                    work_order_type='AIR_FILTER',jalali_date=session.jalali_date,shift=shift,
                    machine_codes=[i['machine_code'] for i in proposed['items']],
                    item_actions={i['machine_code']:i['action_code'] for i in proposed['items']},
                    proposal=creation_metadata(proposed)),db_path=self.db_path)
                self.assertTrue(result['ok'],result)
                order=service.get_work_order(result['order']['work_order_no'])
                self.assertEqual(order['jalali_date'],self.fixture.test_today)
                self.assertEqual(order['shift'],shift)
                self.assertEqual([i['machine_code'] for i in order['items']],[i['machine_code'] for i in proposed['items']])

    def test_full_batch_preflight_lists_every_selected_machine_without_creating_any(self):
        number=self.create()
        extra=__import__('tools.fleet.oil_change.test_proposal',fromlist=['sample_source']).sample_source('HD785-7','HD708')
        self.fixture.source['machines']+=extra['machines'];self.fixture.source['plans']+=extra['plans']
        req=self.fixture.request();req['machine_codes']=list(req['item_actions'])
        with test_database(self.db_path) as con:
            con.execute("UPDATE service_work_orders SET created_by='bale:641220453' WHERE work_order_no=?",(number,))
        before=deepcopy(req)
        result=create_worker.execute_request(req,db_path=self.db_path)
        self.assertFalse(result['ok']);self.assertEqual(result['error'],'CONFLICT')
        self.assertEqual({f['machine_code'] for f in result['failures']},set(req['machine_codes']))
        self.assertIn(number,result['message']);self.assertIn('641220453',result['message'])
        self.assertEqual(len(self.read('SELECT id FROM service_work_orders')),1);self.assertEqual(req,before)

    async def test_partial_batch_false_result_still_shows_success_and_exact_failure(self):
        second=__import__('tools.fleet.oil_change.test_proposal',fromlist=['sample_source']).sample_source('HD785-7','HD708')
        self.fixture.source['machines']+=second['machines'];self.fixture.source['plans']+=second['plans']
        req=self.fixture.request();req['machine_codes']=list(req['item_actions'])
        real=service.create_work_order;calls=[]
        def race(**kwargs):
            calls.append(kwargs['machine_codes'][0])
            if len(calls)==2:raise ValueError('OC-1405-06-23-999 | bale:641220453 | FILE_READY')
            return real(**kwargs)
        with patch.object(service,'create_work_order',side_effect=race):
            result=create_worker.execute_request(req,db_path=self.db_path)
        self.assertFalse(result['ok']);self.assertTrue(result['partial'])
        replies=[];sender=AsyncMock()
        h=WorkOrderMenuHandler(db_path=self.db_path,worker=AsyncMock(return_value=result),document_sender=sender)
        key=('bale',shared.MANAGER,shared.MANAGER);s=FormSession(expires=h.clock()+600)
        h.pending[key]=s
        await h._run_request(key,s,req,None,lambda g,c,t:replies.append(t))
        sender.assert_awaited_once()
        text='\n'.join(replies)
        self.assertIn(result['orders'][0]['work_order_no'],text)
        self.assertIn('OC-1405-06-23-999',text);self.assertIn('ساخته نشد',text)
        self.assertEqual(len(self.read('SELECT id FROM service_work_orders')),1)



class NativeMinimalTests(unittest.IsolatedAsyncioTestCase):
    """Explicit native-only contract; transport and databases remain isolated."""
    def setUp(self):
        # Keep native Workbook paths within Excel's path limit. The fault
        # injection fixture has an additional nested directory for subprocesses.
        self.fixture=shared.OilServiceEditTests('test_repeated_override_retains_original_evidence_for_all_cycles')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.db_path=self.fixture.db_path
        for target,value in (('tools.fleet.work_orders.core.permissions.DB_PATH',self.db_path),):
            p=patch(target,value);p.start();self.addCleanup(p.stop)
        p=patch.object(delivery,'archive_delivered_order');p.start();self.addCleanup(p.stop)
        with test_database(self.db_path) as con:
            oil_safety.migrate(con)
            self.staff=con.execute("INSERT INTO service_staff(display_name,bale_id,service_role,active) VALUES ('TEST ONLY',?,'OIL_CHANGE',1)",(shared.TECH,)).lastrowid
            self.roster=con.execute("INSERT INTO service_staff_roster(display_name,staff_id) VALUES ('TEST ONLY',?)",(self.staff,)).lastrowid
        import subprocess,sys
        run=subprocess.run
        def diagnostic(*args,**kwargs):
            result=run(*args,**kwargs)
            if result.returncode:
                print('NATIVE EXPORT DIAGNOSTIC:',result.stderr.decode('utf-8',errors='replace'),file=sys.stderr)
            return result
        p=patch('tools.fleet.work_orders.core.pdf_document.subprocess.run',side_effect=diagnostic)
        p.start();self.addCleanup(p.stop)
    create = shared.OilSafetyTests.create
    read = shared.OilSafetyTests.read
    ui = MinimalLifecycleTests.ui

    async def test_native_sent_correction_uses_reviewed_bytes_and_preserves_sent_original(self):
        from tools.fleet.work_orders.channels.bale.message_handler import send_manager_excel
        from tools.fleet.work_orders.core.review import approved_pdf_bytes
        old=self.create()
        path,data=await asyncio.to_thread(delivery.prepare_manager_pdf,old,shared.MANAGER)
        delivery.store_manager_pdf(old,path,pdf_bytes=data)
        confirm_document_review(old,shared.MANAGER)
        staff_dispatch.prepare_dispatch(old,shared.MANAGER,shared.MANAGER,self.staff,self.roster)
        captured=[]
        delivery.send_work_order(work_order_no=old,sender=SimpleNamespace(send_document=lambda **kw:captured.append(kw['document_bytes'])))
        self.assertEqual(captured,[data])
        before=service.get_work_order(old);old_hash=hashlib.sha256(Path(before['pdf_path']).read_bytes()).hexdigest()
        h,enter,settle,click,uploads=self.ui();key=('bale',shared.MANAGER,shared.MANAGER)
        h.document_sender=send_manager_excel
        enter('ویرایش '+old);await settle();enter('NATIVE TEST correction');await settle()
        s=h.pending[key];edit_oil_interval(s.proposal,'EX1252',1600)
        await click('confirm');new=h.pending[key].order_no
        self.assertNotEqual(new,old);self.assertEqual(service.get_work_order(new)['revision_of'],old)
        self.assertIsNone(service.get_work_order(old)['superseded_by'])
        self.assertIsNone(service.get_work_order(new)['assigned_staff_id'])
        self.assertEqual(len(uploads),1)
        card=h.reviews[(*key,new)];markup=h._keyboard((*key,new),card)
        callback=next(b['callback_data'] for row in markup['inline_keyboard'] for b in row if b['callback_data'].endswith(':review_confirm'))
        enter('',callback);await settle();self.assertEqual(h.pending[key].stage,'STAFF')
        self.assertEqual(approved_pdf_bytes(service.get_work_order(new)),uploads[0])
        await click('staff_1')
        self.assertEqual(service.get_work_order(new)['status'],'SENT')
        self.assertEqual(service.get_work_order(old)['superseded_by'],new)
        self.assertEqual(uploads[1],uploads[0])
        self.assertEqual(hashlib.sha256(Path(before['pdf_path']).read_bytes()).hexdigest(),old_hash)
        repeated=create_worker.execute_request(self.fixture.request(),db_path=self.db_path)
        self.assertFalse(repeated['orders'][0]['reviewable'])
        self.assertEqual(hashlib.sha256(Path(before['pdf_path']).read_bytes()).hexdigest(),old_hash)
        import os,json,shutil
        evidence=os.environ.get('KOMATSO_OIL_EVIDENCE')
        if evidence:
            destination=Path(evidence);destination.mkdir(parents=True,exist_ok=True)
            for number in (old,new):
                row=service.get_work_order(number)
                for field in ('excel_path','pdf_path'):
                    src=Path(row[field]);shutil.copy2(src,destination/src.name)
            (destination/'native-sent-revision.json').write_text(json.dumps(dict(original=old,revision=new,
                original_pdf_sha256=old_hash,reviewed_bytes_equal_sent=True,transport='SIMULATED',pdf='NATIVE EXCEL',
                production_writes=False),indent=2),'utf-8')
