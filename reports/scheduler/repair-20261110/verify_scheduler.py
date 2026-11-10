"""Final scheduler acceptance with real HTTP explicitly forbidden."""
from pathlib import Path
import sys,json,unittest,logging
from unittest.mock import patch
ROOT=Path('E:/KomatsoAI');OUT=Path(__file__).resolve().parent
STAGE=ROOT/'runtime/scheduler-repair-20261110/stage';sys.path.insert(0,str(STAGE))

def main():
    suite=unittest.defaultTestLoader.discover(str(STAGE/'tools/scheduler'),pattern='test_*.py',top_level_dir=str(STAGE))
    blocked=[]
    def no_http(*args,**kwargs):
        blocked.append('blocked');raise AssertionError('Real HTTP forbidden in scheduler acceptance')
    with (OUT/'scheduler-acceptance.log').open('w',encoding='utf8') as stream:
        handler=logging.StreamHandler(stream);logging.getLogger().addHandler(handler)
        try:
            with patch('requests.Session.request',no_http):result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
        finally:logging.getLogger().removeHandler(handler)
    record=dict(passed=result.wasSuccessful(),tests_run=result.testsRun,failures=len(result.failures),
        errors=len(result.errors),skipped=len(result.skipped),real_http_calls=0,blocked_attempts=len(blocked),
        failed_ids=[t.id() for t,e in result.failures],error_ids=[t.id() for t,e in result.errors])
    (OUT/'scheduler-acceptance.json').write_text(json.dumps(record,indent=2),'utf8');print(json.dumps(record))
    raise SystemExit(0 if result.wasSuccessful() else 1)

if __name__=='__main__':main()
