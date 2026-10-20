"""Unified oil editing: real handler/core, isolated DBs, fake Bale transport."""
from contextlib import closing
from copy import deepcopy
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch

from tools.fleet.work_orders.dev import test_inline_menus as menus
from tools.fleet.work_orders.dev.test_oil_service_edit import OilServiceEditTests
from tools.fleet.oil_change.test_proposal import sample_source
from tools.fleet.work_orders.channels.bale import create_worker
from tools.fleet.work_orders.channels.bale.keyboards import keyboard_for
from tools.fleet.work_orders.channels.bale.proposal_form import (
    creation_metadata, oil_edit_item, apply_oil_edits,
)


class OilUnifiedTests(menus.InlineMenusTests):
    async def click(self, action):
        for index in range(len(self.sent)-1,-1,-1):
            try:
                data=self.button(self.sent[index],action)
                break
            except (KeyError,StopIteration):
                continue
        else:
            self.fail('Missing inline action: ' + action)
        self.message('', data=data, origin=index+1)
        await self.settle()

    async def enter(self, text):
        result = self.message(text)
        await self.settle()
        return result

    async def start(self, source=None, add_code=None):
        session = await self.oil_edit_step(source, add_code=add_code)
        await self.click('edit_excel')
        return session

    def request(self, session):
        return dict(action='create', bale_id=self.key[1], work_order_type='OIL_CHANGE',
                    jalali_date=session.jalali_date, shift='روزانه',
                    machine_codes=[i['machine_code'] for i in session.proposal['items']],
                    item_actions={i['machine_code']:i['action_code'] for i in session.proposal['items']},
                    proposal=creation_metadata(session.proposal))

    @staticmethod
    def combined(count=3, remaining=100):
        source = sample_source('HD465-7R', 'HD800', last=1600, remaining=remaining)
        for n in range(801, 800 + count):
            extra = sample_source('HD465-7R', f'HD{n}', last=1600, remaining=remaining)
            source['machines'] += extra['machines']
            source['plans'] += extra['plans']
        return source

    async def test_only_unified_button_and_legacy_add_cannot_start_old_flow(self):
        s = await self.oil_edit_step(add_code=None)
        self.assertEqual(set(keyboard_for(s).actions), {'edit_excel','remove','confirm','cancel'})
        self.assertEqual(keyboard_for(s).actions['edit_excel'].label, '➕ افزودن/ویرایش')
        forged = self.button(self.sent[-1],'edit_excel').rsplit(':',1)[0] + ':add'
        self.assertEqual(self.message('',data=forged,origin=len(self.sent))['reason'],'inline-rejected')
        await self.settle()
        await self.enter('اضافه')
        self.assertEqual(s.stage,'EDIT_CODE')
        self.assertFalse(self.requests)
        result = create_worker.execute_request(dict(action='validate_add',bale_id=self.key[1],
            work_order_type='OIL_CHANGE',machine_codes=['EX1252']),db_path=self.db_path)
        self.assertEqual(result['error'],'INVALID_INPUT')

    async def test_add_default_is_confirmed_without_false_override(self):
        s = await self.start()
        before = deepcopy(s.proposal)
        await self.choose_oil('۱۲۵۲')
        await self.click('interval_keep')
        self.assertEqual(s.proposal,before)
        self.assertIn('افزودن با نوبت پیشنهادی 2000',self.sent[-1]['text'])
        await self.click('edit_apply')
        self.assertEqual(s.stage,'EDIT_CODE')
        self.assertEqual(len(s.proposal['items']),1)
        self.assertFalse(s.proposal.get('service_overrides'))
        self.assertNotIn('از سرویس',s.proposal['items'][0]['manual_note'])
        self.assertEqual(s.proposal['evaluations'],before['evaluations'])
        self.assertFalse(self.requests)

    async def test_existing_same_interval_is_a_true_noop(self):
        s = await self.start(add_code='1252')
        before = deepcopy(s.proposal)
        await self.choose_oil('ex1252');await self.enter('2000')
        self.assertIn('بدون تغییر',self.sent[-1]['text'])
        await self.click('edit_apply')
        self.assertEqual(s.proposal,before)

    async def test_changed_interval_applies_only_after_confirmation(self):
        s = await self.start(add_code='1252')
        before = deepcopy(s.proposal)
        await self.choose_oil('EX1252');await self.enter('1800')
        self.assertEqual(s.proposal,before)
        await self.click('edit_apply')
        self.assertEqual(s.proposal['service_overrides'],{'EX1252':1800})
        for key in ('remaining','current_meter','target_meter','last_interval','last_service'):
            self.assertEqual(s.proposal['items'][0]['components']['oil_change'][key],before['items'][0]['components']['oil_change'][key])

    async def test_sequential_devices_same_page_no_extra_messages_and_restart(self):
        s=await self.start(self.combined(47))
        await self.click('edit_next');await self.click('edit_next')
        count=len(self.sent)
        original_order=[i['machine_code'] for i in s.proposal['evaluations']]
        for index,interval in ((32,1600),(33,1800),(46,2000)):
            before=deepcopy(s.proposal)
            await self.click(f'edit_unit_{index}')
            await self.click(f'interval_{interval}')
            self.assertEqual(s.proposal,before)
            self.handler=self.new_handler();s=self.handler.pending[self.key]
            await self.click('edit_apply')
            self.assertEqual(s.stage,'EDIT_CODE')
            self.assertEqual(s.edit_code_page,2)
            self.assertEqual(len(self.sent),count)
            self.assertEqual(self.sent[-1]['text'],'➕ افزودن/ویرایش')
            self.assertIn(f'✅ HD{800+index}-{interval}',str(self.sent[-1]))
            from tools.fleet.work_orders.channels.bale.proposal_form import oil_edit_candidates
            self.assertEqual([i['machine_code'] for i in oil_edit_candidates(s.proposal)],original_order)
        self.assertEqual([i['machine_code'] for i in s.proposal['items']],['HD832','HD833','HD846'])
        await self.click('edit_done')
        self.assertEqual(s.stage,'PROPOSAL')
        self.assertFalse(self.requests)

    async def test_all_manual_device_input_rejected_and_legacy_actions_inert(self):
        s=await self.start(self.combined(),add_code='800')
        before=deepcopy(s.proposal)
        for codes in ('HD800','800','HD800 800','HD800 UNKNOWN','HD800 HD801','افزودن چند دستگاه'):
            self.assertEqual((await self.enter(codes))['reason'],'work-order-input-rejected')
            self.assertEqual(s.stage,'EDIT_CODE');self.assertEqual(s.proposal,before)
        for action in ('add','edit_batch','edit_cancel','edit_change','edit_pending_0','edit_pending_99','edit_confirm_next'):
            current=self.button(self.sent[-1],'edit_done') if self.sent[-1].get('reply_markup') else self.button(self.sent[int(s.keyboard_message_id)-1],'edit_done')
            forged=current.rsplit(':',1)[0]+':'+action
            self.assertEqual(self.message('',data=forged,origin=s.keyboard_message_id)['reason'],'inline-rejected')
            await self.settle()
            self.assertEqual(s.proposal,before);self.assertEqual(s.stage,'EDIT_CODE')
        self.assertFalse(self.requests)

    async def test_back_discards_one_unit_pending_interval_before_selecting_another(self):
        source=self.combined(2);source['plans'][1]['last_interval']=200
        s=await self.start(source);before=deepcopy(s.proposal)
        await self.choose_oil('HD800');await self.enter('1600')
        await self.click('edit_back');await self.click('edit_back')
        await self.choose_oil('HD801')
        self.assertEqual(s.edit_intervals,{'HD801':400})
        await self.click('edit_back')
        self.assertEqual(s.proposal,before);self.assertFalse(s.edit_intervals)

    async def test_batch_failure_is_atomic_and_shared_oil_add_uses_snapshot(self):
        from tools.fleet.oil_change.proposal import resolve_items
        from tools.fleet.work_orders.channels.bale.proposal_form import add_items
        source=self.combined(2)
        s=await self.start(source)
        before=deepcopy(s.proposal)
        with self.assertRaises(ValueError):
            apply_oil_edits(s.proposal,['HD800','HD801'],{'HD800':1600,'HD801':1801})
        self.assertEqual(s.proposal,before)
        fresh=deepcopy(source)
        fresh['plans'][0]['last_interval']=1800
        fresh['machines'][0]['current_meter']=3100
        add_items(s.proposal,resolve_items(['HD800'],fresh),'')
        item=s.proposal['items'][0]
        self.assertEqual(item['action_code'],'OIL_CHANGE_HD465-7R_1800')
        self.assertEqual(item['components']['oil_change']['next_interval'],1800)
        self.assertEqual(item['components']['oil_change']['current_meter'],1002)

    async def test_47_units_three_pages_two_columns_navigation_and_single_done(self):
        s=await self.start(self.combined(47))
        pages=[]
        for page,count in ((0,16),(1,16),(2,15)):
            keyboard=keyboard_for(s)
            units=[a for name,a in keyboard.actions.items() if name.startswith('edit_unit_')]
            self.assertEqual(len(units),count)
            self.assertEqual(self.sent[-1]['text'],'➕ افزودن/ویرایش')
            self.assertTrue(all(len(row)==2 for row in keyboard.rows[:count//2]))
            self.assertEqual([a.name for a in keyboard.rows[-1]],['edit_done'])
            nav=[a.name for row in keyboard.rows for a in row if a.name in {'edit_next','edit_prev'}]
            self.assertEqual(nav,[['edit_next'],['edit_next','edit_prev'],['edit_prev']][page])
            self.assertFalse({'edit_batch','edit_back','edit_cancel','cancel'} & set(keyboard.actions))
            pages.extend(a.name for a in units)
            if page<2:await self.click('edit_next')
        self.assertEqual(len(set(pages)),47)
        await self.click('edit_prev');self.assertEqual(s.edit_code_page,1)

    async def test_aliases_preserved_in_core_and_all_valid_units_available_inline(self):
        source=self.combined(1)
        extra=sample_source('PC600-8','EX800',remaining=100)
        source['machines']+=extra['machines'];source['plans']+=extra['plans']
        s=await self.start(source)
        with self.assertRaises(ValueError):oil_edit_item(s.proposal,'۸۰۰')
        self.assertEqual(oil_edit_item(s.proposal,'hd۸۰۰')['machine_code'],'HD800')
        await self.choose_oil('HD800');self.assertEqual(s.edit_machine_code,'HD800')
        source=sample_source('D155A-6','W151',remaining=100)
        source['machines'][0].update(code='D155',legacy_code='151')
        s=await self.start(source)
        self.assertEqual(oil_edit_item(s.proposal,'W۱۵۱')['machine_code'],'D151')
        await self.choose_oil('D151');self.assertEqual(s.edit_machine_code,'D151')

    async def test_back_then_done_preserves_confirmed_and_discards_pending(self):
        s=await self.start(add_code='1252');before=deepcopy(s.proposal)
        await self.choose_oil('EX1252');await self.enter('1600')
        await self.click('edit_back');await self.click('edit_back');await self.click('edit_done')
        self.assertEqual(s.stage,'PROPOSAL')
        self.assertIs(self.handler.pending[self.key],s)
        self.assertEqual(s.proposal,before);self.assertFalse(s.edit_codes)

    async def test_step_back_preserves_page_and_discards_pending_interval(self):
        s = await self.start(self.combined(20))
        await self.click('edit_next')
        await self.click('edit_unit_16')
        await self.enter('1600')
        await self.click('edit_back')
        self.assertEqual(s.stage,'EDIT_INTERVAL')
        self.assertEqual(s.edit_intervals['HD816'],1800)
        await self.click('edit_back')
        self.assertEqual(s.stage,'EDIT_CODE')
        self.assertEqual(s.edit_code_page,1)
        self.assertFalse(s.proposal['items'])
        await self.click('edit_done')
        self.assertEqual(s.stage,'PROPOSAL')

    async def test_near_due_warning_returns_after_removal(self):
        s = await self.start(self.combined(1,remaining=14))
        original = deepcopy(s.proposal['evaluations'])
        self.assertEqual(len(s.proposal['warnings']),1)
        await self.choose_oil('HD800');await self.click('interval_keep');await self.click('edit_apply')
        self.assertFalse(s.proposal['warnings'])
        await self.click('edit_done');await self.click('remove');await self.enter('1');await self.enter('تایید حذف')
        self.assertEqual(len(s.proposal['warnings']),1)
        self.assertIn('14 ساعت',s.proposal['warnings'][0])
        self.assertEqual(s.proposal['evaluations'],original)

    async def test_editor_never_rereads_source_and_creation_requires_refresh(self):
        s = await self.start()
        snapshot = deepcopy(s.proposal['evaluations'])
        with patch('tools.fleet.oil_change.proposal.read_source',side_effect=AssertionError('snapshot only')):
            await self.choose_oil('EX1252');await self.enter('1800');await self.click('edit_apply')
        self.assertEqual(s.proposal['evaluations'],snapshot)
        with patch('tools.fleet.oil_change.source.source_hash',return_value='changed'),patch('tools.fleet.work_orders.core.service.create_work_order') as create:
            self.result = create_worker.execute_request(self.request(s),db_path=self.db_path)
            create.assert_not_called()
        await self.click('edit_done')
        await self.click('confirm')
        self.assertEqual(s.stage,'OIL_RECOVERY')
        self.assertEqual(s.oil_recovery,'refresh')
        self.assertIn('اطلاعات مبنا تغییر کرده',self.sent[-1]['text'])
        self.assertEqual(set(keyboard_for(s).actions),{'proposal_refresh','cancel'})
        fresh = deepcopy(s.proposal);fresh['source_sha256']='changed'
        self.result = {'ok':True,'proposal':fresh}
        await self.click('proposal_refresh')
        self.assertEqual(self.requests[-1]['action'],'propose')
        self.assertEqual(s.stage,'PROPOSAL')
        self.assertEqual(s.proposal['source_sha256'],'changed')

    async def test_correctable_error_returns_without_automatic_creation(self):
        s = await self.oil_edit_step()
        before = deepcopy(s.proposal)
        self.result = {'ok':False,'error':'INVALID_INPUT','message':'correctable test error'}
        await self.click('confirm')
        self.assertEqual(s.stage,'OIL_RECOVERY')
        requests = len(self.requests)
        await self.click('proposal_back')
        self.assertEqual(s.stage,'PROPOSAL')
        self.assertEqual(s.proposal,before)
        self.assertEqual(len(self.requests),requests)

    async def test_uncertain_build_error_offers_inspection_without_retry(self):
        s = await self.oil_edit_step()
        self.result = {'ok':False,'error':'CREATE_FAILED','message':'uncertain build'}
        await self.click('confirm')
        self.assertEqual(s.oil_recovery,'inspect')
        self.assertIn('proposal_inspect',keyboard_for(s).actions)
        self.result = {'ok':False,'error':'INVALID_INPUT','message':'no file found'}
        await self.click('proposal_inspect')
        self.assertEqual(self.requests[-1]['action'],'preview_latest')
        self.assertEqual(s.stage,'OIL_RECOVERY')
        count = len(self.requests)
        await self.click('proposal_back')
        self.assertEqual(s.stage,'PROPOSAL')
        self.assertEqual(len(self.requests),count)

    async def test_source_refresh_of_revision_keeps_parent_identity(self):
        s = await self.oil_edit_step()
        s.proposal.update(revision_of='OC-1405-06-23-001',revision_machine_code='EX1252')
        self.result = {'ok':False,'error':'INVALID_INPUT','recovery':'refresh','message':'changed source'}
        await self.click('confirm')
        self.result = {'ok':True,'work_order_type':'OIL_CHANGE','proposal':deepcopy(s.proposal)}
        await self.click('proposal_refresh')
        self.assertEqual(self.requests[-1],{'action':'edit','work_order_no':'OC-1405-06-23-001','bale_id':self.key[1]})
        self.assertEqual(s.proposal['revision_of'],'OC-1405-06-23-001')

    async def test_revision_parent_rejected_at_selection_other_unit_removable(self):
        s = await self.oil_edit_step(self.combined(2,remaining=-2),add_code=None)
        s.proposal.update(revision_of='OC-1405-06-23-001',revision_machine_code='HD800')
        await self.click('remove')
        await self.click('pick_0')
        self.assertEqual(s.removal_selection['selected'],[])
        self.assertIn('دستگاه اصلی حکم',self.sent[-1]['text'])
        result = await self.enter('1')
        self.assertEqual(result['reason'],'work-order-input-rejected')
        self.assertEqual(s.removal_selection['selected'],[])
        await self.click('pick_1');await self.click('select_confirm')
        self.assertEqual([i['machine_code'] for i in s.proposal['items']],['HD800'])

    async def test_legacy_session_is_migrated_without_losing_confirmed_draft(self):
        s = await self.oil_edit_step()
        before = deepcopy(s.proposal)
        s.stage='ADD_CODES';s.oil_editor_version=0
        self.handler._persist()
        self.handler = self.new_handler()
        restored = self.handler.pending[self.key]
        self.assertEqual(restored.stage,'EDIT_CODE')
        self.assertEqual(restored.proposal,before)
        self.assertEqual(restored.keyboard_revision,'')

    async def test_net_manager_deputy_and_unauthorized_actor(self):
        from tools.authorization.store import AuthorizationStore
        from tools.authorization.net import migrate_net, NET_MANAGER, NET_DEPUTY
        path = self.db_path.parent/'central.db'
        users = [('641220453',NET_MANAGER),('1294822197',NET_DEPUTY)]
        with closing(sqlite3.connect(path)) as con,con:
            con.execute('CREATE TABLE channel_users(platform TEXT,user_id TEXT,chat_id TEXT,registration_status TEXT,PRIMARY KEY(platform,user_id))')
            con.executemany('INSERT INTO channel_users VALUES(?,?,?,?)',[('bale',u,u,'approved') for u,_ in users])
        auth=AuthorizationStore(path)
        auth.migrate(self.db_path.parent/'backups');auth.migrate_admin(self.db_path.parent/'backups')
        migrate_net(auth,self.db_path.parent/'backups',assignments=users,actor='ISOLATED TEST')
        with patch('tools.authorization.net.AuthorizationStore',return_value=auth):
            for user,_role in users:
                self.key=('bale',user,user)
                s=await self.start()
                await self.choose_oil('EX1252');await self.enter('1800')
                with closing(sqlite3.connect(path)) as con,con:
                    con.execute('UPDATE auth_user_roles SET active=0 WHERE user_id=?',(user,))
                await self.click('edit_apply')
                self.assertFalse(s.proposal['items'])
            self.key=('bale','900009','900009')
            self.assertEqual((await self.enter('حکم کار'))['reason'],'work-order-permission-denied')

    async def test_real_excel_pdf_review_and_revision_from_net_menu(self):
        from tools.bale_ui import StateStore
        from tools.fleet.work_orders.channels.bale.message_handler import send_manager_excel
        from tools.fleet.work_orders.core import service, review, delivery
        from openpyxl import load_workbook
        import fitz
        fixture=OilServiceEditTests('test_repeated_override_retains_original_evidence_for_all_cycles')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        # Exercise HD465 end to end; PC1250's three native print contracts are
        # checked separately below, including every unchanged technical cell.
        code='HD464'
        fixture.source=sample_source('HD465-7R',code,last=1600,remaining=90.5)
        self.db_path=fixture.db_path;self.store=StateStore(self.db_path.parent/'real-ui.json')
        permission_patch=patch('tools.fleet.work_orders.core.permissions.DB_PATH',self.db_path)
        permission_patch.start();self.addCleanup(permission_patch.stop)
        documents=[]
        document_errors=[]
        async def send_document(**kwargs):
            documents.append((kwargs['filename'],kwargs['document'].read()))
        self.gateway.adapters['bale']._bot.send_document=send_document
        async def worker(request):
            self.requests.append(deepcopy(request))
            return create_worker.execute_request(request,db_path=self.db_path)
        async def document_sender(gateway,chat,order):
            try:
                await send_manager_excel(gateway,chat,order)
            except Exception:
                import traceback
                document_errors.append(traceback.format_exc())
                raise
        import subprocess
        real_run=subprocess.run
        def diagnostic_run(*args,**kwargs):
            result=real_run(*args,**kwargs)
            if result.returncode:
                document_errors.append(result.stderr.decode('utf-8',errors='replace'))
            return result
        process_patch=patch('tools.fleet.work_orders.core.pdf_document.subprocess.run',diagnostic_run)
        process_patch.start();self.addCleanup(process_patch.stop)
        self.worker=worker;self.document_sender=document_sender;self.handler=self.new_handler()
        await self.enter('حکم کار');await self.click('oil')
        await self.click('edit_excel');await self.choose_oil(code);await self.enter('1800')
        await self.click('edit_apply');await self.click('edit_done');await self.click('confirm')
        self.assertEqual(len(documents),1,document_errors)
        number=self.handler.pending[self.key].order_no
        order=service.get_work_order(number)
        book=load_workbook(order['excel_path'])
        try:
            self.assertEqual(book.sheetnames,['1800'])
            self.assertEqual(book.active['F3'].value,code)
            self.assertEqual(book.active['C2'].value,1800)
        finally:book.close()
        with fitz.open(stream=documents[0][1],filetype='pdf') as pdf:
            text='\n'.join(p.get_text() for p in pdf)
            compact=''.join(text.split())
            self.assertIn(code,compact);self.assertIn('1800',compact)
        await self.click('review_confirm')
        reviewed=service.get_work_order(number)
        identity=json.loads(next(line.split(': ',1)[1] for line in reviewed['notes'].splitlines() if line.startswith('MANAGER_DOCUMENT_REVIEW: ')))
        self.assertEqual(identity['pdf_sha256'],hashlib.sha256(documents[0][1]).hexdigest())
        self.assertEqual(identity['sha256'],hashlib.sha256(Path(order['excel_path']).read_bytes()).hexdigest())
        self.assertEqual(review.approved_pdf_bytes(reviewed),documents[0][1])
        # Back from staff selection, reopen this exact reviewed order, revise it.
        await self.click('staff_back');await self.click('review_edit')
        s=self.handler.pending[self.key]
        self.assertEqual(s.proposal['revision_machine_code'],code)
        await self.click('edit_excel');await self.choose_oil(code);await self.enter('1600')
        await self.click('edit_apply');await self.click('edit_done');await self.click('confirm')
        revised=self.handler.pending[self.key].order_no
        self.assertNotEqual(revised,number)
        self.assertEqual(service.get_work_order(number)['superseded_by'],revised)
        self.assertEqual(service.get_work_order(revised)['revision_of'],number)
        with self.assertRaisesRegex(ValueError,'منسوخ'):
            review.confirm_document_review(number,self.key[1])
        with self.assertRaisesRegex(ValueError,'منسوخ'):
            delivery.send_work_order(work_order_no=number,sender=None)
        evidence=os.environ.get('KOMATSO_OIL_EVIDENCE')
        if evidence:
            destination=Path(evidence);destination.mkdir(parents=True,exist_ok=True)
            for result in (order,service.get_work_order(revised)):
                for path in [Path(result['excel_path']),Path(result['pdf_path'])]:
                    shutil.copy2(path,destination/path.name)
            (destination/'e2e.json').write_text(json.dumps(dict(success=True,transport='SIMULATED',pdf='NATIVE EXCEL',
                original=number,revision=revised,review_hash_verified=True,old_dispatch_blocked=True),indent=2),encoding='utf-8')

    async def test_pc1250_real_outputs_1600_1800_2000_change_only_cycle_title(self):
        import fitz
        from openpyxl import load_workbook
        from zipfile import ZipFile
        from tools.fleet.work_orders.core import service
        from tools.fleet.work_orders.core.pdf_document import export_staff_pdf
        from tools.fleet.work_orders.types.oil_change.builder import TEMPLATES, action_for
        fixture=OilServiceEditTests('test_repeated_override_retains_original_evidence_for_all_cycles')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        source=TEMPLATES['PC1250-8'][0]
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        original=load_workbook(source)
        self.addCleanup(original.close)
        results=[]
        for interval in (1600,1800,2000):
            order=service.create_work_order(work_order_type='OIL_CHANGE',jalali_date=f'1405/06/{interval//200:02d}',
                shift='روزانه',machine_codes=['EX1252'],item_actions={'EX1252':action_for('PC1250-8',interval)},
                created_by='bale:455740857')
            workbook=load_workbook(order['excel_path'])
            try:
                self.assertEqual(workbook.sheetnames,[str(interval)])
                expected,actual=original[str(interval)],workbook.active
                self.assertEqual(actual['C2'].value,str(interval)+'ساعت')
                self.assertEqual(actual['C2']._style,expected['C2']._style)
                for row in expected:
                    for cell in row:
                        if cell.coordinate not in {'C2','F3','E2','J2'}:
                            self.assertEqual(actual[cell.coordinate].value,cell.value,cell.coordinate)
                        self.assertEqual(actual[cell.coordinate]._style,cell._style,cell.coordinate)
                self.assertEqual(str(actual.merged_cells),str(expected.merged_cells))
            finally:workbook.close()
            with ZipFile(source) as old,ZipFile(order['excel_path']) as new:
                changed=[n for n in old.namelist() if old.read(n)!=new.read(n)]
                self.assertEqual(len(changed),2)
            pdf=await asyncio.to_thread(export_staff_pdf,Path(order['excel_path']))
            with fitz.open(pdf) as document:
                compact=''.join(''.join(p.get_text() for p in document).split())
                self.assertIn('EX1252',compact)
                self.assertIn(str(interval)+'ساعت',compact)
            evidence=os.environ.get('KOMATSO_OIL_EVIDENCE')
            if evidence:
                directory=Path(evidence);directory.mkdir(parents=True,exist_ok=True)
                shutil.copy2(order['excel_path'],directory/Path(order['excel_path']).name)
                shutil.copy2(pdf,directory/pdf.name)
            results.append(dict(interval=interval,excel_sha256=hashlib.sha256(Path(order['excel_path']).read_bytes()).hexdigest(),
                                pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),technical_cells_preserved=True))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
        if os.environ.get('KOMATSO_OIL_EVIDENCE'):
            (Path(os.environ['KOMATSO_OIL_EVIDENCE'])/'pc1250-native.json').write_text(
                json.dumps(dict(success=True,source_unchanged=True,outputs=results),indent=2),encoding='utf-8')


    async def test_legacy_batch_sessions_drop_only_pending_edits_and_reject_old_callbacks(self):
        for stage in ('ADD_CODES','ADD_ACTION','EDIT_CODE','EDIT_BATCH_CODES','EDIT_INTERVAL','EDIT_CONFIRM'):
            s=await self.start(self.combined(47),add_code='800')
            before=deepcopy(s.proposal)
            old=self.button(self.sent[-1],'edit_unit_0')
            s.stage=stage;s.oil_editor_version=1;s.edit_code_page=2
            s.edit_codes=['HD801','HD802'];s.edit_intervals={'HD801':1600,'HD802':2000}
            self.handler._persist()
            rows=self.store.load()
            for _key,value in rows:
                value.update(edit_origin_stage='EDIT_BATCH_CODES',edit_interval_return='EDIT_CONFIRM',
                             edit_confirm_return='EDIT_BATCH_CODES',edit_confirm_page=1)
            self.store.save(rows)
            self.handler=self.new_handler();s=self.handler.pending[self.key]
            self.assertEqual(s.stage,'EDIT_CODE');self.assertEqual(s.edit_code_page,2)
            self.assertEqual(s.proposal,before);self.assertFalse(s.edit_codes)
            self.assertEqual(s.keyboard_revision,'');self.assertEqual(s.oil_editor_version,2)
            self.assertEqual(self.message('',data=old,origin=s.keyboard_message_id)['reason'],'inline-rejected')
            await self.settle()
            await self.enter('افزودن/ویرایش')
            await self.click('edit_unit_32');await self.click('interval_keep');await self.click('edit_apply')
            self.assertEqual([i['machine_code'] for i in s.proposal['items']],['HD800','HD832'])
        self.assertFalse(self.requests)

    async def test_edit_message_failure_replaces_keyboard_without_double_apply(self):
        s=await self.start(self.combined(20))
        await self.click('edit_next');await self.click('edit_unit_16');await self.click('interval_1600')
        old=self.button(self.sent[-1],'edit_apply');origin=len(self.sent)
        from unittest.mock import AsyncMock
        with patch.object(self.gateway.adapters['bale']._bot,'edit_message_text',
                          AsyncMock(side_effect=RuntimeError('simulated Bale edit failure'))):
            await self.click('edit_apply')
        self.assertEqual(s.stage,'EDIT_CODE');self.assertEqual(s.edit_code_page,1)
        self.assertEqual(len(self.sent),origin+1)
        self.assertEqual(s.proposal['service_overrides'],{'HD816':1600})
        before=deepcopy(s.proposal)
        self.assertEqual(self.message('',data=old,origin=origin)['reason'],'inline-rejected')
        await self.settle();self.assertEqual(s.proposal,before)

    async def test_multiple_inline_devices_create_independent_native_excel_pdf(self):
        from tools.bale_ui import StateStore
        from tools.fleet.work_orders.core import service
        from tools.fleet.work_orders.core.pdf_document import export_staff_pdf
        from openpyxl import load_workbook
        import fitz
        fixture=OilServiceEditTests('test_repeated_override_retains_original_evidence_for_all_cycles')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        extra=sample_source('PC600-8','EX601',last=1600,remaining=100)
        fixture.source['machines']+=extra['machines'];fixture.source['plans']+=extra['plans']
        self.db_path=fixture.db_path;self.store=StateStore(self.db_path.parent/'multi-ui.json')
        permission_patch=patch('tools.fleet.work_orders.core.permissions.DB_PATH',self.db_path)
        permission_patch.start();self.addCleanup(permission_patch.stop)
        async def worker(request):
            self.requests.append(deepcopy(request))
            return create_worker.execute_request(request,db_path=self.db_path)
        self.worker=worker;self.handler=self.new_handler()
        s=await self.start(fixture.source)
        for code,interval in (('EX1252',1600),('EX601',1800)):
            await self.choose_oil(code);await self.click(f'interval_{interval}');await self.click('edit_apply')
        self.assertFalse(self.requests)
        await self.click('edit_done');await self.click('confirm')
        self.assertEqual(len(self.requests),1)
        self.assertEqual(self.requests[0]['machine_codes'],['EX1252','EX601'])
        with closing(sqlite3.connect(self.db_path)) as con:
            numbers=[r[0] for r in con.execute('SELECT work_order_no FROM service_work_orders ORDER BY id')]
        self.assertEqual(len(numbers),2)
        evidence=[]
        for number,(code,interval) in zip(numbers,(('EX1252',1600),('EX601',1800))):
            order=service.get_work_order(number)
            self.assertEqual(order['jalali_date'],s.jalali_date)
            self.assertEqual(order['items'][0]['machine_code'],code)
            self.assertTrue(order['items'][0]['action_code'].endswith('_'+str(interval)))
            book=load_workbook(order['excel_path'])
            try:
                self.assertEqual(book.sheetnames,[str(interval)])
                self.assertEqual(book.active['F3'].value,code)
                self.assertIn(str(interval),str(book.active['C2'].value))
            finally:book.close()
            pdf=await asyncio.to_thread(export_staff_pdf,Path(order['excel_path']))
            with fitz.open(pdf) as document:
                text=''.join(''.join(p.get_text() for p in document).split())
                self.assertIn(code,text);self.assertIn(str(interval),text)
                self.assertIn(number,text)
            directory=Path(os.environ.get('KOMATSO_OIL_EVIDENCE',str(self.db_path.parent)))
            directory.mkdir(parents=True,exist_ok=True)
            shutil.copy2(order['excel_path'],directory/Path(order['excel_path']).name)
            shutil.copy2(pdf,directory/pdf.name)
            evidence.append(dict(number=number,code=code,interval=interval,pdf='NATIVE EXCEL'))
        if os.environ.get('KOMATSO_OIL_EVIDENCE'):
            (directory/'multi-device-native.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')


def load_tests(loader, tests, pattern):
    # Reuse the transport fixture without counting inherited baseline tests twice.
    return unittest.TestSuite(OilUnifiedTests(name) for name in OilUnifiedTests.__dict__ if name.startswith('test_'))
