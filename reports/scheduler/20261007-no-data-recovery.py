"""Authorized 2026-10-07 recovery; sends only through the canonical scheduler."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib, json, logging, sqlite3
from tools.authorization import AuthorizationStore
from tools.scheduler.runner import STATE, CONFIG, load_config, plan_replay, tick, connect, _save_receipt
from tools.scheduler.no_data import current_recipients
from tools.scheduler.tasks import overflow_report_date
from tools.fleet.overflow.report import load_report, DEFAULT_SOURCE
from integrations.hermes.function_domain.driver_report import load_driver, SOURCE_NAME
from integrations.hermes.function_domain.scoped import ScopedReader
from tools.fleet.maintenance_daily.report import source_snapshot, scan

ROOT=Path.cwd(); OUT=ROOT/'reports/scheduler'; TZ=ZoneInfo('Asia/Tehran')
now=datetime.now(TZ)
if now.date().isoformat()!='2026-10-07':raise RuntimeError('This recovery is authorized only for 2026-10-07')
logging.basicConfig(level=logging.INFO,handlers=[logging.FileHandler(ROOT/'runtime/scheduler/scheduler.log',encoding='utf-8')],format='%(asctime)s %(levelname)s %(message)s')
config_digest=hashlib.sha256(CONFIG.read_bytes()).hexdigest()
auth=AuthorizationStore();_,_,jobs=load_config();jobs=[j for j in jobs if j.get('enabled',True)]
# Operational tables are read-only. Compare authorization assignments, not unrelated live events.
def auth_snapshot():
    with closing(sqlite3.connect(auth.path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
        names=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'auth_%'")]
        return {n:sorted(c.execute('SELECT * FROM '+n).fetchall(),key=repr) for n in names}
auth_before=auth_snapshot()
target=overflow_report_date(now.replace(hour=10,minute=0,second=0,microsecond=0))
over=load_report(target);driver=load_driver(target)
with source_snapshot() as (data,digest):maint=scan(data,target)
source_hashes={'overflow':hashlib.sha256(DEFAULT_SOURCE.read_bytes()).hexdigest(),'driver':hashlib.sha256(ScopedReader().snapshot(SOURCE_NAME)).hexdigest(),'maintenance':digest}
source_status={'overflow':{'latest':over['latest_date'],'rows':len(over['rows'])},'driver':{'latest':driver['latest_date'],'sections':{k:{'status':v['status'],'rows':len(v['rows'])} for k,v in driver['sections'].items()}},'maintenance':{'latest':max(maint['available_dates'],default=None),'rows':maint['rows_matched'],'devices':maint['devices_matched']}}
assert all(x['status']!='date_missing' for x in driver['sections'].values())
pre=json.loads((OUT/'20261007-pre-catchup-snapshot.json').read_text(encoding='utf-8'))
log=(ROOT/'runtime/scheduler/scheduler.log').read_text(encoding='utf-8')
for section in ('mechanical','metalwork'):
    assert f'Driver daily section={section} date=1405/07/14 status=date_missing' in log
backup=ROOT/'runtime/scheduler/runs.sqlite3.before-no-data-recovery-20261007'
if not backup.exists():
    with closing(sqlite3.connect(STATE)) as src, closing(sqlite3.connect(backup)) as dst:src.backup(dst)
key=('driver_daily_office_supervisor','2026-10-07T06:30:00+00:00')
with closing(connect(STATE)) as conn:
    # The old caller logged both date_missing documents after successful uploads.
    # Preserve that evidence as notice; message IDs were not recorded by the old caller.
    row=conn.execute('SELECT * FROM runs WHERE schedule_id=? AND due=?',key).fetchone()
    actual=conn.execute("SELECT * FROM receipts WHERE schedule_id=? AND due=? AND delivery_kind='report'",key).fetchall()
    if row['status']=='succeeded' and row['attempt']==0 and not actual:
        for user in pre['recipients_now'][key[0]]:_save_receipt(conn,key,user,'sent',None,'notice')
        with conn:
            changed=conn.execute("UPDATE runs SET status='waiting_for_data',data_state='missing',notice_delivery_state='sent',error='date_missing',next_attempt_at=NULL WHERE schedule_id=? AND due=? AND status='succeeded' AND attempt=0",key).rowcount
            assert changed==1
    # Mechanical legacy success has no IDs or content-status receipts. Do not infer
    # report delivery from duration, and do not blindly resend to those recipients.
    mkey=('driver_daily_mechanical',key[1])
    row=conn.execute('SELECT * FROM runs WHERE schedule_id=? AND due=?',mkey).fetchone()
    actual=conn.execute("SELECT 1 FROM receipts WHERE schedule_id=? AND due=? AND delivery_kind='report'",mkey).fetchone()
    if row['status']=='succeeded' and row['attempt']==0 and not actual:
        for user in pre['recipients_now'][mkey[0]]:_save_receipt(conn,mkey,user,'uncertain',None,'report')
        with conn:conn.execute("UPDATE runs SET status='uncertain',error='legacy_artifact_unverified',next_attempt_at=NULL WHERE schedule_id=? AND due=? AND status='succeeded' AND attempt=0",mkey)
plan=plan_replay(now=datetime.now(TZ))
assert all(p['original_due'] and p['original_due'].startswith('2026-10-07') for p in plan), 'Unexpected historical occurrence; refused'
print('canonical_recovery_plan',json.dumps(plan,indent=2),flush=True)
code=tick(now=datetime.now(TZ),authorization=auth)
with closing(sqlite3.connect(STATE)) as c:
    c.row_factory=sqlite3.Row
    runs=[dict(r) for r in c.execute('SELECT * FROM runs WHERE due LIKE ? ORDER BY schedule_id',('2026-10-07%',))]
    receipts=[dict(r) for r in c.execute('SELECT * FROM receipts WHERE due LIKE ? ORDER BY schedule_id,recipient_id,delivery_kind',('2026-10-07%',))]
# A subsequent canonical tick must not duplicate completed recipients.
second=tick(now=datetime.now(TZ),authorization=auth)
with source_snapshot() as (_,after_maint):pass
after_hashes={'overflow':hashlib.sha256(DEFAULT_SOURCE.read_bytes()).hexdigest(),'driver':hashlib.sha256(ScopedReader().snapshot(SOURCE_NAME)).hexdigest(),'maintenance':after_maint}
result={'inspected_at':datetime.now(TZ).isoformat(),'target':target,'sources':source_status,'runs':runs,'receipts':receipts,'recipients':{j['id']:current_recipients(j['task'],auth) for j in jobs},'recovery_exit':code,'repeat_tick_exit':second,'config_unchanged':config_digest==hashlib.sha256(CONFIG.read_bytes()).hexdigest(),'authorization_unchanged':auth_before==auth_snapshot(),'source_hashes_before':source_hashes,'source_hashes_after':after_hashes,'sources_unchanged':source_hashes==after_hashes,'legacy_office_notice_evidence':'scheduler.log 10:00:06 both sections date_missing; old sender did not retain IDs','legacy_mechanical_evidence':'10:00 succeeded without artifact status or message IDs; report receipt uncertain; no resend'}
(OUT/'20261007-no-data-final-status.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
assert result['config_unchanged'] and result['authorization_unchanged']
