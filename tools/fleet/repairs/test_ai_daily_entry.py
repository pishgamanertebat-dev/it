"""Isolated two-step entry, guarded atomic Writer and fake transport acceptance."""
import asyncio
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import openpyxl

from tools.bale_ui import StateStore
from . import entry_draft as draft, entry_extract as extract, entry_service as entry
from .entry_draft_store import DraftStore
from .entry_ai_bale import DailyAIHandler
from . import test_entry_service as fixtures

ACTOR='641220453'


def extracted(clauses, date=None, unresolved=None):
    raw='، '.join(quote for code,desc,quote in clauses)+((' تاریخ '+date) if date else '')
    return extract.validate(dict(raw_text=raw,report_date=date,items=[
        dict(raw_code=code,raw_description=desc,source_span=dict(text=quote,occurrence=0))
        for code,desc,quote in clauses],unresolved=unresolved or []),raw)


class DraftTests(unittest.TestCase):
    book=fixtures.EntryServiceTests.book

    def setUp(self):
        fixtures.EntryServiceTests.setUp(self)
        # AI uses existing target rows. Manual fixtures retain their own new-day contract.
        book=openpyxl.load_workbook(self.source)
        sheet=book.worksheets[0];sheet['C1']='تاریخ: 1405/06/26'
        for row in (3,4):
            sheet.cell(row,4,None).value=None;sheet.cell(row,5,None).value=None
        book.save(self.source);book.close()
        self.state=self.root/'state';self.store=DraftStore(self.state)
        self.kw=dict(self.kw,runtime=self.state)
        self.sample=extracted([('710','تعویض واترپمپ','710 تعویض واترپمپ')])

    def create(self,sample=None,actor=ACTOR):
        return draft.create(actor,actor,sample or self.sample,**self.kw)

    def edit(self,d,field,value=None,index=None):
        return draft.edit(d,field,value,index=index,**self.kw)

    def ready(self,d):
        if d['payload']['section'] is None:d=self.edit(d,'section','mechanical')
        for g in d['payload']['groups']:
            if g['expected']:d=self.edit(d,'mode','append',g['code'])
        self.assertEqual(d['payload']['issues'],[])
        return d

    def confirm(self,d,stage,action,message):
        token='token'+str(message)
        self.store.bind(d,stage,token,str(message),write_intent=stage=='TEXT_CONFIRM')
        return self.store.consume(d['id'],d['actor'],d['chat'],stage,token,str(message),action)

    def approved(self,d):
        d=self.ready(d)
        return self.confirm(d,'TEXT_CONFIRM','confirm_text',101)

    def save(self,d,**kw):return draft.commit(d['actor'],d['chat'],d['id'],**(self.kw|kw))

    def test_explicit_text_and_write_authorization_and_journal_based_success(self):
        original=self.source.read_bytes();d=self.ready(self.create())
        self.assertEqual(original,self.source.read_bytes())
        with self.assertRaises(ValueError):self.save(d)
        d=self.confirm(d,'TEXT_CONFIRM','confirm_text',1);saved=self.save(d)
        self.assertEqual(saved['status'],'DONE')
        book=openpyxl.load_workbook(self.source)
        self.assertEqual(book.worksheets[0]['D4'].value,'تعویض واترپمپ');book.close()
        with closing(entry.open_audit(self.state)) as db:
            self.assertEqual(db.execute("SELECT status FROM edits").fetchone()[0],'succeeded')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM entry_draft_events WHERE event='callback'").fetchone()[0],1)

    def test_unresolved_kept_and_explicitly_excluded_and_two_works_same_machine(self):
        sample=extracted([('710','تعویض واترپمپ','710 تعویض واترپمپ'),('710','باز کردن موتور','710 باز کردن موتور'),
                          (None,'تعویض لاکپشتی جلو','تعویض لاکپشتی جلو'),('718','تعویض جنت افتر','718 تعویض جنت افتر')],
                          unresolved=[dict(item=2,field='code',question='کدام دستگاه؟'),dict(item=3,field='description',question='منظور از جنت افتر چیست؟')])
        d=self.edit(self.create(sample),'section','mechanical')
        self.assertEqual(len(d['payload']['items']),4)
        self.assertEqual(d['payload']['groups'][0]['description'],'تعویض واترپمپ\nباز کردن موتور')
        self.assertTrue(d['payload']['issues'])
        with self.assertRaises(ValueError):self.confirm(d,'TEXT_CONFIRM','confirm_text',1)
        d=self.edit(d,'exclude',index=2);d=self.edit(d,'exclude',index=3)
        self.assertEqual(d['payload']['issues'],[])
        self.assertEqual(d['payload']['extraction']['raw_text'],sample['raw_text'])

    def test_all_four_actors_sections_and_hedayati_explicit_selection(self):
        from tools.authorization.net import repairs_sections
        config=json.loads(self.config.read_text('utf8'))
        config['department_sections']={'1732374823':'mechanical','654806764':'mechanical','387679249':'metalwork'}
        self.config.write_text(json.dumps(config),'utf8')
        for actor,expected in [('1732374823',('mechanical',)),('654806764',('mechanical',)),('387679249',('metalwork',)),(ACTOR,('mechanical','metalwork'))]:
            self.assertEqual(repairs_sections(actor,config),expected)
            d=self.create(actor=actor)
            if len(expected)==2:self.assertIsNone(d['payload']['section'])
            else:
                self.assertEqual(d['payload']['section'],expected[0])
                with self.assertRaises(PermissionError):self.edit(d,'section',({'mechanical','metalwork'}-set(expected)).pop())
        with self.assertRaises(PermissionError):self.create(actor='455740857')

    def test_dates_today_seventh_eighth_future_missing_and_preserved_raw(self):
        book=openpyxl.load_workbook(self.source);sheet=book.copy_worksheet(book.worksheets[0]);sheet.title='old';sheet['C1']='1405/06/19';book.save(self.source);book.close()
        for date,valid in [('1405/06/26',True),('1405/06/19',True),('1405/06/18',False),('1405/06/27',False),('1405/06/20',False)]:
            with self.subTest(date=date):
                d=self.create(extracted([('710','تعمیر','710 تعمیر')],date=date));d=self.edit(d,'section','mechanical')
                self.assertEqual(d['payload']['date'],date)
                # Existing cell additionally requires an explicit mode.
                if valid:
                    d=self.ready(d);self.assertFalse(d['payload']['issues'])
                else:self.assertTrue(d['payload']['issues'])
        self.assertEqual(draft.report_date('۱۸ آبان','1405/08/18'),'1405/08/18')
        self.assertEqual(draft.report_date('۱۳ آبان','1405/08/18'),'1405/08/13')

    def test_append_replace_full_description_contract_and_1800_limit(self):
        d=self.approved(self.create());self.save(d)
        d=self.ready(self.create());d=self.edit(d,'mode','append','HD710')
        d=self.approved(d);self.save(d)
        with closing(entry.open_audit(self.state)) as db:
            request=json.loads(db.execute('SELECT request FROM edits ORDER BY created DESC LIMIT 1').fetchone()[0])
            request=request['entries'][0]
            self.assertEqual(request['description'],'تعویض واترپمپ\nتعویض واترپمپ')
            self.assertEqual(request['entered_description'],'تعویض واترپمپ')
        d=self.create();d=self.edit(d,'section','mechanical');d=self.edit(d,'description','x'*1800,index=0);d=self.edit(d,'mode','append','HD710')
        self.assertTrue(d['payload']['issues'])
        d=self.edit(d,'mode','replace','HD710');self.assertFalse(d['payload']['issues'])

    def test_edits_invalidate_both_confirmations_and_old_callbacks(self):
        d=self.ready(self.create())
        self.store.bind(d,'WRITE_CONFIRM','old',2)
        d=self.edit(d,'description','اصلاح کامل',index=0)
        self.assertIsNone(d['text_ok']);self.assertIsNone(d['write_ok'])
        with self.assertRaises(ValueError):self.store.consume(d['id'],ACTOR,ACTOR,'WRITE_CONFIRM','old',2,'confirm_write')
        with self.assertRaises(ValueError):self.save(d)

    def test_wrong_actor_chat_message_stage_and_replayed_callback_denied(self):
        d=self.ready(self.create());self.store.bind(d,'TEXT_CONFIRM','t',1,write_intent=True)
        for actor,chat,stage,message in [('1732374823','1732374823','TEXT_CONFIRM',1),(ACTOR,'other','TEXT_CONFIRM',1),(ACTOR,ACTOR,'WRITE_CONFIRM',1),(ACTOR,ACTOR,'TEXT_CONFIRM',9)]:
            with self.assertRaises(ValueError):self.store.consume(d['id'],actor,chat,stage,'t',message,'confirm_text')
        self.store.consume(d['id'],ACTOR,ACTOR,'TEXT_CONFIRM','t',1,'confirm_text')
        with self.assertRaises(ValueError):self.store.consume(d['id'],ACTOR,ACTOR,'TEXT_CONFIRM','t',1,'confirm_text')

    def test_resume_uses_durable_approved_version_not_model_memory(self):
        d=self.approved(self.create());restarted=DraftStore(self.state)
        self.assertEqual(restarted.latest(ACTOR,ACTOR)['hash'],d['hash'])
        saved=self.save(d);before=self.source.read_bytes();self.save(saved)
        self.assertEqual(before,self.source.read_bytes())

    def test_cut_after_replace_recovers_atomic_batch_from_journal_without_rewriting(self):
        d=self.approved(self.create(extracted([('710','تعمیر','710 تعمیر'),('332','جوش','332 جوش')])))
        real=entry.replace_source;count=0
        def cut(*args):
            nonlocal count
            count+=1
            if count==1:
                real(*args);raise RuntimeError('cut after replace before receipt')
            raise OSError('locked second device')
        with patch.object(entry,'replace_source',side_effect=cut):partial=self.save(d)
        self.assertEqual(partial['status'],'DONE');self.assertEqual(len(partial['results']),2)
        saved=self.save(partial);self.assertEqual(saved['status'],'DONE')
        with closing(entry.open_audit(self.state)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM edits WHERE status='succeeded'").fetchone()[0],1)
        self.assertEqual([g['operation'] for g in saved['payload']['groups']],[g['operation'] for g in d['payload']['groups']])

    def test_second_device_failure_does_not_publish_first_device(self):
        d=self.approved(self.create(extracted([('710','تعمیر','710 تعمیر'),('332','جوش','332 جوش')])))
        real=entry.set_description;original=self.source.read_bytes()
        def fail(book,selected,*args,**kw):
            if selected['code']=='EX332':raise entry.EntryError('locked')
            return real(book,selected,*args,**kw)
        with patch.object(entry,'set_description',side_effect=fail):result=self.save(d)
        self.assertEqual(result['status'],'EDIT');self.assertFalse(result['results'])
        self.assertEqual(self.source.read_bytes(),original)
        with self.assertRaises(ValueError):self.save(result)
        result=self.approved(result)
        self.assertEqual(self.save(result)['status'],'DONE')

    def test_revoke_and_date_expiry_under_lock_and_before_replace(self):
        d=self.approved(self.create());original=self.source.read_bytes()
        with patch.object(entry,'permission',side_effect=PermissionError('revoked')):
            with self.assertRaises(PermissionError):self.save(d)
        self.assertEqual(original,self.source.read_bytes())
        with self.assertRaises(ValueError):self.save(d,day='1405/07/04')
        self.assertEqual(original,self.source.read_bytes())

    def test_approval_rechecked_after_save_before_atomic_replace(self):
        d=self.approved(self.create());original=self.source.read_bytes()
        save=openpyxl.workbook.workbook.Workbook.save
        def revoke(book,output):
            save(book,output)
            changed=self.store.load(d['id'],ACTOR,ACTOR);changed['text_ok']=None
            self.store.update(changed,'test_revoke_confirmation')
        with patch.object(openpyxl.workbook.workbook.Workbook,'save',revoke),patch.object(entry,'replace_source') as replace:
            result=self.save(d)
            self.assertEqual(result['status'],'EDIT');replace.assert_not_called()
        self.assertEqual(original,self.source.read_bytes())
        self.assertIsNone(self.store.load(d['id'],ACTOR,ACTOR)['text_ok'])
        with self.assertRaises(ValueError):self.save(d)


class ExtractionTests(unittest.TestCase):
    def test_gateway_ai_import_needs_no_excel_or_model_packages(self):
        script='''
import importlib.abc,sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in ('openpyxl','jdatetime','openai','anthropic'):
            raise AssertionError('Forbidden gateway dependency: '+fullname)
sys.meta_path.insert(0,Block())
from tools.fleet.repairs.entry_ai_bale import DailyAIHandler
'''
        result=subprocess.run([sys.executable,'-B','-X','utf8','-c',script],cwd=entry.ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_missing_model_date_is_an_unresolved_item_never_silent_today(self):
        raw='710 تعمیر، تاریخ ۱۳ آبان'
        result=extract.validate(dict(raw_text=raw,report_date=None,items=[dict(raw_code='710',raw_description='تعمیر',source_span=dict(text='710 تعمیر',occurrence=0))],unresolved=[]),raw)
        self.assertTrue(any(i['field']=='date' for i in result['unresolved']))

    def test_intent_explicit_optional_question_sorting(self):
        for text in ('ثبت کن 707 تعویض واترپمپ','برای ثبت: ۷۰۷ تعمیر موتور'):
            self.assertEqual(extract.intent(text),'explicit')
        self.assertEqual(extract.intent('707 تعویض واترپمپ'),'possible')
        for text in ('چرا واترپمپ 707 خراب شده؟','چطور گزارش را ثبت کنم؟','فقط متن 707 تعمیر موتور را مرتب کن','سلام','/new'):
            self.assertEqual(extract.intent(text),'none')

    def test_strict_schema_rejects_privileged_fields_and_fabricated_provenance(self):
        valid=extracted([('710','تعمیر','710 تعمیر')]);raw=valid['raw_text']
        valid['items'][0]['source_span']={k:v for k,v in valid['items'][0]['source_span'].items() if k in ('text','occurrence')}
        for field in ('actor','permission','path','sheet','operation','sql','confirmed'):
            with self.assertRaises(ValueError):extract.validate(dict(valid,**{field:'model'}),raw)
        bad=copy.deepcopy(valid);bad['items'][0]['raw_code']='718'
        with self.assertRaises(ValueError):extract.validate(bad,raw)


class BaleTests(unittest.IsolatedAsyncioTestCase):
    # Reuse only the fixture helpers, not synchronous acceptance tests.
    book=DraftTests.book
    setUp=DraftTests.setUp
    async def asyncSetUp(self):
        self.messages=[];self.callbacks=0;self.calls=[]
        async def send_message(**kw):
            self.messages.append(kw);return SimpleNamespace(message_id=len(self.messages))
        self.bot=SimpleNamespace(send_message=AsyncMock(side_effect=send_message),edit_message_reply_markup=AsyncMock(),delete_message=AsyncMock())
        self.gateway=SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=self.bot)})
        self.telegram=patch.dict('sys.modules',{'telegram':SimpleNamespace(InlineKeyboardMarkup=SimpleNamespace(de_json=lambda value,bot:value))});self.telegram.start();self.addCleanup(self.telegram.stop)
        self.cache=StateStore(self.root/'form.json');self.handler=self.handler_factory()
        self.key=(ACTOR,ACTOR)

    def handler_factory(self):
        return DailyAIHandler(drafts=self.store,state_store=self.cache,run=self.run_worker,
             extractor=AsyncMock(return_value=self.sample),authorize=lambda a:a==ACTOR,sections=lambda a:('mechanical','metalwork'))

    async def run_worker(self,payload):
        self.calls.append(payload)
        try:
            with patch.object(extract,'enabled',return_value=True):
                result=({'dates':draft.entry_dates.choices(self.kw['day'])} if payload['action']=='dates' else draft.execute(payload,**self.kw))
            return dict(ok=True,result=result)
        except (ValueError,PermissionError) as exc:return dict(ok=False,message=str(exc))

    async def asyncTearDown(self):
        for timer in self.handler.lifecycle.timers.values():timer.cancel()

    def event(self,text='',raw=None,actor=ACTOR,reply=None):
        self.callbacks+=1
        return SimpleNamespace(text=text,raw_message=raw,message_id=str(self.callbacks),reply_to_message_id=reply,
            source=SimpleNamespace(platform='bale',chat_type='dm',user_id=actor,chat_id=actor))

    def button(self,action,actor=ACTOR):
        s=self.handler.sessions[self.key]
        return self.event(raw=dict(bale_inline_callback=True,data=f"ik:daily_ai:{s['revision']}:{action}",origin_message_id=s['message_id']),actor=actor)

    async def deliver(self,event):
        result=self.handler.handle(event,self.gateway,send=lambda g,c,t:self.messages.append(dict(text=t)))
        await asyncio.gather(*list(self.handler.tasks));return result

    async def start(self):
        await self.deliver(self.event('ثبت کن 710 تعویض واترپمپ'))
        await self.deliver(self.button('build'))
        await self.deliver(self.button('edit'));await self.deliver(self.button('sections'));await self.deliver(self.button('mechanical'))

    async def test_confirm_text_writes_directly_free_yes_and_stale_buttons_do_not_write(self):
        original=self.source.read_bytes();await self.start()
        self.assertEqual(self.handler.sessions[self.key]['stage'],'TEXT_CONFIRM')
        old=self.button('confirm_text')
        await self.deliver(self.event('بله'))
        self.assertEqual(original,self.source.read_bytes())
        await self.deliver(old)
        self.assertIn('گزارش ثبت شد.',self.messages[-1]['text'])
        self.assertNotIn(self.key,self.handler.sessions)
        await self.deliver(self.button('confirm_write') if self.key in self.handler.sessions else old)
        self.assertEqual(sum(c['action']=='ai_commit' for c in self.calls),1)

    async def test_possible_report_offer_and_unrelated_chat_no_capture(self):
        await self.deliver(self.event('710 تعویض واترپمپ'))
        self.assertEqual(self.handler.sessions[self.key]['stage'],'OFFER');self.handler.extractor.assert_not_called()
        await self.deliver(self.event('چرا موتور خراب است؟'));self.handler.extractor.assert_not_called()
        await self.deliver(self.button('build'))
        await self.deliver(self.button('edit'));await self.deliver(self.button('item_0'));await self.deliver(self.button('description'))
        d=self.handler.current(self.key);original=d['payload']['items'][0]['description']
        await self.deliver(self.event('این پیام گفتگو است'))
        self.assertEqual(self.handler.current(self.key)['payload']['items'][0]['description'],original)
        s=self.handler.sessions[self.key]
        await self.deliver(self.event('متن اصلاح شده',reply=s['message_id']))
        self.assertEqual(self.handler.current(self.key)['payload']['items'][0]['description'],'متن اصلاح شده')

    async def test_restart_pause_resume_and_callback_from_previous_instance(self):
        await self.start();old=self.button('confirm_text');d=self.handler.current(self.key)
        for timer in self.handler.lifecycle.timers.values():timer.cancel()
        self.handler=self.handler_factory();await self.deliver(old)
        self.assertIsNone(self.handler.current(self.key)['text_ok'])
        await self.deliver(self.event('ادامه پیش‌نویس'));self.assertEqual(self.handler.current(self.key)['id'],d['id'])
        await self.deliver(self.event('توقف پیش‌نویس'));await self.deliver(self.event('/new'))
        self.assertEqual(self.handler.current(self.key)['payload']['extraction'],d['payload']['extraction'])
        self.assertEqual(self.handler.current(self.key)['payload']['items'],d['payload']['items'])
        self.assertFalse(any(c['action']=='ai_commit' for c in self.calls))


if __name__=='__main__':unittest.main()
