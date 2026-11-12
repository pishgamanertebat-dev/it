"""Audit collector: no application imports, no source writes, no raw personal rows.
Run only with -B. SQLite backups live in memory and are discarded after aggregation.
"""
from pathlib import Path
from datetime import datetime, timezone
import csv
import hashlib
import importlib.metadata as metadata
import json
import platform
import sqlite3
import subprocess
import time
import sys
from zoneinfo import ZoneInfo

OUT = Path(__file__).resolve().parents[2]
K = Path('E:/KomatsoAI')
H = Path('E:/HRMS')
M = Path('C:/Users/win-10/AppData/Local/hermes')

def save(name, value):
    (OUT / 'evidence' / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def stat(path):
    try:
        s = path.stat()
        return dict(path=str(path), bytes=s.st_size, modified_utc=datetime.fromtimestamp(s.st_mtime, timezone.utc).isoformat())
    except OSError as e:
        return dict(path=str(path), access='unavailable', error_type=type(e).__name__)

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()

def snapshot(path):
    # Never call project store constructors: several initialize/migrate on read.
    src = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    dst = sqlite3.connect(':memory:')
    src.execute('PRAGMA query_only=ON')
    deadline = time.monotonic() + 10
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError('snapshot_budget_exceeded')
    try:
        src.backup(dst, pages=256, progress=progress, sleep=.01)
    finally:
        src.close()
    dst.row_factory = sqlite3.Row
    return dst

DBS = {
    'K-SCHED': K/'runtime/scheduler/runs.sqlite3',
    'K-AUTH': K/'reports/telegram_usage/telegram_users.db',
    'K-FLEET': K/'data/fleet/db/fleet_ops.db',
    'K-ENTRY': K/'runtime/repairs-entry/audit.sqlite3',
    'K-MAINT-ENTRY': K/'runtime/maintenance-entry/audit.sqlite3',
    'H-PERSONNEL': H/'data/personnel/personnel.sqlite3',
    'H-BOT': H/'integrations/bale/.local/bot.sqlite3',
}

def collect_dbs():
    result = {}
    for key, path in DBS.items():
        row = stat(path)
        if not path.is_file():
            result[key] = row
            continue
        try:
            db = snapshot(path)
            row['user_version'] = db.execute('PRAGMA user_version').fetchone()[0]
            row['schema_version'] = db.execute('PRAGMA schema_version').fetchone()[0]
            tables = {}
            for t in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
                name = t['name']
                q = '"' + name.replace('"', '""') + '"'
                cols = [dict(c) for c in db.execute('PRAGMA table_info(' + q + ')')]
                tables[name] = dict(columns=cols, count=db.execute('SELECT COUNT(*) FROM '+q).fetchone()[0], ddl=t['sql'])
            row['tables'] = tables
            row['integrity_check'] = db.execute('PRAGMA quick_check').fetchone()[0]
            db.close()
            row['access'] = 'verified_readonly_memory_snapshot'
        except (OSError, sqlite3.Error, TimeoutError) as e:
            row['access'] = 'unavailable'
            row['error_type'] = type(e).__name__
            row['error_code'] = getattr(e, 'sqlite_errorname', None)
            row['error_message'] = str(e)
        result[key] = row
    save('inventory/database-schemas.json', result)
    print(json.dumps({k: { 'access':v.get('access'), 'tables':{n:t['count'] for n,t in v.get('tables',{}).items()}} for k,v in result.items()}, ensure_ascii=False))

def collect_files():
    files = []
    for folder in [K/'settings', H/'rules', H/'data/hr', H/'data/personnel', H/'integrations/bale/.local', K/'tools/scheduler']:
        for p in sorted(folder.glob('*')):
            if not p.is_file() or p.suffix in {'.db','.sqlite3','.log','.lock'} or p.name.startswith('.'):
                continue
            # Filenames/hashes only; never store raw HR rows or settings content.
            r = stat(p)
            if p.suffix in {'.py','.json','.yaml','.csv','.ps1','.toml','.txt'} and p.stat().st_size < 2_000_000:
                r['sha256'] = sha(p)
            files.append(r)
    save('inventory/source-files.json', files)
    rules = []
    with (H/'rules/rule_versions.csv').open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            item = {k:r[k] for k in ['group_code','version','effective_from','effective_to','state','rule_file']}
            item['personnel_scope_count'] = len([p for p in r.get('personnel_ids','').split(';') if p])
            if item['rule_file']:
                p = H/'rules'/item['rule_file']
                item['sha256'] = sha(p)
            rules.append(item)
    save('sanitized/rule-catalog.json', rules)
    packages = {}
    for name in ['openpyxl','PyMuPDF','APScheduler','PyYAML','requests','jdatetime','fastapi','uvicorn','httpx','pytest','xlrd','python-dotenv','tzdata']:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    save('inventory/python-runtime.json', dict(observed_utc=datetime.now(timezone.utc).isoformat(), executable=sys.executable, python=platform.python_version(), sqlite=sqlite3.sqlite_version, packages=packages))

def collect_native_infra():
    import ctypes
    import shutil
    import winreg
    class Memory(ctypes.Structure):
        _fields_ = [('length',ctypes.c_ulong),('load',ctypes.c_ulong),
                   *[(n,ctypes.c_ulonglong) for n in ['total_physical','available_physical','total_page','available_page','total_virtual','available_virtual','available_extended']]]
    mem = Memory()
    mem.length = ctypes.sizeof(mem)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
    result = dict(observed_utc=datetime.now(timezone.utc).isoformat(), os=platform.platform(),
                  logical_cpu=__import__('os').cpu_count(),memory={n:getattr(mem,n) for n,_ in mem._fields_},
                  disks={v:dict(zip(['total','used','free'],shutil.disk_usage(v))) for v in ['C:/','E:/']},
                  cim_access='denied_hresult_0x80041003')
    result['registry'] = {}
    for label, key, names in [('windows',r'SOFTWARE\Microsoft\Windows NT\CurrentVersion',['ProductName','DisplayVersion','CurrentBuildNumber']),
                              ('hardware',r'HARDWARE\DESCRIPTION\System\BIOS',['SystemManufacturer','SystemProductName']),
                              ('cpu',r'HARDWARE\DESCRIPTION\System\CentralProcessor\0',['ProcessorNameString','~MHz'])]:
        values = {}
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,key) as reg:
                for name in names:
                    try: values[name] = winreg.QueryValueEx(reg,name)[0]
                    except OSError: values[name] = None
        except OSError:
            values['access'] = 'denied'
        result['registry'][label] = values
    save('inventory/infrastructure-native.json', result)
    print(json.dumps(result,ensure_ascii=False))

if __name__ == '__main__':
    collect_dbs()
    collect_files()
    collect_native_infra()
