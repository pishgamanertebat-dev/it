"""Bale 7349, Persian boundaries and AI-only structural guard regression."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import openpyxl

from . import entry_extract as extract, entry_draft as draft, entry_service as entry
from . import entry_ai_bale as ai
from . import test_ai_daily_entry as harness
ACTOR=harness.ACTOR

MESSAGE=json.loads((Path(__file__).parent/'fixtures/message-7349.json').read_text('utf8'))
RAW=MESSAGE['content']


class IntentTests(unittest.TestCase):
    def test_verbatim_7349_and_all_eight_source_clauses(self):
        self.assertEqual(MESSAGE['platform_message_id'],'7349')
        self.assertEqual(hashlib.sha256(RAW.encode()).hexdigest(),MESSAGE['content_sha256'])
        self.assertEqual(extract.intent_decision(RAW),('possible','declared_report'))
        lines=RAW.splitlines()[1:]
        codes=('705','704','717','1003','521','1006','152','S1')
        result=extract.validate(dict(raw_text=RAW,report_date='17آبان 1405',unresolved=[],items=[
            dict(raw_code=code,raw_description=line,source_span=dict(text=line,occurrence=0))
            for code,line in zip(codes,lines)]),RAW)
        self.assertEqual([i['raw_code'] for i in result['items']],list(codes))
        self.assertEqual([i['raw_description'] for i in result['items']],lines)
        for index in range(8):
            incomplete=dict(result,items=[dict(i,source_span={k:v for k,v in i['source_span'].items() if k in ('text','occurrence')})
                                          for n,i in enumerate(result['items']) if n!=index])
            with self.assertRaisesRegex(ValueError,'استخراج ناقص'):extract.validate(incomplete,RAW)
        changed=dict(result,items=[dict(i,source_span={k:v for k,v in i['source_span'].items() if k in ('text','occurrence')}) for i in result['items']])
        changed['items'][5]['raw_description']='تعویض چراغ های روی بوم.'
        with self.assertRaisesRegex(ValueError,'استخراج ناقص'):extract.validate(changed,RAW)

    def test_lamp_words_spacing_punctuation_and_mixed_scripts(self):
        for lamp in ('چراغ','چراغ‌ها','چراغ های','چراغ،','(چراغ)','چراغLED','LEDچراغ','چراغ\u0650'):
            with self.subTest(lamp=lamp):
                self.assertEqual(extract.intent('705 تعمیر '+lamp),'possible')
        for question in ('چرا','چرا،','(چرا)','چگونه','چطور','علت چیست','علت\u200cچیست','WHY','how'):
            with self.subTest(question=question):
                self.assertEqual(extract.intent(question+' 705 چراغ خراب است'),'none')
        self.assertEqual(extract.intent('705 تعمیر سیم\u200cکشی چراغ'),'possible')
        self.assertEqual(extract.intent('705 تعمیر XچراY'),'possible')

    def test_declared_report_question_offers_but_technical_question_does_not(self):
        self.assertEqual(extract.intent('گزارش مکانیکی ۱۷ آبان ۱۴۰۵\n705 تعمیر چراغ؛ چرا دوباره خراب شد؟'),'possible')
        for raw in ('چرا چراغ 705 روشن نمی‌شود؟','روش تعمیر چراغ 705 چیست؟',
                    'چطور گزارش مکانیکی ۱۷ آبان ۱۴۰۵ را ثبت کنم؟','گزارش مکانیکی 705 را مرتب کن',
                    'گزارش مکانیکی ۱۷ آبان ۱۴۰۵ چگونه ثبت می‌شود؟',
                    'علت خرابی موتور 705 چیست','نحوه تعمیر چراغ 705','تعمیر 705 را توضیح بده'):
            self.assertEqual(extract.intent(raw),'none')


class RowGuardTests(unittest.TestCase):
    book=harness.DraftTests.book
    setUp=harness.DraftTests.setUp
    create=harness.DraftTests.create
    edit=harness.DraftTests.edit
    ready=harness.DraftTests.ready
    confirm=harness.DraftTests.confirm
    approved=harness.DraftTests.approved
    save=harness.DraftTests.save

    def test_known_device_without_target_row_blocks_draft_approval(self):
        from .test_ai_daily_entry import extracted
        before=self.source.read_bytes()
        d=self.edit(self.create(extracted([('HD999','تعمیر چراغ','HD999 تعمیر چراغ')])),'section','mechanical')
        self.assertEqual(d['payload']['items'][0]['code'],'HD999')
        self.assertTrue(any('ردیف دستگاه HD999' in s for s in d['payload']['issues']))
        with self.assertRaises(ValueError):self.confirm(d,'TEXT_CONFIRM','confirm_text',1)
        self.assertEqual(before,self.source.read_bytes())

    def test_removed_row_after_approvals_is_not_recreated_under_writer_lock(self):
        d=self.approved(self.create())
        book=openpyxl.load_workbook(self.source);sheet=entry.today_sheet(book,self.kw['day'])
        archive=book.copy_worksheet(sheet);archive.title='known-history';archive['C1']='1405/06/25'
        sheet.delete_rows(entry.find_row(sheet,'HD710',entry.layout(sheet)))
        book.save(self.source);book.close();before=self.source.read_bytes()
        result=self.save(d)
        self.assertEqual(result['status'],'EDIT')
        self.assertTrue(any('ردیف دستگاه' in issue for issue in result['payload']['issues']))
        self.assertFalse(result['results'])
        self.assertEqual(before,self.source.read_bytes())


class OfferTests(unittest.IsolatedAsyncioTestCase):
    book=harness.DraftTests.book
    setUp=harness.DraftTests.setUp
    asyncSetUp=harness.BaleTests.asyncSetUp
    asyncTearDown=harness.BaleTests.asyncTearDown
    handler_factory=harness.BaleTests.handler_factory
    run_worker=harness.BaleTests.run_worker
    event=harness.BaleTests.event
    button=harness.BaleTests.button
    deliver=harness.BaleTests.deliver

    async def test_same_message_offers_for_each_authorized_actor_without_extraction_or_write(self):
        self.handler.authorize=lambda a:a in ('1732374823','654806764','387679249',ACTOR)
        before=self.source.read_bytes()
        for actor in ('1732374823','654806764','387679249',ACTOR):
            self.assertEqual((await self.deliver(self.event(RAW,actor=actor)))['reason'],'daily-ai')
            self.assertEqual(self.handler.sessions[(actor,actor)]['stage'],'OFFER')
            self.assertEqual(self.handler.sessions[(actor,actor)]['raw'],RAW)
            self.assertEqual(self.messages[-1]['reply_markup']['inline_keyboard'][0][0]['text'],'تأیید گزارش')
        self.handler.extractor.assert_not_called()
        self.assertEqual(self.calls,[]);self.assertEqual(before,self.source.read_bytes())

    async def test_routing_diagnostics_have_version_reason_but_no_raw_or_exception_text(self):
        secret='SECRET-DO-NOT-LOG'
        with self.assertLogs(ai.log,level='INFO') as logs:
            await self.deliver(self.event(RAW))
            self.handler.extractor.side_effect=RuntimeError(secret)
            await self.deliver(self.button('build'))
        text='\n'.join(logs.output)
        self.assertIn(ai.HANDLER_VERSION,text);self.assertIn('intent=possible',text)
        self.assertIn('reason=declared_report',text);self.assertIn('reason=extraction_failed',text)
        self.assertNotIn(secret,text);self.assertNotIn('دامپتراک',text)

    async def test_manual_and_work_order_precedence_through_dispatch(self):
        from tools.bale_ui import runtime
        from . import entry_bale, maintenance_bale
        from tools.fleet.work_orders.channels.bale import message_handler
        order=SimpleNamespace(pending={},handle=Mock(return_value=None))
        manual=SimpleNamespace(safe_capture=False,sessions={},active_for=Mock(return_value=True),handle=Mock(return_value={'reason':'manual'}))
        maintenance=SimpleNamespace(active_for=Mock(return_value=False),handle=Mock(return_value=None))
        with patch.object(extract,'enabled',return_value=True),patch.object(ai,'_handler',self.handler), \
             patch.object(entry_bale,'_handler',manual),patch.object(maintenance_bale,'_handler',maintenance), \
             patch.object(message_handler,'_handler',order),patch.object(runtime,'reply_menu_step',return_value=None), \
             patch.object(runtime.router,'handlers',{}),patch.object(runtime.router,'dispatch',return_value=None):
            result=runtime.dispatch(self.event(RAW),self.gateway,send=Mock(),bale_approved=True)
            self.assertEqual(result['reason'],'manual');self.assertFalse(self.handler.sessions)
            manual.handle.return_value=None;manual.active_for.return_value=False
            order.pending[('bale',ACTOR,ACTOR)]=SimpleNamespace(expires=10**12,stage='CODE')
            with self.assertLogs(ai.log,level='INFO') as logs:
                runtime.dispatch(self.event(RAW),self.gateway,send=Mock(),bale_approved=True)
            self.assertIn('reason=work_order_open','\n'.join(logs.output))
            self.assertFalse(self.handler.sessions);self.handler.extractor.assert_not_called()


if __name__=='__main__':unittest.main()
