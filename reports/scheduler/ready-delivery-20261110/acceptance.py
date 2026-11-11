"""Focused scheduler and existing-form regressions; isolated stage and no HTTP."""
from pathlib import Path
import sys,json,unittest,logging,socket
from unittest.mock import patch

ROOT=Path('E:/KomatsoAI');STAGE=ROOT/'runtime/ready-delivery-stage-20261110'
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(STAGE))

MODULES=[
 'tools.scheduler.test_recovery_delivery','tools.scheduler.test_recovery_review','tools.scheduler.test_source_wakeup',
 'tools.scheduler.test_runner','tools.scheduler.test_misfire','tools.scheduler.test_no_data',
 'tools.scheduler.test_preflight','tools.scheduler.test_delivery_alert','tools.scheduler.test_daily_summary',
 'tools.scheduler.test_receipt_immutability','tools.scheduler.test_occurrence_recipients',
 'tools.scheduler.test_maintenance_reminder','tools.scheduler.test_source_notice_alert',
 'tools.fleet.maintenance_daily.test_prepared','tools.fleet.maintenance_daily.test_report',
 'tools.fleet.repairs.test_manual_forms_offline','tools.fleet.repairs.test_ai_daily_manual',
 'tools.fleet.repairs.test_ai_daily_entry','tools.fleet.repairs.test_intent_regression',
 'tools.fleet.repairs.test_maintenance_baseline','tools.fleet.repairs.test_maintenance_sync',
 'tools.fleet.work_orders.dev.test_minimal_lifecycle','tools.fleet.work_orders.dev.test_restart_recovery']


def main():
    attempts=[];original=socket.socket.connect
    def no_http(*args,**kwargs):attempts.append('http');raise AssertionError('Live HTTP forbidden')
    def connect(sock,address):
        if isinstance(address,tuple) and address[0] in {'127.0.0.1','::1','localhost'}:return original(sock,address)
        attempts.append('socket');raise AssertionError('External network forbidden')
    suite=unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    def flatten(value):
        for test in value:
            if isinstance(test,unittest.TestSuite):yield from flatten(test)
            else:yield test
    excluded=[test.id() for test in flatten(suite) if '.NativeMinimalTests.' in test.id()]
    suite=unittest.TestSuite(test for test in flatten(suite) if test.id() not in excluded)
    with (OUT/'acceptance.log').open('w',encoding='utf8') as stream:
        handler=logging.StreamHandler(stream);logging.getLogger().addHandler(handler)
        try:
            with patch('requests.Session.request',no_http),patch('socket.socket.connect',connect):
                result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
        finally:logging.getLogger().removeHandler(handler)
    record=dict(passed=result.wasSuccessful(),tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                skipped=len(result.skipped),external_attempts=attempts,modules=MODULES,
                excluded_native_tests=excluded,native_status='Separate native Office test stalled before an exporter child appeared; isolated test process stopped. Not a software regression pass.',
                failed_ids=[t.id() for t,e in result.failures],error_ids=[t.id() for t,e in result.errors])
    (OUT/'acceptance.json').write_text(json.dumps(record,indent=2),'utf8');print(json.dumps(record))
    return 0 if record['passed'] and not attempts else 1


if __name__=='__main__':raise SystemExit(main())
