"""Restart fault injection: disposable SQLite/Excel, captured manager transport only."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tools.fleet.work_orders.channels.bale import create_worker, message_handler
from tools.fleet.work_orders.channels.bale.message_handler import FormSession, WorkOrderMenuHandler
from tools.fleet.work_orders.core import db, delivery, operational, service
from tools.fleet.work_orders.core.review import confirm_document_review, approved_pdf_bytes
from tools.fleet.work_orders.dev import test_oil_p1_safety as shared
from tools.fleet.oil_change.test_proposal import sample_source


class RestartRecoveryTests(unittest.IsolatedAsyncioTestCase):
    setUp = shared.OilSafetyTests.setUp
    create = shared.OilSafetyTests.create
    read = shared.OilSafetyTests.read

    def request(self, three=False):
        if three:
            for code in ('HD709', 'HD710'):
                extra = sample_source('HD785-7', code, last=200)
                self.fixture.source['machines'] += extra['machines']
                self.fixture.source['plans'] += extra['plans']
        request=self.fixture.request()
        request['machine_codes']=list(request['item_actions'])
        return request

    def crash(self, stage, request):
        # Kill only this child, never an operational Gateway or Excel process.
        script = '''
import json, os, sqlite3, sys
from pathlib import Path
from unittest.mock import patch
from tools.fleet.work_orders.core import db, delivery, operational, service
from tools.fleet.work_orders.channels.bale import create_worker
p=json.loads(sys.stdin.read())
db.DB_PATH=Path(p['db']); service.WORK_ORDER_OUTPUT_ROOT=Path(p['output'])
operational.operational_today=lambda:p['today']
if p['stage']=='before_record':
    builder=service._load_builder('OIL_CHANGE')
    builder.build_document=lambda **kw:os._exit(71)
    service.create_work_order(work_order_type='OIL_CHANGE',jalali_date=p['today'],
        shift='روزانه',machine_codes=['EX1252'],created_by='bale:455740857',
        item_actions={'EX1252':'OIL_CHANGE_PC1250-8_1800'})
with patch('tools.fleet.oil_change.source.source_hash',return_value='fixture'), \
     patch('tools.fleet.oil_change.source.read_source',return_value=p['source']), \
     patch('tools.fleet.oil_change.proposal.read_source',return_value=p['source']):
    result=create_worker.execute_request(p['request'],db_path=db.DB_PATH)
    assert result['ok'],result
if p['stage']=='after_excel':os._exit(72)
def interrupted(snapshot):
    Path(snapshot).with_suffix('.pdf').write_bytes(b'%PDF-1.7\\nINCOMPLETE')
    os._exit(73)
delivery.export_staff_pdf=interrupted
delivery.prepare_manager_pdf(result['orders'][0]['work_order_no'],'455740857')
'''
        payload = dict(stage=stage, db=str(self.db_path), output=str(self.fixture.output_root),
                       today=self.fixture.test_today, source=self.fixture.source, request=request)
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script],
                                input=json.dumps(payload), text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, {'before_record':71,'after_excel':72,'during_pdf':73}[stage], result.stderr)

    def test_crash_before_record_rolls_back_then_creates_once(self):
        req=self.request(); self.crash('before_record',req)
        self.assertEqual(self.read('SELECT id FROM service_work_orders'),[])
        number=self.create(req)
        self.assertEqual(self.create(req),number)
        self.assertEqual(len(self.read('SELECT id FROM service_work_orders')),1)

    async def test_crash_after_excel_and_during_pdf_resumes_three_original_numbers(self):
        req=self.request(three=True)
        self.crash('after_excel',req)
        before=service.get_work_order(self.read('SELECT work_order_no FROM service_work_orders')[0]['work_order_no'])
        self.crash('during_pdf',req)
        rows=self.read('SELECT * FROM service_work_orders ORDER BY id')
        self.assertEqual(len(rows),3)
        self.assertTrue(all(r['status']=='FILE_READY' and not r['pdf_path'] for r in rows))
        self.assertFalse(Path(before['excel_path']).with_suffix('.pdf').exists())
        hashes={r['work_order_no']:Path(r['excel_path']).read_bytes() for r in rows}
        uploads=[]
        async def upload(**kw):
            uploads.append(kw['document'].read())
        bot=SimpleNamespace(send_document=upload,send_message=AsyncMock(return_value=SimpleNamespace(message_id='TEST')))
        gateway=SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=bot)})
        async def worker(request):return create_worker.execute_request(request,db_path=self.db_path)
        def render(snapshot):
            import fitz
            pdf=Path(snapshot).with_suffix('.pdf')
            with fitz.open() as document:
                document.new_page(); document.save(pdf)
            return pdf
        handler=WorkOrderMenuHandler(db_path=self.db_path,worker=worker)
        key=('bale',shared.MANAGER,shared.MANAGER)
        session=FormSession(work_order_type='OIL_CHANGE',expires=handler.clock()+600)
        handler.pending[key]=session
        with patch.object(delivery,'export_staff_pdf',side_effect=render):
            await handler._run_request(key,session,req,gateway,lambda *a:None)
        self.assertEqual(len(uploads),3)
        self.assertEqual(set(hashes),{k[-1] for k in handler.reviews})
        for number,excel in hashes.items():
            row=service.get_work_order(number)
            self.assertEqual(Path(row['excel_path']).read_bytes(),excel)
            self.assertEqual(row['send_attempts'],0)
            confirm_document_review(number,shared.MANAGER)
            self.assertIn(approved_pdf_bytes(service.get_work_order(number)),uploads)
        self.assertEqual(len(self.read('SELECT id FROM service_work_orders')),3)
        self.assertEqual(self.read('SELECT * FROM service_work_order_delivery_attempts'),[])

    def test_conflict_exposes_other_existing_drafts_and_both_intervals(self):
        req=self.request(three=True)
        original=create_worker.execute_request(req,db_path=self.db_path)
        self.assertTrue(original['ok'],original)
        changed=deepcopy(req)
        changed['item_actions']['EX1252']='OIL_CHANGE_PC1250-8_1600'
        changed['proposal']['service_overrides']['EX1252']=1600
        rows=self.read('SELECT * FROM service_work_orders ORDER BY id')
        result=create_worker.execute_request(changed,db_path=self.db_path)
        self.assertFalse(result['ok'],result)
        self.assertEqual({o['work_order_no'] for o in result.get('orders',[])},
                         {o['work_order_no'] for o in original['orders'] if 'EX1252' not in o['item_summary']})
        for value in ('1800','1600',original['orders'][-1]['work_order_no'],'ویرایش'):
            self.assertIn(value,result['message'])
        self.assertEqual(self.read('SELECT * FROM service_work_orders ORDER BY id'),rows)

    async def test_controlled_drain_counts_request_until_completion_and_times_out(self):
        started=asyncio.Event(); release=asyncio.Event()
        async def worker(request):
            started.set(); await release.wait()
            return {'ok':False,'message':'TEST STOP'}
        tracked={}
        def track(future, agent):
            tracked[future]=agent
            future.add_done_callback(lambda f:tracked.pop(f,None))
        gateway=SimpleNamespace(_track_deferred_agent_worker=track,adapters={})
        handler=WorkOrderMenuHandler(db_path=self.db_path,worker=worker,document_sender=AsyncMock())
        key=('bale',shared.MANAGER,shared.MANAGER)
        session=FormSession(expires=handler.clock()+600)
        handler.pending[key]=session
        handler._start_request(key,session,{'action':'create'},gateway,lambda *a:None)
        await started.wait()
        try:
            self.assertEqual(len(tracked),1,'Gateway drain cannot see the work-order task')
            pending=list(tracked)
            done,remaining=await asyncio.wait(pending,timeout=0.02)
            self.assertFalse(done);self.assertEqual(len(remaining),1)
            release.set(); await asyncio.gather(*pending)
            await asyncio.sleep(0)
            self.assertEqual(tracked,{})
        finally:
            release.set()
            if handler.tasks:await asyncio.gather(*list(handler.tasks))

    async def test_cancelled_pdf_waiter_keeps_real_thread_visible_and_uploads_nothing(self):
        number=self.create(); order=create_worker.preview_order(service.get_work_order(number))
        started=threading.Event(); release=threading.Event(); tracked={}
        def track(future,agent):
            tracked[future]=agent;future.add_done_callback(lambda f:tracked.pop(f,None))
        def render(*args):
            started.set();release.wait(5)
            return Path('TEST.pdf'),b'%PDF-1.7\nTEST\n%%EOF'
        upload=AsyncMock()
        gateway=SimpleNamespace(_track_deferred_agent_worker=track,
            adapters={'bale':SimpleNamespace(_bot=SimpleNamespace(send_document=upload))})
        with patch.object(delivery,'prepare_manager_pdf',side_effect=render):
            waiter=asyncio.create_task(message_handler.send_manager_excel(gateway,shared.MANAGER,order))
            try:
                for _ in range(100):
                    if started.is_set():break
                    await asyncio.sleep(.01)
                self.assertTrue(started.is_set())
                waiter.cancel()
                with self.assertRaises(asyncio.CancelledError):await waiter
                self.assertEqual(sum(not f.done() for f in tracked),1)
                upload.assert_not_called()
            finally:
                release.set()
                if tracked:await asyncio.gather(*list(tracked))
        await asyncio.sleep(0)
        self.assertEqual(tracked,{})

    def test_incomplete_pdf_is_not_published_or_reviewable(self):
        number=self.create();row=service.get_work_order(number)
        def incomplete(snapshot):
            pdf=Path(snapshot).with_suffix('.pdf');pdf.write_bytes(b'%PDF-1.7\nINCOMPLETE');return pdf
        with patch.object(delivery,'export_staff_pdf',side_effect=incomplete):
            with self.assertRaises(ValueError):delivery.prepare_manager_pdf(number,shared.MANAGER)
        self.assertFalse(Path(row['excel_path']).with_suffix('.pdf').exists())
        self.assertIsNone(service.get_work_order(number)['pdf_path'])

    def test_valid_preview_reuses_bytes_and_changed_pdf_requires_new_review(self):
        number=self.create()
        def render(snapshot):
            import fitz
            pdf=Path(snapshot).with_suffix('.pdf')
            with fitz.open() as document:
                document.new_page();document.save(pdf)
            return pdf
        with patch.object(delivery,'export_staff_pdf',side_effect=render) as exporter:
            path,data=delivery.prepare_manager_pdf(number,shared.MANAGER)
            delivery.store_manager_pdf(number,path,pdf_bytes=data)
            self.assertEqual(delivery.prepare_manager_pdf(number,shared.MANAGER),(path,data))
            self.assertEqual(exporter.call_count,1)
            confirm_document_review(number,shared.MANAGER)
            path.write_bytes(b'%PDF-1.7\nINCOMPLETE')
            new_path,new_data=delivery.prepare_manager_pdf(number,shared.MANAGER)
            self.assertEqual(exporter.call_count,2)
            delivery.store_manager_pdf(number,new_path,pdf_bytes=new_data)
            from tools.fleet.work_orders.core.review import DocumentReviewRequired
            with self.assertRaises(DocumentReviewRequired):approved_pdf_bytes(service.get_work_order(number))
            confirm_document_review(number,shared.MANAGER)
            self.assertEqual(approved_pdf_bytes(service.get_work_order(number)),new_data)


if __name__=='__main__':unittest.main()
