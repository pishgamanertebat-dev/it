"""Acceptance with copied real workbooks, copied authorization and fake Bale only."""
import argparse
import asyncio
from contextlib import closing, ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import uuid

import openpyxl

from tools.authorization import AuthorizationStore
from tools.authorization.net import repairs_sections, domain_decision, WO_DOMAIN
from tools.bale_ui import StateStore
from . import entry_service as daily, entry_dates, maintenance_service as maintenance
from . import maintenance_permission, sync_service, maintenance_baseline, maintenance_sync
from .rehearse_safe_manual_excel import read_snapshot, preserved

DAY = '1405/08/18'
OLDEST = '1405/08/11'
PEOPLE = {'1732374823': ('mechanical',), '654806764': ('mechanical',),
          '641220453': ('mechanical', 'metalwork'), '387679249': ('metalwork',)}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def backup_db(source, target):
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        assert dst.execute('PRAGMA integrity_check').fetchone() == ('ok',)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf8')


def rejected(call, path):
    before = digest(path)
    try:
        call()
    except (daily.EntryError, PermissionError):
        assert digest(path) == before
    else:
        raise AssertionError('Unsafe operation was accepted')


def check_daily(out, fixtures):
    settings = json.loads(daily.CONFIG.read_text('utf8'))
    source = out/'drivers.xlsx'
    shutil.copy2(fixtures/Path(settings['source']).name, source)
    mine = out/'drivers-mine.xlsx'
    shutil.copy2(source, mine)
    settings.update(source=str(source), template=str(daily.ROOT/'templates/repairs/daily_report.xlsx'))
    config = out/'drivers.json'
    write_json(config, settings)
    state = out/'drivers-journal'
    kw = dict(config=config, day=DAY, fleet_db=out/'no-fleet.db')
    sync_service.initialize(mine, config=config, runtime=state)
    original = read_snapshot(source)
    book = openpyxl.load_workbook(source)
    sheet = daily.today_sheet(book, OLDEST)
    cols = daily.layout(sheet)
    row = next(r for r in daily.machine_rows(sheet, cols) if sheet.cell(r, cols['mechanical']).value)
    code = str(sheet.cell(row, cols['code']).value)
    historical_title = sheet.title
    book.close()
    operations, allowed, heights, print_changes = [], set(), set(), set()
    for date in (DAY, OLDEST):
        for actor, sections in PEOPLE.items():
            assert repairs_sections(actor, settings) == sections
            for section in sections:
                preview = daily.preview(actor, code, section, date=date, **kw)
                req = dict(preview, description='ISOLATED ' + actor + ' ' + date,
                           write_mode='append', operation=uuid.uuid4().hex)
                result = daily.commit(actor, req, runtime=state, **kw)
                expected = preview['expected'] + '\n' + req['description'] if preview['expected'] else req['description']
                book = openpyxl.load_workbook(source)
                target = daily.today_sheet(book, date)
                target_cols = daily.layout(target)
                target_row = daily.find_row(target, code, target_cols)
                assert target.cell(target_row, target_cols[section]).value == expected
                allowed.add((target.title, target.cell(target_row, target_cols[section]).coordinate))
                heights.add((target.title, target_row))
                print_changes.add(target.title)
                book.close()
                operations.append(dict(actor=actor, date=date, section=section, operation=req['operation'], sheet=result['sheet']))
                if date == OLDEST and actor == '641220453':
                    before = digest(source)
                    assert daily.commit(actor, req, runtime=state, **kw) == result
                    assert digest(source) == before
            for section in set(('mechanical', 'metalwork')) - set(sections):
                rejected(lambda: daily.preview(actor, code, section, date=date, **kw), source)
    after = read_snapshot(source)
    preserved(original, {title: after[title] for title in original}, allowed, print_changes, heights)
    book = openpyxl.load_workbook(mine)
    assert len(after) == len(original) + (daily.today_sheet(book, DAY) is None)
    book.close()
    for date in ('1405/08/10', '1405/08/19', '1405/08/15'):
        rejected(lambda: daily.preview('641220453', code, 'mechanical', date=date, **kw), source)
        preview = daily.preview('641220453', code, 'mechanical', date=OLDEST, **kw)
        req = dict(preview, date=date, description='never written', write_mode='replace', operation=uuid.uuid4().hex)
        rejected(lambda: daily.commit('641220453', req, runtime=state, **kw), source)
    req = dict(daily.preview('641220453', code, 'mechanical', date=OLDEST, **kw),
               description='no implicit overwrite', operation=uuid.uuid4().hex)
    rejected(lambda: daily.commit('641220453', req, runtime=state, **kw), source)
    stale = dict(req, write_mode='replace')
    first = dict(stale, operation=uuid.uuid4().hex, description='explicit replacement')
    daily.commit('641220453', first, runtime=state, **kw)
    rejected(lambda: daily.commit('641220453', stale, runtime=state, **kw), source)
    # Independently changed mine cell must merge while historical Bale edits survive.
    book = openpyxl.load_workbook(mine)
    latest = daily.dated_sheets(book)[0][1]
    latest_cols = daily.layout(latest)
    latest_row = daily.machine_rows(latest, latest_cols)[0]
    latest.cell(latest_row, latest_cols['metalwork'], 'INDEPENDENT MINE COPY')
    book.save(mine)
    book.close()
    merged = sync_service.merge_snapshot(mine, source, config=config, runtime=state)
    assert merged['status'] == 'merged', merged
    book = openpyxl.load_workbook(source)
    assert daily.today_sheet(book, OLDEST).cell(row, cols['mechanical']).value == 'explicit replacement'
    book.close()
    repeat = digest(source)
    assert sync_service.merge_snapshot(mine, source, config=config, runtime=state)['status'] == 'unchanged'
    assert digest(source) == repeat
    with closing(sqlite3.connect(state/'audit.sqlite3')) as db:
        rows = db.execute("SELECT actor,request,status FROM edits WHERE actor!='mine-file-sync'").fetchall()
    assert len(rows) == len(operations)+1 and all(row[2] == 'succeeded' for row in rows)
    assert all(json.loads(row[1])['date'] in (DAY, OLDEST) for row in rows)
    return dict(operations=operations, original_sheets=len(original), historical_sheet=historical_title,
                today_created=True, full_history_and_format_preserved=True, historical_missing_rejected=True,
                implicit_overwrite_denied=True, concurrent_change_denied=True, journal_verified=True,
                sync_merge=merged['status'], sync_repeat='unchanged', output=str(source))


