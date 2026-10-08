"""Offline acceptance runner: protect production DBs/Excel and prohibit live transports."""
import argparse
from contextlib import ExitStack
import importlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

LIVE = Path('E:/KomatsoAI')
CORE = Path('C:/Users/win-10/AppData/Local/hermes/hermes-agent')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--modules',nargs='*')
    args=parser.parse_args()
    root=args.root.resolve()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    sys.path[:0]=[str(root),str(CORE)]
    sys.path.extend([str(LIVE/'.venv/Lib/site-packages'),str(CORE/'venv/Lib/site-packages')])
    import tools
    tools.__path__=[str(root/'tools'), *list(tools.__path__)]
    if str(CORE/'tools') not in tools.__path__:tools.__path__.append(str(CORE/'tools'))
    import subprocess
    run=subprocess.run
    def safe_run(argv,*a,**kw):
        if isinstance(argv,(list,tuple)) and '-TargetPath' in argv:
            target=Path(argv[argv.index('-TargetPath')+1]).resolve()
            if str(target).lower().startswith('e:\\function'):
                raise AssertionError('Production workbook subprocess write prohibited')
        return run(argv,*a,**kw)
    connect=sqlite3.connect
    import openpyxl
    save=openpyxl.workbook.workbook.Workbook.save
    protected=[LIVE/'data/fleet/db/fleet_ops.db',LIVE/'reports/telegram_usage/telegram_users.db',LIVE/'runtime/scheduler/runs.sqlite3']
    def safe_connect(database,*a,**kw):
        value=str(database).replace('\\','/').lower()
        if any(str(p).replace('\\','/').lower() in value for p in protected) and 'mode=ro' not in value:
            raise AssertionError('Production SQLite write prohibited in acceptance tests')
        c=connect(database,*a,**kw)
        if '/runtime/' in value:
            try:
                c.execute('PRAGMA synchronous=OFF')
            except Exception:
                c.close()
                raise
        return c
    def safe_save(book,filename):
        if isinstance(filename,(str,Path)):
            p=Path(filename).resolve()
            if str(p).lower().startswith('e:\\function') or p.is_relative_to(LIVE/'work_orders'):
                raise AssertionError('Production workbook write prohibited')
        return save(book,filename)
    def fixture_pdf(source):
        p=Path(source).resolve()
        if not p.is_relative_to(LIVE/'runtime'):
            raise AssertionError('PDF fixture outside runtime')
        target=p.with_suffix('.pdf');target.write_bytes(b'%PDF-1.7\nFIXTURE')
        return target
    for key in list(os.environ):
        if key.endswith(('_API_KEY','_TOKEN','_SECRET','_PASSWORD','_CREDENTIALS')):
            os.environ.pop(key,None)
    logging.getLogger().setLevel(logging.CRITICAL)
    with tempfile.TemporaryDirectory(dir=LIVE/'runtime/pakdel-delivery',prefix='offline-') as home, ExitStack() as stack:
        os.environ['HERMES_HOME']=home
        stack.enter_context(patch('sqlite3.connect',side_effect=safe_connect))
        stack.enter_context(patch('subprocess.run',side_effect=safe_run))
        stack.enter_context(patch.object(openpyxl.workbook.workbook.Workbook,'save',safe_save))
        stack.enter_context(patch('requests.sessions.Session.request',side_effect=AssertionError('Live network prohibited')))
        stack.enter_context(patch('tools.fleet.work_orders.core.delivery.export_staff_pdf',side_effect=fixture_pdf))
        stack.enter_context(patch('tools.fleet.work_orders.channels.bale.message_handler.export_staff_pdf',side_effect=fixture_pdf))
        loader=unittest.defaultTestLoader
        if args.modules:
            suite=loader.loadTestsFromNames(args.modules)
        else:
            suite=unittest.TestSuite(loader.discover(str(root/d),top_level_dir=str(root)) for d in
                ['tools/authorization','tools/scheduler','tools/fleet/work_orders/dev','tools/fleet/maintenance_daily'])
        seen=set()
        def flatten(s):
            for test in s:
                if isinstance(test,unittest.TestSuite):
                    yield from flatten(test)
                elif test.id() not in seen:
                    seen.add(test.id());yield test
        suite=unittest.TestSuite(flatten(suite))
        with args.out.with_suffix('.log').open('w',encoding='utf-8') as log:
            result=unittest.TextTestRunner(stream=log,verbosity=1).run(suite)
        report=dict(tests=result.testsRun,success=result.wasSuccessful(),skipped=[(t.id(),r) for t,r in result.skipped],
                    failures=[(t.id(),s) for t,s in result.failures],errors=[(t.id(),s) for t,s in result.errors])
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in {'failures','errors','skipped'}}))
        for t,s in result.errors+result.failures:print(t, s.splitlines()[-1])
        return 0 if result.wasSuccessful() else 1


if __name__=='__main__':
    raise SystemExit(main())
