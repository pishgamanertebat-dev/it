"""Run the staged implementation in this process; never modify production modules."""
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
STAGE=Path(__file__).with_name('candidate')
for name in ['tools.scheduler.report_errors',
             'integrations.hermes.function_domain.driver_report',
             'tools.fleet.repairs.report']:
    module=importlib.import_module(name)
    source=STAGE/Path(*name.split('.')).with_suffix('.py')
    exec(compile(source.read_text(encoding='utf-8'),module.__file__,'exec'),module.__dict__)

if '--native' in sys.argv:
    from integrations.hermes.function_domain.driver_report import build_driver_pdf
    import hashlib
    out=Path(__file__).parent
    source=next(out.glob('source-*.xlsx'));data=source.read_bytes()
    result=build_driver_pdf('1405/08/19',out/'native-acceptance',data=data)
    evidence={'source_sha256':hashlib.sha256(data).hexdigest(),
        'inventory_count':len(result['report']['inventory']),
        'blank_ordinals':[{'row':r['row'],'code':r['values'][2]} for r in result['report']['inventory'] if r['values'][0] is None],
        'documents':result['documents'],'validation':result['validation']}
    (out/'native-acceptance.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(evidence,ensure_ascii=False))
else:
    name='tools.admin.test_driver_ordinal'
    parent=importlib.import_module('tools.admin')
    spec=importlib.util.spec_from_file_location(name,STAGE/'tools/admin/test_driver_ordinal.py')
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    setattr(parent,'test_driver_ordinal',module);spec.loader.exec_module(module)
    names=['tools.admin.test_driver_pdf','tools.fleet.repairs.test_pdf_validation',
        'tools.fleet.repairs.test_report','tools.scheduler.test_report_errors',
        'tools.scheduler.test_preflight','tools.scheduler.test_driver_pdf_gate']
    suite=unittest.defaultTestLoader.loadTestsFromModule(module)
    if '--ordinal-only' not in sys.argv:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromNames(names))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
