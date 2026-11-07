"""Run isolated maintenance/UI/Drivers2 and staged Sync regression checks."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

from integrations.hermes.deploy_maintenance_baseline import ROOT, OUT, HOME, digest

NAMES = [
    'tools.fleet.repairs.test_maintenance_baseline',
    'tools.fleet.repairs.test_maintenance_service',
    'tools.fleet.repairs.test_maintenance_bale',
    'tools.fleet.repairs.test_entry_service',
    'tools.fleet.repairs.test_entry_bale',
    'tools.fleet.repairs.test_department_entry',
    'tools.fleet.repairs.test_sync_service',
    'tools.fleet.repairs.test_sync_cleanup',
    'tools.fleet.repairs.test_maintenance_sync',
    'tools.fleet.repairs.test_manual_forms_offline.DriversOfflineTests.test_no_model_or_network_required_and_unknown_result_retries_same_operation',
    'tools.fleet.repairs.test_manual_forms_offline.RepairsOfflineTests.test_no_model_or_network_required_and_unknown_result_retries_same_operation',
    'tools.bale_ui.test_reply_keyboard', 'tools.bale_ui.test_inline',
    'tools.bale_ui.test_lifecycle', 'tools.bale_ui.test_bridge',
]


def run():
    code = [ROOT / name for name in (
        'tools/fleet/repairs/maintenance_baseline.py', 'tools/fleet/repairs/maintenance_sync.py',
        'tools/fleet/repairs/maintenance_service.py', 'tools/fleet/repairs/entry_service.py',
        'tools/fleet/repairs/sync_service.py', 'tools/fleet/repairs/sync_cells.py',
        'tools/fleet/repairs/entry_bale.py', 'tools/fleet/repairs/maintenance_bale.py',
        'settings/maintenance_entry.json', 'settings/repairs_entry.json', 'settings/bale_reply_menus.json',
        'tools/fleet/repairs/test_maintenance_baseline.py',
        'integrations/hermes/deploy_maintenance_baseline.py',
        'integrations/hermes/verify_maintenance_baseline.py',
        'integrations/hermes/verify_maintenance_baseline_excel.ps1',
        'tools/fleet/repairs/rehearse_maintenance_baseline.py',
        'runtime/maintenance-baseline-20261107/stage/mine_file_sync.py',
    )]
    hashes = {str(path): digest(path.read_bytes()) for path in code}
    suite = unittest.defaultTestLoader.loadTestsFromNames(NAMES)
    stage = OUT / 'stage'
    (OUT / 'scratch').mkdir(exist_ok=True)
    for name in ('test_mine_file_sync.py', 'test_mine_file_sync_reliability.py'):
        path = stage / name
        path.write_bytes((HOME / 'scripts' / name).read_bytes())
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    with (OUT / 'acceptance-tests.txt').open('w', encoding='utf8') as stream:
        stderr = sys.stderr
        sys.stderr = stream
        try:
            result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        finally:
            sys.stderr = stderr
    assert hashes == {str(path): digest(path.read_bytes()) for path in code}
    report = dict(passed=result.wasSuccessful(), tests_run=result.testsRun,
                  failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                  native_excel_passed=False, tested_code=hashes)
    (OUT / 'acceptance.json').write_text(json.dumps(report, indent=2), 'utf8')
    print(json.dumps({k:v for k,v in report.items() if k != 'tested_code'}))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    run()
