"""Report diagnostics preserve categories and locations, never exception text."""
import json
import unittest
from .report_errors import ReportFailure,RecordIdentityMismatch,classify,public_reason
from . import test_delivery_alert as harness


class DiagnosticTests(unittest.TestCase):
    def test_categories_and_secret_redaction(self):
        cases=[(ValueError('secret'),'source','source_invalid'),
               (RuntimeError('secret'),'generation','report_generation'),
               (FileNotFoundError('secret'),'source','source_missing'),
               (PermissionError('secret'),'source','source_permission')]
        for exc,stage,code in cases:
            failure=classify(exc,stage)
            self.assertEqual(failure.code,code)
            self.assertNotIn('secret',failure.serialized())
            self.assertIsNotNone(public_reason(code,failure.serialized()))
        lock=PermissionError('private');lock.winerror=32
        self.assertEqual(classify(lock).code,'source_locked')

    def test_invalid_location_is_not_public(self):
        failure=ReportFailure('source_invalid',sheet='E:/secret',row=-1,column='token')
        self.assertEqual(json.loads(failure.serialized()),{})

    def test_record_identity_conflict_is_specific_and_keeps_column_without_raw_text(self):
        failure=classify(RecordIdentityMismatch('private workbook contents',sheet='711',row=16,column=3),'source')
        self.assertEqual(failure.code,'source_identity_mismatch')
        self.assertEqual(failure.details,{'stage':'source','sheet':'711','row':16,'column':3})
        reason=public_reason(failure.code,failure.serialized())
        for value in ['کد مکانیزم رکورد با کد شیت مطابقت ندارد','شیت 711','ردیف 16','ستون 3']:
            self.assertIn(value,reason)
        self.assertNotIn('private',failure.serialized()+reason)


class OperatorDiagnosticTests(unittest.TestCase):
    setUp=harness.AlertTests.setUp
    arm=harness.AlertTests.arm
    job=harness.AlertTests.job
    add_run=harness.AlertTests.add_run
    add_receipt=harness.AlertTests.add_receipt
    sender=harness.AlertTests.sender

    def test_new_generation_error_is_immediate_precise_and_deduplicated(self):
        from .runner import connect
        from .delivery_alert import scan_delivery_exceptions
        self.arm();self.add_run('retry_wait','pdf_layout',data_state='missing')
        self.add_receipt('A','report','pending');self.add_receipt('B','report','pending')
        c=connect(self.state)
        with c:c.execute('UPDATE runs SET error_detail=?',(ReportFailure('pdf_layout',stage='layout',sheet='461',row=11,column=3).serialized(),))
        for _ in range(2):scan_delivery_exceptions(c,[self.job()],self.moment,self.store,sender_factory=self.sender)
        self.assertEqual(len(self.sent),1)
        text=self.sent[0][1]
        for part in ['تولید گزارش کامل نشد','شیت 461','ردیف 11','ستون 3','تلاش مجدد ادامه دارد']:
            self.assertIn(part,text)
        self.assertNotIn('خطای ارسال در بله',text)
        c.close()


if __name__=='__main__':unittest.main()
