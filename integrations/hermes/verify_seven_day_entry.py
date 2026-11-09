"""Reproducible isolated manual-entry, NET, UI and Sync regression suite."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
HOME = Path('C:/Users/win-10/AppData/Local/hermes')

NAMES = [
    'tools.fleet.repairs.test_seven_day_entry',
    'tools.fleet.repairs.test_entry_service',
    'tools.fleet.repairs.test_entry_bale',
    'tools.fleet.repairs.test_department_entry',
    'tools.fleet.repairs.test_maintenance_service',
    'tools.fleet.repairs.test_maintenance_bale',
    'tools.fleet.repairs.test_maintenance_sync',
    'tools.fleet.repairs.test_maintenance_baseline',
    'tools.fleet.repairs.test_sync_service',
    'tools.fleet.repairs.test_sync_cleanup',
    'tools.fleet.repairs.test_manual_forms_offline.DriversOfflineTests.test_no_model_or_network_required_and_unknown_result_retries_same_operation',
    'tools.fleet.repairs.test_manual_forms_offline.RepairsOfflineTests.test_no_model_or_network_required_and_unknown_result_retries_same_operation',
    'tools.bale_ui.test_reply_keyboard', 'tools.bale_ui.test_inline',
    'tools.bale_ui.test_lifecycle', 'tools.bale_ui.test_bridge',
    'tools.authorization.test_net.NetContracts',
    'tools.authorization.test_net.NetInlineContracts',
    'tools.authorization.test_net.NetWorkflows.test_both_repairs_mechanical_metalwork_replace_clear_append_daily_sheet_retry',
    'tools.authorization.test_net.NetWorkflows.test_repairs_revoke_before_replace_denies_and_preserves_file',
    'tools.authorization.test_net.NetWorkflows.test_reply_menu_no_maintenance_and_staff_inbox_coexists',
    'tools.fleet.work_orders.dev.test_minimal_lifecycle.MinimalLifecycleTests.test_tehran_midnight_uses_operational_jalali_date',
    'tools.fleet.work_orders.dev.test_minimal_lifecycle.MinimalLifecycleTests.test_dependency_free_calendar_matches_jdatetime',
    'tools.fleet.work_orders.dev.test_minimal_lifecycle.MinimalLifecycleTests.test_expired_draft_rejects_all_backend_operations',
    'tools.fleet.work_orders.dev.test_minimal_lifecycle.MinimalLifecycleTests.test_expired_uncertain_is_absent_and_cannot_retry',
    'tools.fleet.work_orders.dev.test_minimal_lifecycle.MinimalLifecycleTests.test_old_callback_cannot_reactivate_form',
]

PREVIOUS_FAILURES = [
    'tools.authorization.test_net.NetWorkflows.test_both_own_AF_OC_GR_create_preview_review_assign_approve_send_archives',
    'tools.authorization.test_net.NetWorkflows.test_create_revoke_during_render_rolls_back_and_cleans_output',
    'tools.authorization.test_net.NetWorkflows.test_send_revoke_during_export_and_cross_owner_denied',
    'tools.fleet.work_orders.dev.test_minimal_lifecycle.MinimalLifecycleTests.test_historical_af_proposal_and_source_dates_are_not_operational_order_dates',
    'tools.fleet.work_orders.dev.test_minimal_lifecycle.MinimalLifecycleTests.test_main_entry_exits_every_idle_form_and_no_extra_entry_actions_exist',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--suite', choices=['required', 'previous-failures', 'work-order-audit'], default='required')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (ROOT/'runtime').mkdir(exist_ok=True)
    core = HOME/'hermes-agent'
    sys.path.extend([str(core), str(core/'venv/Lib/site-packages')])
    import tools
    if str(ROOT/'tools') not in tools.__path__:
        tools.__path__.append(str(ROOT/'tools'))
    tools.__path__.append(str(core/'tools'))
    # Historical work-order fixtures have a fixed production project root.
    # Redirect their entire test workspace before importing any test modules.
    from tools.fleet.work_orders.core import paths
    paths.PROJECT_ROOT = ROOT
    paths.DB_PATH = ROOT/'data/fleet/db/fleet_ops.db'
    paths.WORK_ORDER_OUTPUT_ROOT = out/'work_orders'
    paths.WORK_ORDER_TEMPLATE_ROOT = ROOT/'templates/work_orders'
    code = list((ROOT/'tools/fleet/repairs').glob('*.py')) + list((ROOT/'settings').glob('*entry.json'))
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in code}
    selected = (NAMES if args.suite == 'required' else PREVIOUS_FAILURES if args.suite == 'previous-failures'
                else ['tools.authorization.test_net', 'tools.fleet.work_orders.dev.test_minimal_lifecycle'])
    suite = unittest.defaultTestLoader.loadTestsFromNames(selected)
    stage = out/'sync'
    stage.mkdir(exist_ok=True)
    (out/'scratch').mkdir(exist_ok=True)
    for name in ('mine_file_sync.py', 'test_mine_file_sync.py', 'test_mine_file_sync_reliability.py'):
        if args.suite != 'required':
            break
        shutil.copy2(HOME/'scripts'/name, stage/name)
        if not name.startswith('test_'):
            continue
        spec = importlib.util.spec_from_file_location(Path(name).stem, stage/name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    with (out/'tests.txt').open('w', encoding='utf8') as stream:
        old_stderr = sys.stderr
        sys.stderr = stream
        try:
            result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        finally:
            sys.stderr = old_stderr
    assert hashes == {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in code}
    report = dict(passed=result.wasSuccessful(), tests_run=result.testsRun, failures=len(result.failures),
                  errors=len(result.errors), skipped=len(result.skipped), tested_code=hashes,
                  failed_ids=[test.id() for test, _ in result.failures], error_ids=[test.id() for test, _ in result.errors])
    (out/'acceptance.json').write_text(json.dumps(report, indent=2), 'utf8')
    print(json.dumps({k: v for k, v in report.items() if k != 'tested_code'}))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
