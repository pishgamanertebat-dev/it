"""Bounded NET-1 suite. Guard production DB/Excel writes even on fixture mistakes."""
from pathlib import Path
from contextlib import ExitStack
import json, sqlite3, sys, unittest, subprocess, argparse, tempfile
from unittest.mock import patch
import openpyxl

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'runtime/net-migration-20261007'
CORE=Path('C:/Users/win-10/AppData/Local/hermes/hermes-agent')
FIXTURES=Path('C:/Users/win-10/AppData/Local/hermes/runtime/net-isolated-fixtures')
import tools
sys.path.extend([str(CORE),str(CORE/'venv/Lib/site-packages')])
for path in [ROOT/'tools',CORE/'tools']:
    if str(path) not in tools.__path__:tools.__path__.append(str(path))
MODULES=[
    'tools.authorization.test_net',
    'tools.authorization.test_authorization',
    'tools.authorization.test_business_admin',
    'tools.authorization.test_mechanical',
    'tools.authorization.test_metalwork',
    'tools.fleet.repairs.test_entry_service',
    'tools.fleet.repairs.test_entry_bale',
    'tools.fleet.repairs.test_maintenance_service',
    'tools.fleet.repairs.test_maintenance_bale',
    'tools.fleet.work_orders.dev.test_permissions',
    'tools.fleet.work_orders.dev.test_bale_work_order_create',
    'tools.fleet.work_orders.dev.test_bale_message_handler',
    'tools.fleet.work_orders.dev.test_manager_review',
    'tools.fleet.work_orders.dev.test_inline_menus',
    'tools.fleet.work_orders.dev.test_oil_change',
    'tools.fleet.work_orders.dev.test_greasing_proposal_create',
    'tools.fleet.work_orders.dev.test_staff_dispatch',
    'tools.fleet.work_orders.dev.test_staff_pdf',
    'tools.fleet.work_orders.dev.test_staff_inbox',
    'tools.fleet.work_orders.dev.test_daily_archive.ArchiveRetryTests',
    'tools.bale_ui.test_reply_keyboard',
    'tools.bale_ui.test_inline',
    'tools.bale_ui.test_lifecycle',
    'tools.bale_ui.test_multiselect',
]
connect=sqlite3.connect
save=openpyxl.workbook.workbook.Workbook.save
protected=[ROOT/'data/fleet/db/fleet_ops.db',ROOT/'reports/telegram_usage/telegram_users.db']
def safe_connect(database,*args,**kwargs):
    value=str(database).replace('\\','/').lower()
    if any(str(p).replace('\\','/').lower() in value for p in protected) and 'mode=ro' not in value:
        raise AssertionError('Fixture attempted production DB write')
    connection=connect(database,*args,**kwargs)
    fixture_roots=[ROOT/'runtime',FIXTURES]
    if any(str(p).replace('\\','/').lower() in value for p in fixture_roots):
        # Authorization tests do not certify disk/power-loss durability.
        # Avoid the host's blocked FlushFileBuffers in disposable fixture DBs.
        try:
            connection.execute('PRAGMA synchronous=OFF')
        except Exception:
            connection.close()
            raise
    return connection
def safe_save(book,filename):
    if isinstance(filename,(str,Path)):
        p=Path(filename).resolve()
        if str(p).lower().startswith('e:\\function') or p.is_relative_to(ROOT/'work_orders'):
            raise AssertionError('Fixture attempted production workbook write')
    return save(book,filename)
def fixture_pdf(source):
    source=Path(source).resolve()
    assert source.is_relative_to(ROOT/'runtime') or source.is_relative_to(FIXTURES),'PDF fixture escaped test runtime'
    target=source.with_suffix('.pdf')
    target.write_bytes(b'%PDF-1.7\nNET fixture')
    return target
