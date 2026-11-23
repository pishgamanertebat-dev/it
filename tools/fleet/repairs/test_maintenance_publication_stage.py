import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid
from tools.fleet.repairs import maintenance_publication as pub
OUT = pub.CANDIDATE_ROOT / "chunked-20261117"

class StageTests(unittest.TestCase):
    def setUp(self):
        self.local=OUT/'stage-tests'/uuid.uuid4().hex
        self.local.mkdir(parents=True)
        self.data=bytes(range(256))*21+b'END'
        self.hash=hashlib.sha256(self.data).hexdigest()
        self.unc=Path(r'\\192.168.20.251\share\_smb_test_20261116_155948')/('.maintenance-publish-'+uuid.uuid4().hex+'.partial')

    def worker(self,path,sha=None,prefix=''):
        return subprocess.run([sys.executable,'-B','-c',prefix+pub.SMB_STAGE_WORKER,str(path),sha or self.hash],
                              input=self.data,capture_output=True,timeout=10)

    def test_local_stage_unchanged_and_existing_never_overwritten(self):
        target=self.local/'stage.partial'
        pub.stage_bytes(target,self.data)
        self.assertEqual(target.read_bytes(),self.data)
        with self.assertRaises(FileExistsError):pub.stage_bytes(target,b'wrong')
        self.assertEqual(target.read_bytes(),self.data)

    def test_unc_operational_name_denied_before_io(self):
        with patch.object(pub.subprocess,'run') as run:
            with self.assertRaisesRegex(Exception,'STAGE_NAME_DENIED'):
                pub.stage_bytes(self.unc.with_name(pub.WORKBOOK),self.data)
            run.assert_not_called()

    def test_unc_timeout_does_not_retry(self):
        with patch.object(pub.subprocess,'run',side_effect=subprocess.TimeoutExpired('stage',120)) as run:
            with self.assertRaisesRegex(Exception,'STAGE_TIMEOUT'):pub.stage_bytes(self.unc,self.data)
            run.assert_called_once()

    def test_unc_worker_error_and_hash_failure_propagate(self):
        for code,error in [(1,'STAGE_WRITE_FAILED'),(3,'STAGE_WRITE_FAILED'),(4,'STAGE_READBACK_FAILED')]:
            with self.subTest(code=code),patch.object(pub.subprocess,'run',return_value=subprocess.CompletedProcess([],code)) as run:
                with self.assertRaisesRegex(Exception,error):pub.stage_bytes(self.unc,self.data)
                run.assert_called_once()

    def test_unc_success_uses_one_bounded_child(self):
        with patch.object(pub.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as run:
            pub.stage_bytes(self.unc,self.data)
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs['timeout'],120)
            self.assertEqual(run.call_args.kwargs['input'],self.data)

    def test_worker_real_bytes_and_short_final_chunk(self):
        target=self.local/'stage.partial'
        self.assertEqual(self.worker(target).returncode,0)
        self.assertEqual(target.read_bytes(),self.data)

    def test_worker_existing_stage_preserved(self):
        target=self.local/'stage.partial';target.write_bytes(b'KEEP')
        self.assertNotEqual(self.worker(target).returncode,0)
        self.assertEqual(target.read_bytes(),b'KEEP')

    def test_worker_rejects_corrupted_input_before_create(self):
        target=self.local/'stage.partial'
        self.assertEqual(self.worker(target,'0'*64).returncode,2)
        self.assertFalse(target.exists())

    def test_worker_rejects_corrupted_readback(self):
        target=self.local/'stage.partial'
        prefix="import pathlib\npathlib.Path.read_bytes=lambda self:b'CORRUPTED'\n"
        self.assertEqual(self.worker(target,prefix=prefix).returncode,4)

if __name__=='__main__':
    with (OUT/'stage-unit.tests.log').open('w',encoding='utf8') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StageTests))
    (OUT/'stage-unit.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skips=len(result.skipped)),indent=2),'utf8')
    print('tests',result.testsRun,'failures',len(result.failures),'errors',len(result.errors))
    raise SystemExit(not result.wasSuccessful())
