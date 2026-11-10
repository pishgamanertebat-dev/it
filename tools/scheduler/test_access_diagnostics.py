"""Safe stage/OS diagnostics without raw exception strings or unrestricted paths."""
from pathlib import Path
import json
import unittest
from .report_errors import access_diagnostics,report_io


class DiagnosticsTests(unittest.TestCase):
    def test_only_allowed_paths_and_system_fields_are_recorded(self):
        exc=PermissionError(13,'token=secret',r'E:\Function\daily.xlsx');exc.winerror=32
        data=access_diagnostics(exc,'source',access='read')
        self.assertEqual(data['errno'],13);self.assertEqual(data['winerror'],32)
        self.assertEqual(data['access'],'read');self.assertEqual(data['path'],r'E:\Function\daily.xlsx')
        self.assertNotIn('secret',json.dumps(data))
        exc.filename=r'C:\private\secret.xlsx'
        self.assertNotIn('path',access_diagnostics(exc))

    def test_nested_io_retains_exact_inner_stage_and_context_without_changing_exception(self):
        exc=PermissionError(13,'private text');exc.winerror=5
        path=Path(__file__).resolve().parents[2]/'runtime/fixture.pdf'
        with self.assertLogs('tools.scheduler.report_errors',level='ERROR') as logs:
            with self.assertRaises(PermissionError) as caught:
                with report_io('temp_directory',path.parent,'write'):
                    with report_io('export',path,'write'):raise exc
        self.assertIs(caught.exception,exc)
        data=access_diagnostics(exc)
        self.assertEqual(data['stage'],'export');self.assertEqual(data['target_path'],str(path))
        self.assertNotIn('private text','\n'.join(logs.output))

    def test_source_and_write_failures_have_distinct_diagnostics(self):
        for stage,access in [('source','read'),('output_dir','write'),('snapshot_write','write'),('metadata_write','write')]:
            with self.subTest(stage=stage),self.assertLogs('tools.scheduler.report_errors',level='ERROR'):
                exc=PermissionError(13,'redacted')
                with self.assertRaises(PermissionError):
                    with report_io(stage,None,access):raise exc
                self.assertEqual(access_diagnostics(exc)['stage'],stage)
                self.assertEqual(access_diagnostics(exc)['access'],access)

if __name__=='__main__':unittest.main()
