"""Audit-only reproduction. Synthetic files only; no service, model or transport.

Run from the project with .venv/Scripts/python.exe -B -X utf8 this_file.py.
The outgoing copy below is a SIMULATED use of an existing generic copy primitive,
not an implementation or activation of outgoing sync. Production main/worker,
Cron, Gateway, operational settings and operational journals are never invoked.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone, timedelta
import ctypes
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import openpyxl
from tools.fleet.repairs import entry_service as writer
from tools.fleet.repairs import maintenance_service as service
from tools.fleet.repairs import maintenance_report as reports
from tools.fleet.repairs import maintenance_baseline as baseline
from tools.fleet.repairs.maintenance_sync import guard_snapshot, MaintenanceSyncBlocked

spec = importlib.util.spec_from_file_location('audit_isolated_sync', ROOT/'integrations/hermes/mine_sync/mine_file_sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
DAY = '1405/08/25'  # fixed synthetic clock, no dependence on operational dates
RUN = ROOT/'runtime/ai-excel-sync-audit'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
RUN.mkdir(parents=True, exist_ok=False)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def within(path):
    path = Path(path).resolve()
    if RUN.resolve() not in path.parents:
        raise AssertionError('All mutable test paths must be inside the isolated run')
    return path


def rows(path):
    book = openpyxl.load_workbook(io.BytesIO(within(path).read_bytes()))
    try:
        return {s.title: [list(r) for r in s.iter_rows(min_row=3, values_only=True)
                          if any(v is not None for v in r)] for s in book}
    finally:
        book.close()


class Fixture:
    def __init__(self, name):
        self.root = RUN/name
        self.mine = self.root/'mine/maintenance.xlsx'
        self.local = self.root/'function/maintenance.xlsx'
        self.runtime = self.root/'journal'
        self.config = self.root/'entry.json'
        for p in (self.mine.parent, self.local.parent, self.runtime):
            within(p).mkdir(parents=True)
        book = openpyxl.Workbook()
        book.remove(book.active)
        for code, name in [('710', 'دامپ 710'), ('851', 'بیل 851'), ('متفرقه', 'متفرقه')]:
            s = book.create_sheet(code)
            s['A1'] = name
            s.append(['تاریخ', 'ثبت کننده', 'نام مکانیک', 'کد مکانیزم' if code != 'متفرقه' else 'قسمت',
                      'نوع خرابی', 'قطعات مصرفی'])
            s.append(['1405/08/20', 'SYNTHETIC-MINE', 'TEST', code, 'BASE-'+code, None])
            for c in 'ABCDEF':
                s.column_dimensions[c].width = 45
            s.print_area = 'A1:F3'
        book.save(self.local)
        book.close()
        self.mine.write_bytes(self.local.read_bytes())
        self.initial = rows(self.local)
        self.config.write_text(json.dumps(dict(source=str(self.local), allowed_users=['synthetic'],
                                              sync_policy='upstream_copy')), encoding='utf8')
        with closing(writer.open_audit(self.runtime)):
            pass
        baseline.initialize(self.mine, expected_hash=sha(self.mine), approved_by='project-owner',
                            decision='SYNTHETIC TEST ONLY: equal fixture baseline',
                            config=self.config, runtime=self.runtime)
        self.settings = dict(source=str(self.local), recorder='SYNTHETIC-AI', department='تعمیرگاه',
                             mechanic='TEST', scope='all')
        self.ops = []

    def ai(self, text='AI-1', source='test:1', devices=('دامپتراک710',)):
        raw = 'گزارش مکانیکی 24 آبان 1405\n'+'\n'.join('-'+d+': '+text+'.' for d in devices)
        with patch.object(reports, 'permission', return_value=self.settings):
            result = reports.receive('synthetic', 'synthetic', source, raw, config=self.root/'policy.json',
                                     entry_config=self.config, runtime=self.runtime, day=DAY,
                                     ledger=self.root/'absent-ledger.sqlite3')
        if result['operation'] not in self.ops:
            self.ops.append(result['operation'])
        return result

    def alter(self, fn, target=None):
        target = within(target or self.mine)
        book = openpyxl.load_workbook(target)
        try:
            fn(book)
            book.save(target)
        finally:
            book.close()

    def inbound(self):
        # The subprocess boundary alone is replaced, preventing any default
        # operational config/runtime from being used. The actual tree, staging,
        # Windows read handle, guard, journal and ReplaceFileW execute unchanged.
        def bridge(staged, destination, **kw):
            assert kw['module'] == 'tools.fleet.repairs.maintenance_sync'
            within(staged); within(destination)
            try:
                return guard_snapshot(staged, destination, config=self.config, runtime=self.runtime)
            except Exception as exc:
                raise OSError(str(exc)) from exc
        with patch.object(sync, 'MAINTENANCE_REPORT', self.local), \
             patch.object(sync, 'FAULT_REPORT', self.root/'unused-fault.xlsx'), \
             patch.object(sync, 'publish_fault_report', side_effect=bridge):
            result = sync.empty_result()
            sync.sync_tree(self.mine.parent, self.local.parent, result, lambda **kw: None)
            return result

    def outgoing_simulated(self):
        within(self.local); within(self.mine)
        assert self.mine.resolve() not in {sync.MAINTENANCE_REPORT.resolve(), sync.FAULT_REPORT.resolve()}
        return sync.atomic_copy(self.local, self.mine, self.local.stat(), lambda **kw: None)

    def evidence(self):
        with closing(sqlite3.connect(self.runtime/'audit.sqlite3')) as db:
            journal = [dict(zip(('operation', 'status', 'before_hash', 'after_hash'), r)) for r in db.execute(
                'SELECT operation,status,before_hash,after_hash FROM edits ORDER BY rowid')]
        return dict(initial=self.initial, final_mine=rows(self.mine), final_function=rows(self.local),
                    mine_sha256=sha(self.mine), function_sha256=sha(self.local),
                    journal=journal, artifacts=str(self.root.relative_to(ROOT)))


def net_error(code=53):
    e = OSError('SIMULATED SMB failure')
    e.winerror = code
    return e


def count(path, text):
    return sum(text in str(r[4]) for values in rows(path).values() for r in values)


tests = []


def record(f, verdict, sequence, mine_change, ai_change, loss, duplicate, conflict, behavior,
           simulation=None, extra=None):
    tests.append(dict(id=f.root.name, verdict=verdict, experiment_assertions='PASS', sequence=sequence,
                      mine_change=mine_change, ai_change=ai_change, data_loss=loss, duplicate=duplicate,
                      conflict=conflict, current_code_behavior=behavior, simulation=simulation or [],
                      extra=extra or {}, **f.evidence()))


f = Fixture('T1')
f.ai(); f.outgoing_simulated(); result = f.inbound()
assert sha(f.mine) == sha(f.local) and count(f.local, 'AI-1') == 1
assert result['maintenance_guard']['absorbed_operations'] == f.ops
record(f, 'PASS', ['equal BASE files', 'AI append', 'SIMULATED outgoing whole-file copy', 'immediate real inbound guard'],
       'none', 'one append to 710', False, False, False,
       'Exact returned bytes acknowledge protected UUIDs; no repeated append.',
       ['outgoing publication only; not present in production'], result)

f = Fixture('T2')
f.ai(); before = sha(f.local); result = f.inbound()
assert sha(f.local) == before and count(f.local, 'AI-1') == 1 and count(f.mine, 'AI-1') == 0
record(f, 'PASS', ['equal BASE', 'AI append', 'inbound before outgoing'], 'none', 'one append to 710',
       False, False, False, 'Unchanged upstream baseline preserves local UUID; mine still lacks AI.', extra=result)

f = Fixture('T3')
f.ai(); f.alter(lambda b: setattr(b['851']['E3'], 'value', 'MINE-EDIT-851'))
result = f.inbound()
assert count(f.local, 'MINE-EDIT-851') == 1 and count(f.local, 'AI-1') == 1
record(f, 'PASS', ['equal BASE', 'AI append on 710', 'mine edits 851/E3', 'inbound guarded merge'],
       '851/E3= MINE-EDIT-851', 'new 710 row', False, False, False,
       'upstream_copy keeps mine history edit and replays exact UUID-proven AI append locally.', extra=result)

f = Fixture('T4')
f.ai(); f.outgoing_simulated()
f.alter(lambda b: setattr(b['710']['E4'], 'value', 'MINE-CORRECTED-SAME-AI-RECORD'))
result = f.inbound()
assert count(f.local, 'AI-1') == 1 and count(f.local, 'MINE-CORRECTED-SAME-AI-RECORD') == 1
record(f, 'FAIL', ['AI append 710/E4', 'SIMULATED publication without inbound acknowledgement',
                   'mine corrects the same published record E4', 'inbound'],
       '710/E4 changes the same AI event', '710/E4 original AI text', False, True, 'UNDETECTED_SEMANTIC_CONFLICT',
       'No stable row UUID in workbook: mine correction and original AI append become two rows, without conflict.',
       ['outgoing copy; mine edit via openpyxl, not native Excel'], result)

f = Fixture('T5')
f.ai(); before = (sha(f.mine), sha(f.local)); replace = sync.os.replace
def outage_replace(src, dst):
    if Path(dst).resolve() == f.mine.resolve():
        raise net_error()
    return replace(src, dst)
try:
    with patch.object(sync.os, 'replace', side_effect=outage_replace):
        f.outgoing_simulated()
except OSError:
    pass
else:
    raise AssertionError('Expected synthetic outage')
assert before == (sha(f.mine), sha(f.local))
args = SimpleNamespace(home=f.root/'sync-home', source=f.mine.parent, dest=f.local.parent, dry_run=False,
                       report=False, backoff_minutes=sync.BACKOFF_MINUTES, backoff_jitter=0)
with patch.object(sync, 'reachable', side_effect=net_error()):
    sync.run(args)
state = json.loads((args.home/'state/mine-file-sync.json').read_text('utf8'))
assert state['health_status'] == 'source_unavailable' and not state['sync_healthy']
record(f, 'UNKNOWN', ['AI append', 'SIMULATED outgoing rename network failure', 'isolated incoming supervisor outage'],
       'none', 'one append', False, False, False,
       'Copy primitive preserves old target; existing inbound run records outage/backoff. No outgoing retry exists.',
       ['SMB/network failure injection; outgoing supervisor absent'], state['last_run'])

# T6 reproduces T5's persisted-backoff lifecycle in a separate fixture.
f6 = Fixture('T6')
f6.ai()
args6 = SimpleNamespace(**vars(args)); args6.dest = f6.local.parent; args6.source = f6.mine.parent
args6.home = f6.root/'sync-home'
with patch.object(sync, 'reachable', side_effect=net_error()):
    sync.run(args6)
sync.run(args6)
skip = json.loads((args6.home/'state/mine-file-sync.json').read_text('utf8'))['health_status']
assert skip == 'backoff_skip'
f6.outgoing_simulated()
future = (datetime.now(timezone.utc)+timedelta(minutes=4)).isoformat()
with patch.object(sync, 'utcnow', return_value=future), \
     patch.object(sync, 'supervise', side_effect=lambda a: f6.inbound()):
    sync.run(args6)
state = json.loads((args6.home/'state/mine-file-sync.json').read_text('utf8'))
assert state['sync_healthy'] and not state['outage'] and sha(f6.local) == sha(f6.mine)
record(f6, 'UNKNOWN', ['outage', 'early retry skips under persisted backoff', 'SIMULATED reconnection and outgoing copy',
                     'clock advanced 4 minutes in isolated supervisor', 'guarded inbound recovery'],
       'none', 'one append', False, False, False,
       'Inbound recovery clears outage. Outgoing recovery/receipt does not exist and cannot be certified.',
       ['network and time simulation; outgoing copy; supervisor subprocess replaced by isolated inbound'], state['last_run'])

f = Fixture('T7')
f.ai(); before = (sha(f.mine), sha(f.local))
from ctypes import wintypes
api = ctypes.WinDLL('kernel32', use_last_error=True)
api.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,
                           wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
api.CreateFileW.restype = wintypes.HANDLE
api.CloseHandle.argtypes = [wintypes.HANDLE]
api.CloseHandle.restype = wintypes.BOOL
handle = api.CreateFileW(str(f.mine), 0x80000000, 1, None, 3, 0, None)
assert handle != wintypes.HANDLE(-1).value
locked_error = None
try:
    try:
        f.outgoing_simulated()
    except OSError as exc:
        locked_error = dict(type=type(exc).__name__, winerror=exc.winerror)
finally:
    api.CloseHandle(handle)
assert locked_error and locked_error['winerror'] in (5,32,33)
assert before == (sha(f.mine), sha(f.local))
f.outgoing_simulated(); result = f.inbound()
record(f, 'UNKNOWN', ['AI append', 'native local Win32 deny-delete/write handle on mine fixture',
                     'outgoing replacement denied', 'release handle', 'SIMULATED outgoing retry', 'inbound'],
       'none', 'one append', False, False, False,
       'Real NTFS sharing violation preserves target; retry after releasing the handle succeeds. SMB/Excel parity unknown.',
       ['SIMULATED Excel/SMB lock using real Windows NTFS handle; no native Excel or real SMB'],
       dict(lock_error=locked_error, inbound=result))

f = Fixture('T8')
f.ai(); before = (sha(f.mine), sha(f.local)); reader = sync.readonly_source
@contextmanager
def interrupted_reader(path):
    with reader(path) as stream:
        class Broken:
            calls = 0
            def fileno(self): return stream.fileno()
            def read(self, size):
                self.calls += 1
                if self.calls > 1: raise net_error(64)
                return stream.read(100)
        yield Broken()
try:
    with patch.object(sync, 'readonly_source', interrupted_reader):
        f.outgoing_simulated()
except OSError:
    pass
else:
    raise AssertionError('Expected partial transfer failure')
assert before == (sha(f.mine),sha(f.local))
assert not list(f.mine.parent.glob('.mine-sync-*.partial'))
f.alter(lambda b: setattr(b['851']['E3'], 'value', 'MINE-NEW'))
before = (sha(f.mine),sha(f.local))
try:
    with patch.object(sync, 'readonly_source', interrupted_reader):
        f.inbound()
except OSError as exc:
    assert exc.winerror == 64
assert before == (sha(f.mine),sha(f.local))
assert not list(f.local.parent.glob('.mine-sync-*.partial'))
record(f, 'UNKNOWN', ['AI append', 'outgoing read fails after 100 staged bytes', 'mine edit',
                     'incoming read fails after 100 staged bytes'],
       '851/E3=MINE-NEW', 'one append', False, False, False,
       'Actual staging code removes partial file and preserves both completed workbooks in both directions.',
       ['transfer interruption injected; outbound lifecycle absent; no SMB power-loss durability test'])

f = Fixture('T9')
first = f.ai(); first_hash = sha(f.local)
assert f.ai() == first and sha(f.local) == first_hash
f.outgoing_simulated(); f.inbound(); f.outgoing_simulated(); result = f.inbound()
assert count(f.local,'AI-1') == count(f.mine,'AI-1') == 1
record(f, 'PASS', ['same AI source ID twice', 'SIMULATED outgoing', 'inbound ack',
                   'same outgoing bytes again', 'inbound again'], 'none', 'one source ID twice', False, False, False,
       'UUIDv5 message identity and exact byte acknowledgement are idempotent under this ordering.',
       ['outgoing copy'], result)

f = Fixture('T10')
for n in range(3):
    f.ai(text='AI-BATCH-'+str(n), source='test:'+str(n), devices=('دامپتراک710','بیل851'))
f.alter(lambda b: setattr(b['851']['E3'], 'value', 'MINE-BETWEEN'))
result = f.inbound()
assert count(f.local,'AI-BATCH-') == 6 and count(f.local,'MINE-BETWEEN') == 1
repeat = f.inbound()
assert count(f.local,'AI-BATCH-') == 6
record(f, 'PASS', ['three atomic reports, two devices each', 'mine edits base 851 row', 'inbound', 'repeat inbound'],
       '851 base row edit', 'three operations/six rows across 710 and 851', False, False, False,
       'Ordered journal reconstructs all batches once; repeat inbound unchanged. Mine does not yet contain them.',
       extra=dict(first=result, repeat=repeat))

f = Fixture('T11')
saved = f.ai(); operation = saved['operation']; f.local.write_bytes(f.mine.read_bytes())
result = f.inbound()
assert result['status'] == 'partial' and 'UNJOURNALED_LOCAL_CHANGE' in result['maintenance_guard']['error']
try:
    f.ai()
except writer.EntryError:
    replay_refused = True
else:
    replay_refused = False
assert replay_refused and count(f.local,'AI-1') == 0
with closing(sqlite3.connect(f.runtime/'audit.sqlite3')) as db:
    after = db.execute('SELECT after_hash FROM edits WHERE operation=?',(operation,)).fetchone()[0]
recovered = f.root/'recovered-private.xlsx'
recovered.write_bytes(baseline.snapshot(f.runtime, after))
assert count(recovered,'AI-1') == 1
record(f, 'FAIL', ['AI succeeds', 'FORCED bypass: overwrite synthetic Function with stale mine bytes',
                   'normal inbound guard', 'retry same AI operation', 'recover into a third private evidence file'],
       'none (stale mine bytes)', 'AI append subsequently displaced by explicit fixture overwrite', True, False,
       'DETECTED_UNJOURNALED_LOCAL_CHANGE',
       'Normal sync did not cause overwrite: bypass was deliberate. Guard blocks, replay refuses false success; snapshot permits reviewed recovery.',
       ['forced unsafe manual copy, not a current guarded sync action'],
       dict(inbound=result, replay_refused=replay_refused, recovery_snapshot_sha256=sha(recovered)))

f = Fixture('T12')
f.ai(); f.outgoing_simulated()
f.alter(lambda b: setattr(b['710'].sheet_view, 'zoomScale', 85))
result = f.inbound()
assert count(f.mine,'AI-1') == 1 and count(f.local,'AI-1') == 2
control = Fixture('T12-after-ack-control')
control.ai(); control.outgoing_simulated(); control.inbound()
control.alter(lambda b: setattr(b['710'].sheet_view,'zoomScale',85))
control_result = control.inbound()
assert count(control.local,'AI-1') == 1
record(f, 'FAIL', ['AI append', 'SIMULATED outgoing copy', 'mine opens/saves equivalent contents with zoom change BEFORE ack',
                   'inbound replay', 'control: acknowledge exact bytes BEFORE mine save'],
       'view zoom=85, original AI row unchanged', 'one AI append', False, True, 'IDENTITY_NOT_ACKNOWLEDGED',
       'Before ack: byte inequality causes the existing AI row to be appended again; after ack control has one row.',
       ['openpyxl save/zoom change approximates Excel save; native Excel UNKNOWN; outgoing copy'],
       dict(inbound=result, after_ack_control=control.evidence(), after_ack_result=control_result))

# Verify actual Writer/Sync mutual exclusion, not just sequential correctness.
f = Fixture('X1-lock-order')
f.ai(); start = threading.Event(); finished = threading.Event(); out = {}
def wait_for_sync():
    start.set()
    out['result'] = f.inbound()
    finished.set()
with writer.writer_lock(f.runtime):
    thread = threading.Thread(target=wait_for_sync)
    thread.start(); assert start.wait(3)
    time.sleep(.35)
    assert not finished.is_set()
thread.join(10)
assert finished.is_set() and out['result']['status']=='success'
components = dict(writer_sync_lock='PASS: actual Windows byte-range lock held off inbound until release',
                  lock_artifacts=str(f.root.relative_to(ROOT)))

summary = {v:sum(t['verdict']==v for t in tests) for v in ('PASS','FAIL','UNKNOWN')}
evidence = dict(schema='komatso-ai-excel-mine-share-audit-v1', captured_at_utc=datetime.now(timezone.utc).isoformat(),
                client_date='2026-11-16', timezone='Asia/Tehran', synthetic_only=True, run_root=str(RUN),
                python=sys.executable, platform=sys.platform, openpyxl=openpyxl.__version__,
                code_sha256={str(p.relative_to(ROOT)):sha(p) for p in [
                    ROOT/'integrations/hermes/mine_sync/mine_file_sync.py',
                    ROOT/'tools/fleet/repairs/maintenance_baseline.py', ROOT/'tools/fleet/repairs/maintenance_sync.py',
                    ROOT/'tools/fleet/repairs/entry_service.py', ROOT/'tools/fleet/repairs/maintenance_report.py']},
                verdict_definition=dict(PASS='Observed preservation/idempotency for stated isolated ordering; not certification of outgoing production sync.',
                                        FAIL='Observed loss, duplicate or unresolved semantic conflict in stated scenario.',
                                        UNKNOWN='Isolated primitive executed, but outgoing lifecycle or real SMB/Excel behavior does not exist/is not verified.'),
                summary=summary, tests=tests, extra_components=components,
                constraints=dict(production_workbook_writes=0, production_journal_writes=0, cron_triggers=0,
                                 gateway_restarts=0, bale_sends=0, model_calls=0, new_sync_implementation=False))
target = ROOT/'reports/sync/AI-EXCEL-SYNC-TESTS.json'
target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
print(json.dumps(dict(summary=summary, assertions='PASS', output=str(target), run_root=str(RUN))))
