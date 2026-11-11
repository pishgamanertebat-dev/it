"""Regression cases for consent, atomic merges, delivery history and old sessions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import shutil
import sqlite3
import threading
import unittest
from unittest.mock import patch

import openpyxl
from . import entry_draft as draft, entry_service as entry, entry_extract
from .entry_delivery import delivery_review
from . import test_ai_daily_entry as fixtures
from .test_ai_daily_entry import ACTOR, extracted


class MergeSafetyTests(unittest.TestCase):
    book=fixtures.DraftTests.book
    setUp=fixtures.DraftTests.setUp
    create=fixtures.DraftTests.create
    edit=fixtures.DraftTests.edit
    ready=fixtures.DraftTests.ready
    confirm=fixtures.DraftTests.confirm
    approved=fixtures.DraftTests.approved
    save=fixtures.DraftTests.save

    def change(self,cell,value):
        book=openpyxl.load_workbook(self.source)
        book.worksheets[0][cell]=value
        book.save(self.source);book.close()

    def value(self,cell):
        book=openpyxl.load_workbook(self.source)
        try:return book.worksheets[0][cell].value
        finally:book.close()

    def test_existing_sheet_empty_cell_preserves_other_devices_columns_and_history(self):
        self.change('E4','آهنگری محفوظ');self.change('H3','اطلاعات راننده')
        result=self.save(self.approved(self.create()))
        self.assertEqual(result['status'],'DONE')
        self.assertEqual(self.value('D4'),'تعویض واترپمپ')
        self.assertEqual(self.value('E4'),'آهنگری محفوظ');self.assertEqual(self.value('H3'),'اطلاعات راننده')
        book=openpyxl.load_workbook(self.source)
        self.assertEqual(len(book.worksheets),2);self.assertEqual(book['archive']['D3'].value,'mechanical-old');book.close()

    def test_existing_description_requires_choice_and_explicit_replace_changes_only_target(self):
        self.change('D4','شرح قبلی');self.change('E4','آهنگری محفوظ')
        d=self.edit(self.create(),'section','mechanical')
        self.assertTrue(d['payload']['issues'])
        with self.assertRaises(ValueError):self.confirm(d,'TEXT_CONFIRM','confirm_text',1)
        d=self.edit(d,'mode','replace','HD710')
        d=self.confirm(d,'TEXT_CONFIRM','confirm_text',2)
        self.assertEqual(self.save(d)['status'],'DONE')
        self.assertEqual(self.value('D4'),'تعویض واترپمپ');self.assertEqual(self.value('E4'),'آهنگری محفوظ')

    def test_same_text_different_operation_is_visible_and_never_silently_removed(self):
        self.change('D4','تعویض واترپمپ')
        d=self.edit(self.create(),'section','mechanical')
        self.assertTrue(d['payload']['groups'][0]['duplicate_text']);self.assertTrue(d['payload']['issues'])
        d=self.approved(d);self.save(d)
        self.assertEqual(self.value('D4'),'تعویض واترپمپ\nتعویض واترپمپ')

    def test_many_conflicts_are_individual_and_no_unselected_device_is_dropped(self):
        self.change('D4','قدیمی یک');self.change('D3','قدیمی دو')
        d=self.edit(self.create(extracted([('710','تعمیر','710 تعمیر'),('332','جوش','332 جوش')])),'section','mechanical')
        d=self.edit(d,'mode','append','HD710')
        self.assertNotIn('EX332',d['payload']['modes']);self.assertTrue(d['payload']['issues'])
        with self.assertRaises(ValueError):self.confirm(d,'TEXT_CONFIRM','confirm_text',1)
        d=self.edit(d,'mode','replace','EX332');d=self.confirm(d,'TEXT_CONFIRM','confirm_text',2)
        self.assertEqual(self.save(d)['status'],'DONE')
        self.assertEqual(self.value('D4'),'قدیمی یک\nتعمیر');self.assertEqual(self.value('D3'),'جوش')

    def test_changed_cell_rebases_clears_prior_choice_and_requires_new_text_approval(self):
        self.change('D4','قدیمی')
        d=self.approved(self.create());self.change('D4','متن کاربر دوم')
        result=self.save(d)
        self.assertEqual(result['status'],'EDIT');self.assertIsNone(result['text_ok'])
        self.assertEqual(self.value('D4'),'متن کاربر دوم')
        self.assertEqual(result['payload']['groups'][0]['expected'],'متن کاربر دوم')
        self.assertNotIn('HD710',result['payload']['modes'])
        with self.assertRaises(ValueError):self.save(result)
        self.save(self.approved(result))
        self.assertEqual(self.value('D4'),'متن کاربر دوم\nتعویض واترپمپ')

    def test_two_actual_users_race_same_cell_only_one_commits_under_writer_lock(self):
        config=json.loads(self.config.read_text('utf8'))
        config['department_sections']={'654806764':'mechanical'}
        self.config.write_text(json.dumps(config),'utf8')
        a=self.approved(self.create())
        b=self.approved(self.create(extracted([('710','تعمیر دوم','710 تعمیر دوم')]),actor='654806764'))
        barrier=threading.Barrier(2)
        def write(d):barrier.wait(timeout=10);return self.save(d)
        with ThreadPoolExecutor(2) as pool:
            futures=[pool.submit(write,d) for d in (a,b)]
            results=[f.result(timeout=40) for f in futures]
        self.assertEqual(sorted(r['status'] for r in results),['DONE','EDIT'])
        self.assertIn(self.value('D4'),('تعویض واترپمپ','تعمیر دوم'))
        with closing(entry.open_audit(self.state)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM edits WHERE status='succeeded'").fetchone()[0],1)

    def test_unknown_identity_and_missing_existing_row_never_create_rows(self):
        original=self.source.read_bytes()
        for code in ('UNKNOWN','999'):
            d=self.edit(self.create(extracted([(code,'تعمیر',code+' تعمیر')])),'section','mechanical')
            self.assertTrue(d['payload']['issues'])
            with self.assertRaises(ValueError):self.confirm(d,'TEXT_CONFIRM','confirm_text',1)
        self.assertEqual(original,self.source.read_bytes())

    def test_changed_machine_identity_requires_new_preview_and_does_not_write(self):
        d=self.approved(self.create());self.change('B4','نام تازه دستگاه')
        original=self.source.read_bytes();result=self.save(d)
        self.assertEqual(original,self.source.read_bytes());self.assertEqual(result['status'],'EDIT')
        self.assertIsNone(result['text_ok']);self.assertIn('هویت دستگاه',result['payload']['notice'])

    def test_failed_save_rolls_back_entire_batch_and_no_success_is_returned(self):
        d=self.approved(self.create(extracted([('710','تعمیر','710 تعمیر'),('332','جوش','332 جوش')])))
        original=self.source.read_bytes()
        with patch.object(entry,'replace_source',side_effect=OSError('locked')):result=self.save(d)
        self.assertEqual(result['status'],'EDIT');self.assertFalse(result['results'])
        self.assertEqual(original,self.source.read_bytes())
        with closing(entry.open_audit(self.state)) as db:
            self.assertEqual(db.execute('SELECT status FROM edits').fetchone()[0],'not_applied')

    def test_process_loss_after_replace_is_recovered_on_resume_without_second_write(self):
        d=self.approved(self.create());real=entry.replace_source
        def cut(*a):real(*a);raise SystemExit('process died')
        with patch.object(entry,'replace_source',side_effect=cut):
            with self.assertRaises(SystemExit):self.save(d)
        published=self.source.read_bytes()
        with patch.object(entry,'replace_source',side_effect=AssertionError('recovery must not write')):
            recovered=draft.recover(self.store.load(d['id'],ACTOR,ACTOR),**self.kw)
        self.assertEqual(recovered['status'],'DONE');self.assertEqual(published,self.source.read_bytes())

    def test_external_atomic_change_during_save_is_preserved_by_snapshot_guard(self):
        d=self.approved(self.create())
        other=self.root/'external.xlsx';shutil.copy2(self.source,other)
        book=openpyxl.load_workbook(other);book.worksheets[0]['D4']='ویرایش بیرونی';book.save(other);book.close()
        external=other.read_bytes();save=openpyxl.workbook.workbook.Workbook.save
        def change_during_save(book,output):
            save(book,output)
            entry.replace_source(other,self.source)
        with patch.object(openpyxl.workbook.workbook.Workbook,'save',change_during_save):
            result=self.save(d)
        self.assertEqual(self.source.read_bytes(),external)
        self.assertEqual(result['status'],'PARTIAL')
        self.assertFalse(result['results'])
        with patch.object(entry,'replace_source',side_effect=AssertionError('uncertain operation cannot retry')):
            recovered=draft.recover(result,**self.kw)
        self.assertEqual(recovered['status'],'PARTIAL')

    def test_legacy_text_ok_session_is_migrated_to_fresh_review_without_writing(self):
        d=self.ready(self.create());original=self.source.read_bytes()
        d['payload'].pop('workflow');d['payload'].pop('batch_operation')
        from .entry_draft_store import content_hash
        d['hash']=content_hash(d['payload']);d['status']='TEXT_OK';d['text_ok']={'message':'old'}
        self.store.update(d,'legacy_fixture')
        with patch.object(entry_extract,'enabled',return_value=True):
            resumed=draft.execute(dict(action='ai_resume',actor=ACTOR,chat=ACTOR,draft=d['id']),**self.kw)
        self.assertEqual(resumed['status'],'EDIT');self.assertIsNone(resumed['text_ok'])
        self.assertEqual(resumed['payload']['workflow'],'two_step_v1');self.assertEqual(original,self.source.read_bytes())

    def test_legacy_approval_binding_cannot_be_used_as_new_write_authority(self):
        d=self.ready(self.create());self.store.bind(d,'TEXT_CONFIRM','old','1')
        with self.assertRaises(ValueError):self.store.consume(d['id'],ACTOR,ACTOR,'TEXT_CONFIRM','old','1','confirm_text')
        self.store.bind(d,'WRITE_CONFIRM','legacy','2')
        with self.assertRaises(ValueError):self.store.consume(d['id'],ACTOR,ACTOR,'WRITE_CONFIRM','legacy','2','confirm_write')

    def test_batch_journal_is_replayed_by_existing_sync_and_second_merge_is_unchanged(self):
        from .sync_service import initialize,merge_snapshot
        mine=self.root/'mine.xlsx';shutil.copy2(self.source,mine)
        sync_kw=dict(config=self.config,runtime=self.state)
        initialize(mine,**sync_kw)
        self.save(self.approved(self.create(extracted([('710','تعمیر','710 تعمیر'),('332','جوش','332 جوش')]))))
        book=openpyxl.load_workbook(mine);book.worksheets[0]['H3']='داده جدید معدن';book.save(mine);book.close()
        merge_snapshot(mine,self.source,**sync_kw)
        self.assertEqual(self.value('D4'),'تعمیر');self.assertEqual(self.value('D3'),'جوش')
        self.assertEqual(self.value('H3'),'داده جدید معدن')
        self.assertEqual(merge_snapshot(mine,self.source,**sync_kw)['status'],'unchanged')

    def test_section_change_does_not_inherit_previous_cell_replacement_choice(self):
        self.change('D4','مکانیکی قدیم');self.change('E4','آهنگری قدیم')
        d=self.edit(self.create(),'section','mechanical');d=self.edit(d,'mode','replace','HD710')
        d=self.edit(d,'section','metalwork')
        self.assertNotIn('HD710',d['payload']['modes']);self.assertTrue(d['payload']['issues'])

    def test_sent_pdf_ledger_is_immutable_and_independent_correction_is_recorded(self):
        ledger=self.root/'receipts.sqlite3'
        with closing(sqlite3.connect(ledger)) as db, db:
            db.execute('CREATE TABLE runs(schedule_id TEXT,due TEXT,task TEXT)')
            db.execute('CREATE TABLE office_artifact_receipts(schedule_id TEXT,due TEXT,recipient_id TEXT,artifact_id TEXT,status TEXT,message_id TEXT,sha256 TEXT)')
            db.execute("INSERT INTO runs VALUES ('driver_daily_mechanical','2026-09-18T09:00:00+03:30','mechanical_driver_daily')")
            db.execute("INSERT INTO office_artifact_receipts VALUES ('driver_daily_mechanical','2026-09-18T09:00:00+03:30','manager','mechanical','sent','123','pdf-hash')")
        original=ledger.read_bytes()
        self.assertEqual(delivery_review('1405/06/26','mechanical',ledger)['status'],'correction_required')
        config=json.loads(self.config.read_text('utf8'));config['delivery_receipts']=str(ledger)
        self.config.write_text(json.dumps(config),'utf8')
        self.assertEqual(self.save(self.approved(self.create()))['status'],'DONE')
        self.assertEqual(ledger.read_bytes(),original)
        with closing(entry.open_audit(self.state)) as db:
            self.assertEqual(db.execute('SELECT status FROM entry_correction_reviews').fetchone()[0],'needs_review')


class TwoStepBaleTests(unittest.IsolatedAsyncioTestCase):
    book=fixtures.BaleTests.book
    setUp=fixtures.BaleTests.setUp
    asyncSetUp=fixtures.BaleTests.asyncSetUp
    asyncTearDown=fixtures.BaleTests.asyncTearDown
    handler_factory=fixtures.BaleTests.handler_factory
    run_worker=fixtures.BaleTests.run_worker
    event=fixtures.BaleTests.event
    button=fixtures.BaleTests.button
    deliver=fixtures.BaleTests.deliver
    start=fixtures.BaleTests.start

    def labels(self):
        return [b['text'] for row in self.messages[-1].get('reply_markup',{}).get('inline_keyboard',[]) for b in row]

    async def test_explicit_request_also_requires_report_confirmation_and_only_two_start_buttons(self):
        await self.deliver(self.event('ثبت کن 710 تعویض واترپمپ'))
        self.assertEqual(self.labels(),['تأیید گزارش','انصراف'])
        self.handler.extractor.assert_not_called()
        self.assertFalse(any(c['action']=='ai_commit' for c in self.calls))

    async def test_ready_review_only_has_three_main_buttons_then_direct_write_receipt(self):
        await self.start()
        self.assertEqual(self.labels(),['تأیید متن','اصلاح گزارش','انصراف'])
        await self.deliver(self.button('confirm_text'))
        self.assertIn('گزارش ثبت شد.',self.messages[-1]['text']);self.assertEqual(self.labels(),[])
        self.assertEqual(sum(c['action']=='ai_commit' for c in self.calls),1)

    async def test_cancel_at_offer_and_review_prevents_write_and_stale_confirmation(self):
        original=self.source.read_bytes()
        await self.deliver(self.event('710 تعویض واترپمپ'));await self.deliver(self.button('cancel'))
        self.assertNotIn(self.key,self.handler.sessions)
        await self.start();old=self.button('confirm_text');await self.deliver(self.button('cancel'));await self.deliver(old)
        self.assertNotIn(self.key,self.handler.sessions);self.assertEqual(original,self.source.read_bytes())

    async def test_old_final_and_pause_callbacks_redirect_to_safe_review_without_write(self):
        await self.start();original=self.source.read_bytes()
        for action in ('confirm_write','pause'):
            await self.deliver(self.button(action))
            self.assertIn('اصلاح گزارش',self.labels())
            self.assertNotIn('ثبت نهایی',' '.join(self.labels()))
        self.assertEqual(original,self.source.read_bytes());self.assertFalse(any(c['action']=='ai_commit' for c in self.calls))

    async def test_write_failure_shows_review_with_error_and_no_success_message(self):
        await self.start();original=self.source.read_bytes()
        with patch.object(entry,'replace_source',side_effect=OSError('locked')):
            await self.deliver(self.button('confirm_text'))
        self.assertNotIn('گزارش ثبت شد.',self.messages[-1]['text']);self.assertIn('ثبت انجام نشد',self.messages[-1]['text'])
        self.assertEqual(original,self.source.read_bytes());self.assertIn('تأیید متن',self.labels())

    async def test_edit_text_returns_to_same_review_and_fresh_confirmation(self):
        await self.start();old=self.button('confirm_text')
        await self.deliver(self.button('edit'));await self.deliver(self.button('item_0'));await self.deliver(self.button('description'))
        message=self.handler.sessions[self.key]['message_id']
        await self.deliver(self.event('تعمیر تازه',reply=message))
        await self.deliver(old)
        self.assertFalse(any(c['action']=='ai_commit' for c in self.calls))
        self.assertIn('تعمیر تازه',self.messages[-1]['text'])
        await self.deliver(self.button('confirm_text'));self.assertIn('گزارش ثبت شد.',self.messages[-1]['text'])

    async def test_restart_offer_retains_raw_report_and_requires_report_confirmation(self):
        await self.deliver(self.event('710 تعویض واترپمپ'))
        for timer in self.handler.lifecycle.timers.values():timer.cancel()
        self.handler=self.handler_factory();await self.deliver(self.event('ادامه گزارش'))
        self.assertEqual(self.labels(),['تأیید گزارش','انصراف']);self.handler.extractor.assert_not_called()

    async def test_conflict_list_shows_only_conflicting_devices_and_choices_are_per_device(self):
        book=openpyxl.load_workbook(self.source);book.worksheets[0]['D4']='قدیمی';book.save(self.source);book.close()
        self.handler.extractor.return_value=extracted([('710','تعمیر','710 تعمیر'),('332','جوش','332 جوش')])
        await self.start();self.assertNotIn('تأیید متن',self.labels())
        await self.deliver(self.button('conflicts'))
        self.assertTrue(any('HD710' in label for label in self.labels()))
        self.assertFalse(any('EX332' in label for label in self.labels()))
        await self.deliver(self.button('mode_0'))
        self.assertIn('افزودن به شرح قبلی',self.labels());self.assertIn('جایگزینی شرح قبلی',self.labels())
        await self.deliver(self.button('append_0'))
        self.assertEqual(self.labels(),['تأیید متن','اصلاح گزارش','انصراف'])
        d=self.handler.current(self.key)
        self.assertEqual(d['payload']['modes'],{'HD710':'append'});self.assertEqual(len(d['payload']['groups']),2)


if __name__=='__main__':unittest.main()
