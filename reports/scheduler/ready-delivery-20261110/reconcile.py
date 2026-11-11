"""No transport: satisfy only the two explicitly approved, fully delivered holds."""
from pathlib import Path
import json,sys
ROOT=Path('E:/KomatsoAI');sys.path.insert(0,str(ROOT))
from tools.scheduler.runner import connect
from tools.scheduler.recovery_delivery import reconcile_holds
from tools.scheduler.recovery_review import delivered_recoveries
from tools.scheduler.delivery_alert import _delivery_summary
from tools.authorization import AuthorizationStore

plan=json.loads((ROOT/'reports/scheduler/repair-20261110/recovery-plan.json').read_text('utf8'))['ready_proposals']
approval=json.loads((Path(__file__).parent/'execution-authorization.json').read_text('utf8'))
assert {p['operation_id'] for p in plan}=={p['operation_id'] for p in approval['operations']}
conn=connect(ROOT/'runtime/scheduler/runs.sqlite3')
try:
    holds=reconcile_holds(conn,plan,approval['approval_ref'])
    summaries=[]
    for due in sorted({p['original_due'] for p in plan}):
        run=dict(conn.execute('SELECT * FROM runs WHERE schedule_id=? AND due=?',('maintenance_daily_report',due)).fetchone())
        summary=_delivery_summary(conn,dict(id='maintenance_daily_report',task='maintenance_daily_report'),run,
                                  ('397185913','514458396'),AuthorizationStore())
        assert len(summary)==2 and all(r['status']=='sent' and r.get('recovery_operation_id') for r in summary)
        summaries.append(dict(due=due,recipients=summary))
    print(json.dumps(dict(holds=holds,summaries=summaries,transport_calls=0),ensure_ascii=False))
finally:conn.close()
