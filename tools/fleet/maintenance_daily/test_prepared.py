"""Offline publication, exact-day readiness, journal proof and hash safety."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from . import prepared as p,report as m,delivery
from .test_report import workbook,package,DATE
from tools.authorization.store import RecipientSetResolution
from tools.scheduler.report_errors import ReportFailure


class PreparedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch=tempfile.TemporaryDirectory();cls.folder=Path(cls.scratch.name)
        book,cls.layout=workbook([('801',[[DATE,801,'مکانیک','تعویض فیلتر','فیلتر']])])
        cls.data=package(book);book.close()
        cls.report=m.scan(cls.data,DATE,layout=cls.layout)
        pdf=cls.folder/'reference.pdf';validation=m.render(cls.report,pdf)
        cls.result=dict(status='ready',report=cls.report,pdf=str(pdf),validation=validation)

    @classmethod
    def tearDownClass(cls):cls.scratch.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cache=Path(self.temp.name)/'cache';self.work=Path(self.temp.name)

    def pin(self):return p.pin(self.data,self.result,{'journal_version':'isolated'},cache=self.cache)

    def test_prepared_0830_survives_0858_source_outage_and_sends_at_due(self):
        self.pin();sender=Mock();sender.document.return_value='isolated-message'
        store=Mock();store.resolve_active_recipients.return_value=RecipientSetResolution('ready',('101',))
        with patch.object(m,'source_snapshot',side_effect=AssertionError('No live source after ARTIFACT_READY')),\
             patch.object(delivery,'build',side_effect=lambda date,directory:p.prepare(date,directory,cache=self.cache)),\
             patch('tools.scheduler.tasks.BaleSender',return_value=sender):
            result=delivery.deliver(None,{'date':DATE},authorization=store)
        self.assertEqual(result['sent_count'],1);sender.document.assert_called_once()

    def test_closed_sent_and_uncertain_do_not_upload_again(self):
        ready=self.pin();sender=Mock();store=Mock()
        store.resolve_active_recipients.return_value=RecipientSetResolution('ready',('101','202'))
        with patch.object(delivery,'build',return_value=ready),patch('tools.scheduler.tasks.BaleSender',return_value=sender):
            delivery.deliver(None,{'date':DATE},authorization=store,closed={'sent':{'101'},'uncertain':{'202'}})
        sender.document.assert_not_called()

    def test_missing_message_id_is_uncertain_and_never_claimed_sent(self):
        ready=self.pin();sender=Mock();sender.document.return_value=None
        store=Mock();store.resolve_active_recipients.return_value=RecipientSetResolution('ready',('101',))
        receipts=[]
        with patch.object(delivery,'build',return_value=ready),patch('tools.scheduler.tasks.BaleSender',return_value=sender):
            result=delivery.deliver(None,{'date':DATE},authorization=store,on_receipt=lambda *v:receipts.append(v))
        self.assertEqual(result['uncertain_recipients'],['101']);self.assertEqual(result['sent_count'],0)
        self.assertEqual(receipts[-1][1],'uncertain')

    def test_pinned_snapshot_and_pdf_tampering_fail_before_sender(self):
        ready=self.pin();pdf=Path(ready['pdf']);pdf.write_bytes(b'tampered')
        with self.assertRaises(ReportFailure) as exc:p.load(DATE,cache=self.cache)
        self.assertEqual(exc.exception.code,'pdf_validation')

    def test_source_outage_without_valid_artifact_is_not_date_missing(self):
        @contextmanager
        def offline(reader):raise FileNotFoundError();yield
        with patch.object(m,'source_snapshot',offline):
            with self.assertRaises(ReportFailure) as exc:p.prepare(DATE,self.work,cache=self.cache)
        self.assertEqual(exc.exception.code,'source_missing')

    def test_local_missing_date_with_stale_mine_is_technical(self):
        empty=dict(self.report,devices=[],rows_matched=0,devices_matched=0)
        @contextmanager
        def local(reader):yield self.data,p.sha(self.data)
        def stale(digest):raise ReportFailure('source_stale',stage='source')
        with patch.object(m,'source_snapshot',local),patch.object(m,'scan',return_value=empty):
            with self.assertRaises(ReportFailure) as exc:
                p.prepare(DATE,self.work,cache=self.cache,proof_reader=lambda d:{},freshness_reader=stale)
        self.assertEqual(exc.exception.code,'source_stale')

    def test_true_missing_requires_recent_exact_file_sync(self):
        state=self.work/'sync.json';now=datetime.now(timezone.utc)
        value=dict(outage=False,last_run=dict(source_enumerated=True,finished_at=now.isoformat(),
                   maintenance_guard=dict(status='unchanged',hash='exact-hash')))
        state.write_text(json.dumps(value),'utf8')
        self.assertEqual(p.confirm_missing_source('exact-hash',state_path=state,now=now)['state'],'DATA_NOT_READY')
        for digest,clock in [('wrong-hash',now),('exact-hash',now+timedelta(minutes=6))]:
            with self.assertRaises(ReportFailure):p.confirm_missing_source(digest,state_path=state,now=clock)

    def test_source_version_is_frozen_and_does_not_silently_replace_ready_pdf(self):
        ready=self.pin();original=Path(ready['pdf']).read_bytes()
        with patch.object(m,'source_snapshot',side_effect=AssertionError('New source requires correction review')):
            later=p.prepare(DATE,self.work,cache=self.cache)
        self.assertEqual(Path(later['pdf']).read_bytes(),original)
        self.assertIn('correction',later['manifest']['source_change_policy'])


class JournalProofTests(unittest.TestCase):
    from tools.fleet.repairs.test_maintenance_baseline import MaintenanceBaselineTests as Harness
    setUp=Harness.setUp
    setup_baseline=Harness.setup_baseline
    request=Harness.request
    save=Harness.save
    sync=Harness.sync
    change_mine=Harness.change_mine

    def test_real_approved_journal_then_sync_version_and_unjournalled_change(self):
        (self.root/'journal').mkdir(exist_ok=True)
        self.setup_baseline(legacy=False)
        runtime=self.root/'journal'
        initial=p.source_proof(p.sha(self.source.read_bytes()),runtime=runtime,source=self.source)
        self.change_mine();self.sync()
        imported=p.source_proof(p.sha(self.source.read_bytes()),runtime=runtime,source=self.source)
        self.assertNotEqual(initial['journal_version'],imported['journal_version'])
        with self.assertRaises(ReportFailure):p.source_proof('0'*64,runtime=runtime,source=self.source)


if __name__=='__main__':unittest.main()
