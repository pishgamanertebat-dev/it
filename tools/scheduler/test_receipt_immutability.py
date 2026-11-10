"""Successful/uncertain evidence must survive unexpected repeat callbacks byte-for-byte."""
from datetime import datetime,timezone
import unittest
from .runner import connect,_save_receipt
from . import office_receipts
from .delivery_alert import ensure_schema,_insert_case,_claim,_mark


class ReceiptImmutabilityTests(unittest.TestCase):
    def test_report_terminal_identity_and_timestamp_are_immutable(self):
        c=connect(':memory:');self.addCleanup(c.close);key=('fixture','2026-11-10T05:30:00+00:00')
        for state in ('sent','uncertain'):
            with c:c.execute('DELETE FROM receipts')
            _save_receipt(c,key,'101',state,'original')
            before=tuple(c.execute('SELECT * FROM receipts').fetchone())
            for new in ('pending','failed','sending','sent','uncertain'):
                _save_receipt(c,key,'101',new,'different')
                self.assertEqual(tuple(c.execute('SELECT * FROM receipts').fetchone()),before)

    def test_artifact_terminal_hash_identity_and_timestamp_are_immutable(self):
        c=connect(':memory:');self.addCleanup(c.close);key=('fixture','due')
        for state in ('sent','uncertain'):
            with c:c.execute('DELETE FROM office_artifact_receipts')
            office_receipts.save(c,key,'101','metalwork',state,'original','original-hash')
            before=tuple(c.execute('SELECT * FROM office_artifact_receipts').fetchone())
            for new in ('pending','failed','sending','sent','uncertain'):
                office_receipts.save(c,key,'101','metalwork',new,'different','different-hash')
                self.assertEqual(tuple(c.execute('SELECT * FROM office_artifact_receipts').fetchone()),before)

    def test_manager_terminal_receipt_is_not_overwritten_by_later_batch_failure(self):
        c=connect(':memory:');self.addCleanup(c.close);ensure_schema(c);now=datetime.now(timezone.utc)
        case=_insert_case(c,'fixture','due','event','text',now)
        for state in ('sent','uncertain'):
            with c:c.execute('DELETE FROM delivery_alert_receipt')
            _claim(c,case,'exception',('101',),now);_mark(c,case,'exception','101',state,'original',None,now)
            before=tuple(c.execute('SELECT * FROM delivery_alert_receipt').fetchone())
            _mark(c,case,'exception','101','failed',None,'InterruptedBatch',now)
            self.assertEqual(tuple(c.execute('SELECT * FROM delivery_alert_receipt').fetchone()),before)

if __name__=='__main__':unittest.main()
