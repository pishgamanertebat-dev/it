"""Full scheduler/receipt rehearsal: synthetic users and fake Bale only."""
import unittest
from unittest.mock import patch
from .test_office_delivery import OfficeDeliveryTests
from .report_errors import ReportFailure


class DriverContentGateTests(OfficeDeliveryTests):
    def incomplete(self,date,directory,**kwargs):
        self.assertTrue(kwargs['allow_partial'])
        result=self.driver(date,directory,**kwargs)
        result['documents']=result['documents'][:1]
        result['artifacts']={'mechanical':result['documents'][0]}
        result['failures']={'metalwork':ReportFailure('driver_pdf_content',stage='validation',
            sheet='sheet-17',date=date,section='metalwork',sheet_number=17,defect='description_mismatch',row=37,column=5)}
        return result

    def test_bad_metalwork_no_send_no_retry_mechanical_each_recipient_once(self):
        with patch('integrations.hermes.function_domain.driver_report.build_driver_pdf',side_effect=self.incomplete) as builder:
            self.run_at();self.run_at(61);self.run_at(901)
        self.assertEqual(builder.call_count,1)
        self.assertEqual([(u,a) for u,a,d,c in self.calls],[('101','mechanical'),('202','mechanical')])
        states={(r['recipient_id'],r['artifact_id']):r['status'] for r in self.rows('office_artifact_receipts')}
        self.assertEqual(states,{('101','mechanical'):'sent',('101','metalwork'):'failed',
                                 ('202','mechanical'):'sent',('202','metalwork'):'failed'})
        run=self.rows('runs')[0]
        self.assertEqual(run['status'],'failed');self.assertEqual(run['error'],'driver_pdf_content')
        self.assertIsNone(run['next_attempt_at']);self.assertIn('description_mismatch',run['error_detail'])

    def test_existing_receipts_are_closed_and_never_reset(self):
        self.run_at();before=self.rows('office_artifact_receipts');self.calls=[]
        self.run_at(61);self.run_at(901)
        self.assertEqual(self.rows('office_artifact_receipts'),before)
        self.assertEqual(self.calls,[])

    def test_bytes_changed_after_validation_are_terminal_for_that_file(self):
        def modified(date,directory,**kwargs):
            result=self.driver(date,directory,**kwargs)
            result['validation']={'mechanical':{'pdf_sha256':'0'*64}}
            return result
        with patch('integrations.hermes.function_domain.driver_report.build_driver_pdf',side_effect=modified) as builder:
            self.run_at();self.run_at(61)
        self.assertEqual(builder.call_count,1)
        self.assertEqual([(u,a) for u,a,d,c in self.calls],[('101','metalwork'),('202','metalwork')])
        self.assertEqual(self.rows('runs')[0]['error'],'driver_pdf_content')


if __name__=='__main__':unittest.main()
