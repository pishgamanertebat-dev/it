"""Isolated transport, approval scope and immutable historical receipts."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.authorization.store import RecipientSetResolution
from .runner import connect
from . import recovery_delivery as recovery
from .tasks import TransportUncertain


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.conn = connect(self.folder/'ledger.sqlite3')
        self.addCleanup(self.conn.close)
        self.pdf = self.folder/'ready.pdf'; self.pdf.write_bytes(b'isolated-pdf')
        self.source = self.folder/'source.xlsx'; self.source.write_bytes(b'isolated-source')
        self.op = dict(operation_id='exact-operation',original_job='maintenance_daily_report',
                       original_due='2026-11-10T08:30:00+00:00',data_date='1405/08/18',recipient_id='397185913',
                       pdf=str(self.pdf),pdf_sha256=recovery.digest(self.pdf),source_snapshot=str(self.source),
                       source_sha256=recovery.digest(self.source),proposed_receipt_schedule_id='manual-exact',caption='approved')
        self.approval = dict(approval_ref='isolated-owner-instruction',operations=[dict(operation_id='exact-operation',
            target_date='1405/08/18',recipient_id='397185913',sha256=self.op['pdf_sha256'])])
        self.store=Mock();self.store.resolve_active_recipients.return_value=RecipientSetResolution('ready',('397185913',))
        self.store.identity_label.return_value='امامی'
        self.sender=Mock();self.sender.document.return_value='test-message'
        with self.conn:
            self.conn.execute("INSERT INTO report_recovery_hold VALUES (?,?, 'held', 'owner_review_required_for_historical_delivery',?,?,?,NULL)",
                ('maintenance_daily_report',self.op['original_due'],self.op['original_due'],self.op['original_due'],'{}'))
        self.original=[tuple(r) for r in self.conn.execute('SELECT * FROM report_recovery_hold')]

    def approve(self):
        with patch.object(recovery,'validate_candidate',return_value={'rows':8}):
            recovery.authorize(self.conn,[self.op],self.approval,self.store,self.folder)

    def test_exact_approval_sends_once_and_retains_original_hold(self):
        self.approve()
        for _ in range(2):
            result=recovery.execute(self.conn,[self.op],self.store,sender_factory=lambda:self.sender)
        self.assertEqual(result[0]['message_id'],'test-message')
        self.sender.document.assert_called_once()
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM report_recovery_hold')],self.original)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM receipts').fetchone()[0],1)

    def test_uncertain_never_retried(self):
        self.approve();self.sender.document.side_effect=TransportUncertain()
        recovery.execute(self.conn,[self.op],self.store,sender_factory=lambda:self.sender)
        result=recovery.execute(self.conn,[self.op],self.store,sender_factory=lambda:self.sender)
        self.assertEqual(result[0]['status'],'uncertain');self.sender.document.assert_called_once()

    def test_sent_original_prevents_duplicate(self):
        self.approve()
        with self.conn:self.conn.execute('INSERT INTO receipts VALUES (?,?,?,\'report\',\'sent\',\'old-message\',?)',
            (self.op['original_job'],self.op['original_due'],self.op['recipient_id'],self.op['original_due']))
        result=recovery.execute(self.conn,[self.op],self.store,sender_factory=lambda:self.sender)
        self.assertEqual(result[0]['status'],'already_sent');self.sender.document.assert_not_called()

    def test_scope_and_real_safety_hold_fail_closed(self):
        self.approval['operations'][0]['target_date']='1405/08/16'
        with self.assertRaises(ValueError):self.approve()
        self.approval['operations'][0]['target_date']='1405/08/18'
        with self.conn:self.conn.execute("UPDATE report_recovery_hold SET reason='unsafe_source_conflict'")
        with self.assertRaises(ValueError):self.approve()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM report_recovery_operation').fetchone()[0],0)

    def test_changed_file_or_revoked_recipient_blocks_transport(self):
        self.approve();self.pdf.write_bytes(b'changed')
        with self.assertRaises(ValueError):recovery.execute(self.conn,[self.op],self.store,sender_factory=lambda:self.sender)
        self.sender.document.assert_not_called()


if __name__=='__main__':unittest.main()