def unique_tests(suite):
    seen=set()
    def flatten(items):
        for t in items:
            if isinstance(t,unittest.TestSuite):
                yield from flatten(t)
            elif t.id() not in seen:
                seen.add(t.id());yield t
    return unittest.TestSuite(flatten(suite))
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--module')
    parser.add_argument('--part')
    parser.add_argument('--rerun-failed',action='store_true')
    args=parser.parse_args()
    if not args.module:
        previous={}
        if args.rerun_failed:
            old=json.loads((OUT/'net1-tests.json').read_text(encoding='utf-8'))
            previous={p['module']:p for p in old['parts']}
        parts=[]
        for index,module in enumerate(MODULES):
            if module in previous and previous[module]['success']:
                parts.append(previous[module])
                print(module+': PASS (existing verified result)',flush=True)
                continue
            part=str(index)
            command=[sys.executable,'-E','-s','-B','-X','utf8','-m','tools.authorization.run_net_checks','--module',module,'--part',part]
            try:
                result=subprocess.run(command,cwd=ROOT,capture_output=True,timeout=240)
                (OUT/('net1-part-'+part+'-stderr.log')).write_bytes(result.stderr)
                evidence=json.loads((OUT/('net1-part-'+part+'.json')).read_text(encoding='utf-8'))
                if result.returncode!=0:evidence['success']=False
            except Exception as exc:
                evidence=dict(success=False,run=0,failures=[],errors=[dict(test=module,traceback=str(exc))])
            parts.append(dict(module=module,**evidence))
            print(module+': '+('PASS' if evidence['success'] else 'FAIL'),flush=True)
        combined=dict(success=all(p['success'] for p in parts),run=sum(p['run'] for p in parts),
            failures=[e for p in parts for e in p.get('failures',[])],
            errors=[e for p in parts for e in p.get('errors',[])],parts=parts,
            production_db_write=False,production_xlsx_save=False,production_send=False,
            fixture_pdf_renderer=True,fixture_durability_simulated=True)
        (OUT/'net1-tests.json').write_text(json.dumps(combined,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(success=combined['success'],run=combined['run'],failures=combined['failures'],errors=combined['errors']),ensure_ascii=False))
        return 0 if combined['success'] else 1
    FIXTURES.mkdir(parents=True,exist_ok=True)
    base_temporary=tempfile.TemporaryDirectory
    class IsolatedDirectory(base_temporary):
        def __init__(self,*a,dir=None,**kw):
            # This worker runs only fixtures; production constants and actor scopes are untouched.
            if dir is not None and Path(dir).resolve().is_relative_to(ROOT/'runtime') and any('test' in part.lower() for part in Path(dir).parts):
                dir=FIXTURES
            super().__init__(*a,dir=dir,**kw)
    with ExitStack() as stack:
        stack.enter_context(patch('tempfile.TemporaryDirectory',IsolatedDirectory))
        suite=unique_tests(unittest.defaultTestLoader.loadTestsFromName(args.module))
        stack.enter_context(patch('sqlite3.connect',side_effect=safe_connect))
        stack.enter_context(patch('os.fsync'))
        stack.enter_context(patch.object(openpyxl.workbook.workbook.Workbook,'save',safe_save))
        for target in ['tools.fleet.work_orders.core.pdf_document.export_staff_pdf',
                       'tools.fleet.work_orders.core.delivery.export_staff_pdf',
                       'tools.fleet.work_orders.channels.bale.message_handler.export_staff_pdf']:
            stack.enter_context(patch(target,side_effect=fixture_pdf))
        with (OUT/('net1-part-'+args.part+'.log')).open('w',encoding='utf-8') as log:
            result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    evidence=dict(success=result.wasSuccessful(),run=result.testsRun,
        failures=[dict(test=str(t),traceback=e) for t,e in result.failures],
        errors=[dict(test=str(t),traceback=e) for t,e in result.errors],skipped=result.skipped)
    (OUT/('net1-part-'+args.part+'.json')).write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result.wasSuccessful() else 1
if __name__=='__main__':sys.exit(main())
