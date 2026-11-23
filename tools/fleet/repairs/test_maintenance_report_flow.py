from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import openpyxl

from . import entry_service, maintenance_identity as identity, maintenance_report as legacy
from . import maintenance_report_flow as flow, maintenance_report_store as store
from .maintenance_baseline import digest, initialize, journal_branch
from .maintenance_report_ui import card

ACTOR = '1732374823'
DAY = '1405/08/27'
ROOT = Path(__file__).resolve().parents[3]/'runtime'


class PartialReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        runtime = root/'journal'
        runtime.mkdir()
        self.source = root/'local.xlsx'
        self.upstream = root/'upstream.xlsx'
        book = openpyxl.Workbook()
        book.remove(book.active)
        for title, name in [('710','دامپتراک 710'),('801','بیل 801'),('603','لودر 603'),('متفرقه','متفرقه')]:
            sheet = book.create_sheet(title)
            sheet['A1'] = name
            code_label = 'قسمت' if title == 'متفرقه' else 'کد مکانیزم'
            sheet.append(['تاریخ','ثبت کننده','نام مکانیک',code_label,'نوع خرابی','قطعات مصرفی'])
            sheet.column_dimensions['E'].width = 70
        identity.enable(book)
        book.save(self.source)
        book.close()
        shutil.copyfile(self.source,self.upstream)
        config, policy = root/'entry.json', root/'reports.json'
        config.write_text(json.dumps(dict(source=str(self.source),allowed_users=[ACTOR])),'utf8')
        policy.write_text(json.dumps(dict(enabled=True,reporters={ACTOR:dict(scope='all',
            mechanic='اکیپ مکانیکی',department='تعمیرگاه',partial_confirmation=True)})),'utf8')
        self.kwargs = dict(runtime=runtime,entry_config=config,config=policy,day=DAY,ledger=root/'absent.db',
                           now=lambda:self.clock)
        self.clock, self.origin = 100, 200
        self.initial = self.source.read_bytes()
        with closing(entry_service.open_audit(runtime)):
            pass
        initialize(self.upstream,expected_hash=digest(self.initial),decision='private fixture',
                   approved_by='project-owner',config=config,runtime=runtime)
        auth = patch('tools.fleet.repairs.maintenance_permission.approved_identity',return_value='محسن کرمعلی')
        self.auth = auth.start()
        self.addCleanup(auth.stop)

    def process(self, action, *, actor=ACTOR, **fields):
        return flow.process(action,actor,actor,**fields,**self.kwargs)

    def prepare(self, lines, source_id='bale:1'):
        raw = 'گزارش مکانیکی '+DAY+'\n'+'\n'.join(lines)
        return self.process('prepare',source_id=source_id,raw=raw),raw

    def bind(self, result):
        self.origin += 1
        fields = dict(report_id=result['id'],token=result['view']['token'],origin=str(self.origin))
        self.process('bind',**fields)
        return fields

    def confirm(self, result, number=None):
        review = self.process('approve',**self.bind(result),number=number)
        self.assertEqual(review['view']['phase'],'text')
        return self.process('confirm',**self.bind(review))

    def audit(self):
        return closing(entry_service.open_audit(self.kwargs['runtime']))

    def test_nine_of_ten_and_restart_late_correction(self):
        lines = [f'-دامپتراک 710: تعمیر قطعه {index}' for index in range(1,11)]
        lines[3] = '-لودر 600: تعویض تسمه'
        offered, raw = self.prepare(lines)
        self.assertEqual([i['status'] for i in offered['items']],['VALID']*3+['PENDING_CORRECTION']+['VALID']*6)
        self.assertIn('۹ مورد',card(offered)[0])
        self.assertEqual(self.source.read_bytes(),self.initial)
        offer = self.bind(offered)
        with self.assertRaisesRegex(ValueError,'منقضی'):
            self.process('confirm',**offer)
        review = self.process('approve',**offer)
        self.assertEqual(self.source.read_bytes(),self.initial)
        text = self.bind(review)
        with self.assertRaisesRegex(ValueError,'منقضی'):
            self.process('confirm',**(text|dict(origin='999')))
        saved = self.process('confirm',**text)
        self.assertEqual(saved['committed'],9)
        self.assertIn('۱ مورد باقی مانده',card(saved)[0])
        partial = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError,'منقضی'):
            self.process('confirm',**text)
        self.assertEqual(self.process('prepare',source_id='bale:1',raw=raw),dict(duplicate='bale:1'))
        self.assertEqual(self.process('prepare',source_id='bale:2',raw=raw),dict(duplicate='bale:1'))
        with self.assertRaisesRegex(ValueError,'نسخه‌ها'):
            self.process('prepare',source_id='bale:1',raw=raw+' تغییر')
        self.assertEqual(self.source.read_bytes(),partial)

        self.clock += flow.TTL+1
        self.kwargs['day'] = '1405/09/20'
        current = self.process('status',source_id='bale:1')
        self.assertEqual(current['items'][3]['status'],'PENDING_CORRECTION')
        prompt = self.process('correct',**self.bind(current),number=4)
        prompt_id = self.bind(prompt)['origin']
        new = self.process('reply',origin=prompt_id,reply_id='199',raw='603')
        self.assertEqual(new['items'][3]['record']['date'],DAY)
        final = self.confirm(new,number=4)
        self.assertEqual(final['committed'],1)
        self.assertTrue(all(i['status']=='COMMITTED' for i in final['items']))
        with self.assertRaisesRegex(ValueError,'منقضی'):
            self.process('reply',origin=prompt_id,reply_id='199',raw='603')
        with self.audit() as db:
            proof = journal_branch(db,self.source,self.source.read_bytes(),self.kwargs['runtime'])
            writes = [json.loads(row[0]) for row in db.execute("SELECT result FROM edits WHERE status='succeeded'")]
        self.assertEqual([len(r['records']) for r in writes],[9,1])
        self.assertEqual(len(proof[3]),2)
        self.assertFalse(set(writes[0]['item_ids']) & set(writes[1]['item_ids']))
        with closing(openpyxl.load_workbook(self.source)) as book:
            self.assertEqual(len(identity.inventory(book)),10)
            self.assertEqual(book['603']['E3'].value,'-لودر 603: تعویض تسمه')
            self.assertEqual(book['710'].max_row,11)

    def test_independent_validation_and_duplicate_item(self):
        offer,_ = self.prepare(['-بیل 801: تعمیر', '-بیل 801: تعمیر', '-بیل 801 و لودر 603: تعویض',
                                '-بیل 801:', '-لودر بدون کد تعویض تسمه',
                                '-لودر 801: تعویض', '-دامپتراک 710: '+('ک'*1750), '-تعمیر ابزار کارگاه'])
        self.assertEqual([i['status'] for i in offer['items']],
                         ['VALID']+['PENDING_CORRECTION']*6+['VALID'])
        self.assertEqual(self.confirm(offer)['committed'],2)
        with closing(openpyxl.load_workbook(self.source)) as book:
            self.assertEqual(book['801'].max_row,3)
            self.assertEqual(book['710'].max_row,2)
            self.assertEqual(book['متفرقه']['D3'].value,'تعمیرگاه')

    def test_expired_button_is_read_only_and_new_status_recovers(self):
        offer,_ = self.prepare(['-بیل 801: تعمیر'])
        bound = self.bind(offer)
        self.clock += flow.TTL+1
        with self.assertRaisesRegex(ValueError,'منقضی'):
            self.process('approve',**bound)
        self.assertEqual(self.source.read_bytes(),self.initial)
        self.assertEqual(self.confirm(self.process('status',source_id='bale:1'))['committed'],1)

    def test_wrapped_items_keep_their_own_sheet_and_original_span(self):
        raw = ('گزارش مکانیکی '+DAY+'\r\n-دامپتراک 710:\r\n تعمیر پمپ\r\n'
               '-بستن جک بیل \r\n801 تعمیرگاه\r\nلودر 603: آزمون')
        items = flow.parse(raw, DAY, independent=True)['items']
        self.assertEqual([item['code'] for item in items], ['710', '801', '603'])
        self.assertEqual([item['device_labels'] for item in items], [['دامپتراک'], ['بیل'], ['لودر']])
        for item in items:
            self.assertIsNone(item['issue'])
            self.assertEqual(raw[item['span']['start']:item['span']['end']], item['raw'])
        self.assertEqual(len(flow.parse(raw, DAY)['items']), 5)
        made = self.process('prepare', source_id='bale:7', raw=raw)
        self.assertEqual(self.confirm(made)['committed'], 3)
        another = raw.replace('آزمون', 'سرویس')
        checked = self.process('prepare', source_id='bale:8', raw=another)
        self.assertEqual([item['status'] for item in checked['items']],
                         ['PENDING_CORRECTION', 'PENDING_CORRECTION', 'VALID'])

    def test_exact_historical_admission_preserves_receipt_and_requires_new_callbacks(self):
        sid = 'bale:9'
        raw = 'گزارش مکانیکی '+DAY+'\n-بیل 801: تعمیر\n-لودر 600: تعمیر'
        operation = store.operation_id(ACTOR, ACTOR, sid)
        sha = store.remember(self.kwargs['runtime'], operation, ACTOR, ACTOR, sid,
                             raw, status='needs_review')
        self.assertEqual(self.process('status', source_id=sid), {'missing': True})
        file = self.kwargs['config']
        settings = json.loads(file.read_text('utf8'))
        approval = {'raw_sha256': '0'*64, 'date': DAY}
        settings['reporters'][ACTOR]['historical_recovery'] = {sid: approval}
        file.write_text(json.dumps(settings), 'utf8')
        with self.assertRaisesRegex(ValueError, 'تطبیق ندارد'):
            self.process('status', source_id=sid)
        approval['raw_sha256'] = sha
        file.write_text(json.dumps(settings), 'utf8')
        self.assertEqual(self.process('prepare', source_id=sid, raw=raw), {'legacy': 'needs_review'})
        report = self.process('status', source_id=sid)
        self.assertEqual([i['status'] for i in report['items']], ['VALID', 'PENDING_CORRECTION'])
        offer = self.bind(report)
        with self.assertRaisesRegex(ValueError, 'منقضی'):
            self.process('confirm', **offer)
        review = self.process('approve', **offer)
        self.assertEqual(self.source.read_bytes(), self.initial)
        self.assertEqual(self.process('confirm', **self.bind(review))['committed'], 1)
        with self.audit() as db:
            self.assertEqual(db.execute('SELECT status FROM maintenance_report_messages').fetchone()[0],
                             'needs_review')
            request = json.loads(db.execute('SELECT request FROM edits').fetchone()[0])
        self.assertEqual(request['historical_recovery'], approval)
        self.assertEqual(request['source_id'], sid)

    def test_second_callback_concurrent_delivery_writes_once(self):
        offer,_ = self.prepare(['-بیل 801: تعمیر'])
        review = self.process('approve',**self.bind(offer))
        fields = self.bind(review)

        def confirm():
            try:
                return self.process('confirm',**fields)['committed']
            except ValueError as exc:
                self.assertIn('منقضی',str(exc))
                return 0

        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(lambda _:confirm(),range(2))),[0,1])
        with self.audit() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM edits').fetchone()[0],1)

    def test_late_item_conflict_keeps_other_approved_row(self):
        offer,_ = self.prepare(['-بیل 801: تعمیر','-دامپتراک 710: تعمیر'])
        review = self.process('approve',**self.bind(offer))
        fields = self.bind(review)
        check = flow._check_item
        def changed(book,parsed,*args):
            if parsed['code']=='801':
                raise entry_service.EntryError('این بند برای همین تاریخ و دستگاه تکراری است.')
            return check(book,parsed,*args)

        with patch.object(flow,'_check_item',side_effect=changed):
            saved = self.process('confirm',**fields)
        self.assertEqual(saved['committed'],1)
        self.assertEqual([item['status'] for item in saved['items']],['PENDING_CORRECTION','COMMITTED'])
        with self.audit() as db:
            row = db.execute('SELECT request,result FROM edits').fetchone()
            request,result = map(json.loads,row)
            legacy.verify_operation(request,result)
            self.assertEqual(len(result['rejected_items']),1)

    def test_item_layout_failure_does_not_block_other_sheet(self):
        with closing(openpyxl.load_workbook(self.source)) as book:
            book['801']['E2'] = 'ستون ناشناخته'
            parsed = flow.parse('گزارش مکانیکی '+DAY+'\n-بیل 801: تعمیر\n-دامپتراک 710: تعمیر',
                                DAY,independent=True)
            items = [dict(parsed=item) for item in parsed['items']]
            flow._classify(book,parsed,dict(recorder='محسن کرمعلی',mechanic='اکیپ مکانیکی',department='تعمیرگاه'),items)
        self.assertEqual([item['status'] for item in items],['PENDING_CORRECTION','VALID'])

    def test_permission_revocation_inside_writer_and_legacy_bypass(self):
        offer,raw = self.prepare(['-بیل 801: تعمیر'])
        options = {key:value for key,value in self.kwargs.items() if key!='now'}
        with self.assertRaises(PermissionError):
            legacy.receive(ACTOR,ACTOR,'bale:2',raw,**options)
        review = self.process('approve',**self.bind(offer))
        fields = self.bind(review)
        self.auth.side_effect = ['محسن کرمعلی']*4+[PermissionError('revoked')]
        with self.assertRaisesRegex(PermissionError,'revoked'):
            self.process('confirm',**fields)
        self.assertEqual(self.source.read_bytes(),self.initial)
        self.auth.side_effect = None
        self.assertEqual(self.confirm(self.process('status'))['committed'],1)

    def test_writer_atomic_failure_and_fresh_approval(self):
        offer,_ = self.prepare(['-بیل 801: تعمیر','-دامپتراک 710: تعمیر'])
        with patch.object(entry_service,'replace_source',side_effect=OSError('write failed')):
            with self.assertRaises(ValueError):
                self.confirm(offer)
        self.assertEqual(self.source.read_bytes(),self.initial)
        with self.audit() as db:
            self.assertEqual(db.execute('SELECT status FROM edits').fetchone()[0],'prepared')
        fresh = self.process('status')
        self.assertEqual(self.source.read_bytes(),self.initial)
        self.assertEqual(self.confirm(fresh)['committed'],2)
        with self.audit() as db:
            self.assertEqual([row[0] for row in db.execute('SELECT status FROM edits')],['not_applied','succeeded'])

    def test_prepared_recovery_after_replace_does_not_reapply(self):
        offer,_ = self.prepare(['-بیل 801: تعمیر'])
        replace = entry_service.replace_source
        def interrupted(*args):
            replace(*args)
            raise RuntimeError('process stopped after replace')
        with patch.object(entry_service,'replace_source',side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.confirm(offer)
        written = self.source.read_bytes()
        self.assertNotEqual(written,self.initial)
        self.assertEqual(self.process('status')['committed'],1)
        self.assertIsNone(self.process('status')['committed'])
        self.assertEqual(self.source.read_bytes(),written)
        with self.audit() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM edits WHERE status='succeeded'").fetchone()[0],1)

    def test_invalid_candidate_cannot_publish(self):
        offer,_ = self.prepare(['-بیل 801: تعمیر'])
        with patch.object(flow,'_candidate_cells',side_effect=entry_service.EntryError('readback mismatch')):
            with self.assertRaisesRegex(ValueError,'readback mismatch'):
                self.confirm(offer)
        self.assertEqual(self.source.read_bytes(),self.initial)
        with self.audit() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM edits').fetchone()[0],0)

    def test_unreadable_or_unproven_workbook_is_global(self):
        self.source.write_bytes(b'invalid')
        with self.assertRaisesRegex(ValueError,'دفتر ثبت'):
            self.prepare(['-بیل 801: تعمیر','-لودر 600: تعمیر'])
        with closing(sqlite3.connect(self.kwargs['runtime']/'audit.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM maintenance_report_workflows').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM maintenance_report_messages').fetchone()[0],1)