async def check_maintenance(out, fixtures, auth):
    from tools.bale_ui import runtime
    from . import maintenance_bale, entry_bale
    settings = json.loads(maintenance.CONFIG.read_text('utf8'))
    source = out/'maintenance.xlsx'
    shutil.copy2(fixtures/Path(settings['source']).name, source)
    mine = out/'maintenance-mine.xlsx'
    shutil.copy2(source, mine)
    settings['source'] = str(source)
    config = out/'maintenance.json'
    write_json(config, settings)
    state = out/'maintenance-journal'
    state.mkdir()
    with closing(daily.open_audit(state)):
        pass
    maintenance_baseline.initialize(mine, config=config, runtime=state, expected_hash=digest(source),
        decision='ISOLATED TEST BASELINE ONLY', approved_by='project-owner')
    original = read_snapshot(source)
    kw = dict(config=config, day=DAY)
    request = dict(maintenance.preview('641220453', 'HD707', **kw), mechanic='ISOLATED',
                   description='ISOLATED NET REPAIR', parts='none', operation=uuid.uuid4().hex)
    for actor in ('1732374823', '387679249', '999', '', None):
        rejected(lambda: maintenance.preview(actor, 'HD707', **kw), source)
        rejected(lambda: maintenance.commit(actor, request, runtime=state, **kw), source)
    # Exact installed NET registry entrypoint, actual dispatch and real Writer,
    # with a separate identity DB and a fake transport. No model/network exists.
    home = Path('C:/Users/win-10/AppData/Local/hermes')
    plugin_path = home/'profiles/net/plugins/komatso-bale-registry/__init__.py'
    spec = importlib.util.spec_from_file_location('net_manual_entry_acceptance', plugin_path)
    plugin = importlib.util.module_from_spec(spec)
    with patch.dict('sys.modules', {'gateway.pairing': SimpleNamespace(PairingStore=Mock())}):
        spec.loader.exec_module(plugin)
    plugin.DB_PATH = auth.path
    messages, calls = [], []
    async def send_message(**kw):
        messages.append(kw)
        return SimpleNamespace(message_id=len(messages))
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=send_message), edit_message_reply_markup=AsyncMock())
    gateway = SimpleNamespace(adapters={'bale': SimpleNamespace(_bot=bot)}, _primary_profile_name='net')
    async def worker(payload):
        calls.append(payload)
        if payload['action'] == 'preview':
            result = maintenance.preview(payload['actor'], payload['code'], **kw)
        else:
            result = maintenance.commit(payload['actor'], payload['request'], runtime=state, **kw)
        return {'ok': True, 'result': result}
    handler = maintenance_bale.MaintenanceEntryHandler(run=worker, state_store=StateStore(out/'repair-form.json'))
    telegram = SimpleNamespace(InlineKeyboardMarkup=SimpleNamespace(de_json=lambda value, bot: value))
    def event(text='', raw=None, actor='641220453'):
        return SimpleNamespace(text=text, raw_message=raw, message_id=uuid.uuid4().hex,
            source=SimpleNamespace(platform='bale', chat_type='dm', user_id=actor, chat_id=actor))
    def fallback(gateway, chat, text):
        messages.append(dict(chat_id=chat, text=text))
    key = ('641220453', '641220453')
    with patch.dict('sys.modules', {'telegram': telegram}), \
         patch.object(maintenance_bale, 'CONFIG', config), patch.object(maintenance_bale, '_handler', handler), \
         patch.object(plugin, '_handle_overflow_report', return_value=None), patch.object(plugin, '_send', fallback), \
         patch.object(runtime, 'reply_menu_step', return_value=None), patch.object(runtime.router, 'handlers', {}):
        for text in ('تعمیرات', 'ISOLATED', 'HD707', 'ISOLATED NET REPAIR', 'none'):
            result = plugin._handle_bale(event(text), gateway)
            assert result['reason'] == 'repairs-entry', result
            await asyncio.gather(*list(handler.tasks))
        session = handler.sessions[key]
        confirm = event(raw=dict(bale_inline_callback=True,
            data=f"ik:maintenance_entry:{session['revision']}:confirm", origin_message_id=session['message_id']))
        result = plugin._handle_bale(confirm, gateway)
        assert result['reason'] == 'repairs-entry'
        await asyncio.gather(*list(handler.tasks))
        operation = calls[-1]['request']['operation']
        saved = digest(source)
        assert plugin._handle_bale(confirm, gateway)['reason'] in ('repairs-entry-duplicate', 'repairs-entry-stale')
        assert digest(source) == saved
        assert '✅' in messages[-1]['text']
        for timer in handler.lifecycle.timers.values():
            timer.cancel()
    assert auth.resolve_profile('641220453', '641220453') == 'net'
    with closing(sqlite3.connect(state/'audit.sqlite3')) as db:
        row = db.execute('SELECT actor,request,result FROM edits WHERE operation=?', (operation,)).fetchone()
    result = json.loads(row[2])
    assert row[0] == '641220453'
    assert json.loads(row[1])['date'] == DAY
    book = openpyxl.load_workbook(source)
    _, cols = maintenance.layout(book[result['sheet']])
    book.close()
    allowed = {(result['sheet'], f'{openpyxl.utils.get_column_letter(col)}{result["row"]}') for col in cols.values()}
    preserved(original, read_snapshot(source), allowed, (result['sheet'],), ((result['sheet'], result['row']),))
    assert maintenance_sync.guard_snapshot(mine, source, config=config, runtime=state)['status'] == 'unchanged'
    # Mine copy appends on a separate sheet: retain the new Bale UUID without replay.
    mine_request = dict(maintenance.preview('654806764', 'HD710', **kw), mechanic='MINE COPY',
                        description='INDEPENDENT', parts='none', operation=uuid.uuid4().hex)
    book = openpyxl.load_workbook(mine)
    maintenance.append_record(book, mine_request)
    book.save(mine)
    book.close()
    merged = maintenance_sync.guard_snapshot(mine, source, config=config, runtime=state)
    assert merged['status'] == 'merged', merged
    before_denials = digest(source)
    # Revoke approved identity under the writer lock just before Replace.
    original_save = openpyxl.workbook.workbook.Workbook.save
    def revoke_during_save(book, output):
        original_save(book, output)
        with closing(sqlite3.connect(auth.path)) as db, db:
            db.execute("UPDATE channel_users SET registration_status='revoked' WHERE user_id='641220453'")
    with patch.object(openpyxl.workbook.workbook.Workbook, 'save', revoke_during_save), patch.object(daily, 'replace_source') as replace:
        rejected(lambda: maintenance.commit('641220453', request, runtime=state, **kw), source)
        replace.assert_not_called()
    assert digest(source) == before_denials
    with closing(sqlite3.connect(auth.path)) as db, db:
        db.execute("UPDATE channel_users SET registration_status='approved' WHERE user_id='641220453'")
    # The repair form retains the pre-existing today-only date policy.
    for date in (OLDEST, '1405/08/19'):
        rejected(lambda: maintenance.commit('641220453', dict(request, date=date), runtime=state, **kw), source)
    return dict(actor='641220453', profile='net', installed_plugin_sha256=digest(plugin_path),
                registry_dispatch_and_writer=True, operation=operation, previous_history_preserved=True,
                unauthorized_denied=True, under_lock_identity_revoke_denied=True,
                maintenance_date_policy_unchanged=True, sync_merge=merged['status'], output=str(source))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--daily-evidence', type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    from tools.fleet.work_orders.core import paths
    paths.PROJECT_ROOT = daily.ROOT
    paths.DB_PATH = out/'unused-fleet.db'
    paths.WORK_ORDER_OUTPUT_ROOT = out/'work_orders'
    auth_path = out/'auth.sqlite3'
    backup_db(args.fixtures/'reports/telegram_usage/telegram_users.db', auth_path)
    auth = AuthorizationStore(auth_path)
    before_roles = {actor: auth.roles(actor) for actor in PEOPLE}
    # Windows initializes asyncio using a local socket pair. Build it before
    # blocking all socket connections used by the actual form rehearsal.
    loop = asyncio.new_event_loop()
    with patch('tools.authorization.net.AuthorizationStore', return_value=auth), \
         patch.object(maintenance_permission, 'AuthorizationStore', return_value=auth), \
         patch.dict(os.environ, {'OPENAI_API_KEY': '', 'ANTHROPIC_API_KEY': ''}), \
         patch('socket.socket.connect', side_effect=AssertionError('Model and network forbidden')):
        before_authority = domain_decision('641220453', WO_DOMAIN)
        daily_result = (json.loads(args.daily_evidence.read_text('utf8')) if args.daily_evidence
                        else check_daily(out, args.fixtures))
        write_json(out/'daily-evidence.json', daily_result)
        result = dict(daily=daily_result, maintenance=loop.run_until_complete(check_maintenance(out, args.fixtures, auth)))
        assert domain_decision('641220453', WO_DOMAIN) == before_authority
    assert {actor: auth.roles(actor) for actor in PEOPLE} == before_roles
    loop.close()
    result.update(passed=True, ai_and_network_disabled=True, prior_roles_and_work_order_authority_preserved=True)
    write_json(out/'evidence.json', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
