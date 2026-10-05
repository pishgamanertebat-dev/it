"""Run local acceptance under the existing isolated native test venv; never send to Bale."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json,os,sys,unittest
ROOT=Path(__file__).resolve().parents[2]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
OUT=Path((ROOT/'runtime/mechanical-active.txt').read_text(encoding='utf-8'))
sys.path[:0]=[str(ROOT),str(HOME/'hermes-agent')]
sys.path.extend([str(ROOT/'.venv/Lib/site-packages'),str(HOME/'hermes-agent/venv/Lib/site-packages'),str(ROOT/'tools')])
import tools
if str(ROOT/'tools') not in tools.__path__:tools.__path__.append(str(ROOT/'tools'))
if str(HOME/'hermes-agent/tools') not in tools.__path__:tools.__path__.append(str(HOME/'hermes-agent/tools'))
SUITES=['tools.authorization.test_authorization','tools.authorization.test_mechanical',
        'tools.admin.test_admin1','tools.admin.test_driver_pdf','tools.admin.test_gateway_extensions',
        'tools.scheduler.test_runner','tools.fleet.overflow.test_report',
        'tools.security.test_public_research','tools.test_shared_fast_core','tools.test_manual_worker_batch',
        'tools.test_manual_preparation']
def main():
    for key in list(os.environ):
        if key.endswith(('_API_KEY','_TOKEN','_SECRET','_PASSWORD','_CREDENTIALS')) or key.startswith('HERMES_SESSION_'):
            os.environ.pop(key,None)
    with TemporaryDirectory(prefix='hermetic-',dir=OUT) as tmp:
        os.environ['HERMES_HOME']=tmp
        suite=unittest.defaultTestLoader.loadTestsFromNames(SUITES)
        with (OUT/'hermetic-tests.log').open('w',encoding='utf-8') as log:
            result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
        report={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'success':result.wasSuccessful(),'suites':SUITES}
        (OUT/'hermetic-tests.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
        if not result.wasSuccessful():
            for test,error in result.failures+result.errors:print(test,error)
        return 0 if result.wasSuccessful() else 1
if __name__=='__main__':raise SystemExit(main())
