"""Read-only production proof after natural scheduler activation."""
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,sqlite3,sys
ROOT=Path('E:/KomatsoAI');OUT=Path(__file__).resolve().parent
HOME=Path('C:/Users/win-10/AppData/Local/hermes');sys.path.insert(0,str(ROOT))
from tools.scheduler.recovery_review import held,delivered_recoveries
from tools.authorization import AuthorizationStore
from tools.authorization.maintenance import CAPABILITY

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def rows(conn,table):
    return [dict(r) for r in conn.execute(f'SELECT * FROM {table} WHERE schedule_id=? AND due IN (?,?) ORDER BY due,1',
        ('maintenance_daily_report','2026-11-09T08:30:00+00:00','2026-11-10T08:30:00+00:00'))]

with closing(sqlite3.connect((ROOT/'runtime/scheduler/runs.sqlite3').as_uri()+'?mode=ro',uri=True)) as conn,\
     closing(sqlite3.connect((OUT/'ledger-before.sqlite3').as_uri()+'?mode=ro',uri=True)) as before:
    conn.row_factory=before.row_factory=sqlite3.Row
    installed=json.loads((OUT/'installed.json').read_text('utf8'))
    matches=all(sha(ROOT/name)==digest for name,digest in installed['files'].items())
    preserved={table:rows(conn,table)==rows(before,table) for table in ('runs','receipts','office_artifact_receipts')}
    operations=[dict(r) for r in conn.execute('SELECT * FROM report_recovery_operation ORDER BY original_due,recipient_id')]
    holds=rows(conn,'report_recovery_hold')
    suppression={h['due']:held(conn,('maintenance_daily_report',h['due'])) for h in holds}
    events=[dict(r) for r in conn.execute('SELECT * FROM report_recovery_event ORDER BY id')]
    wakeup=[dict(r) for r in conn.execute('SELECT * FROM report_source_wakeup')] if conn.execute("SELECT 1 FROM sqlite_master WHERE name='report_source_wakeup'").fetchone() else []
    integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
    with closing(sqlite3.connect(OUT/'ledger-final.sqlite3')) as backup:conn.backup(backup)
heartbeat=json.loads((HOME/'state/gateway.heartbeat').read_text('utf8'))
gateway=json.loads((HOME/'state/gateway.lifecycle.json').read_text('utf8'))
sync=json.loads((HOME/'state/mine-file-sync.json').read_text('utf8'))
auth=AuthorizationStore();resolution=auth.resolve_active_recipients(CAPABILITY)
plan=ROOT/'reports/scheduler/repair-20261110/recovery-plan.json'
approval=json.loads((OUT/'execution-authorization.json').read_text('utf8'))
assert len(operations)==4 and all(o['status']=='sent' and o['message_id'] for o in operations)
assert matches and all(preserved.values()) and integrity=='ok'
assert len(holds)==2 and all(h['status']=='recovered' for h in holds) and all(suppression.values())
assert sha(plan)==approval['plan_sha256']
assert not (ROOT/'runtime/scheduler/drain-request.json').exists()
record=dict(verified_at=datetime.now(timezone.utc).isoformat(),installed_code_matches=matches,
    original_records_preserved=preserved,operations=operations,holds=holds,original_occurrences_suppressed=suppression,
    recovery_events=events,natural_scheduler_wakeup=wakeup,integrity=integrity,drain_released=True,
    registry=dict(status=resolution.status,recipients=resolution.recipients,names={u:auth.identity_label(u) for u in resolution.recipients}),
    gateway=gateway,heartbeat=heartbeat,sync_health=dict(status=sync.get('health_status'),healthy=sync.get('sync_healthy'),last_success=sync.get('last_success_at')),
    plan_unchanged=True,transport_calls=0)
(OUT/'production-verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),'utf8')
print(json.dumps(dict(verified_at=record['verified_at'],code_matches=matches,preserved=preserved,wakeup=wakeup,
    gateway=gateway,integrity=integrity,drain_released=True),ensure_ascii=False))
