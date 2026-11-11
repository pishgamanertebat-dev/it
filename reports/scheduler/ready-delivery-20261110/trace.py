"""Read-only, exact incident transitions from retained snapshots, journal and logs."""
from pathlib import Path
from contextlib import closing
import sqlite3,json,sys
from datetime import datetime
ROOT=Path('E:/KomatsoAI');OUT=Path(__file__).resolve().parent


def main():
    previous=ROOT/'reports/scheduler/repair-20261110'
    sources=json.loads((previous/'current-sources-before-sync.json').read_text('utf8'))
    source_summary={}
    for name in ('maintenance_local','maintenance_mine'):
        value=sources[name]
        source_summary[name]=dict(sha256=value['sha256'],captured=value['captured'],snapshot=value['snapshot'],dates=[])
        for item in value.get('reports',[]):
            if item['date'] in {'1405/08/17','1405/08/18'}:
                r=item.get('report',{})
                source_summary[name]['dates'].append(dict(date=item['date'],rows=r.get('rows_matched'),devices=r.get('devices_matched'),error=item.get('error')))
    with closing(sqlite3.connect((ROOT/'runtime/maintenance-entry/audit.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        transitions=[dict(r) for r in db.execute("SELECT rowid,* FROM edits WHERE after_hash=?",('78fe91effe2d463a679167c583b11cf94649a9916c983133f6294de18adc3ae0',))]
    logs=[]
    for path in (ROOT/'runtime/scheduler').glob('scheduler.log*'):
        for line in path.read_text('utf8',errors='replace').splitlines():
            if line.startswith('2026-11-10 ') and any(x in line for x in ('Maintenance workbook_sha256=',
                    'maintenance_daily_report original_due=2026-11-10T08:30','maintenance_daily_report original_due=2026-11-09T08:30',
                    '2026-11-10 12:4','2026-11-10 15:3')):
                if ('maintenance_daily_report' in line or 'Maintenance workbook_sha256' in line or 'Scheduler' in line):logs.append(line)
    snapshot=json.loads((OUT/'current-evidence.json').read_text('utf8'))
    holds=[]
    for hold in snapshot['holds']:
        holds.append(dict(schedule_id=hold['schedule_id'],due=hold['due'],created=hold['created'],
            seconds_after_due=(datetime.fromisoformat(hold['created'])-datetime.fromisoformat(hold['due'])).total_seconds(),
            data_deadline=hold['deadline'],previous_run=json.loads(hold['evidence'])['previous_run'],
            current_run=next(r for r in snapshot['runs'] if r['due']==hold['due'])))
    result=dict(source_snapshots=source_summary,sync_journal_transitions=transitions,hold_trace=holds,
        installation_transition=json.loads((previous/'historical-transition.json').read_text('utf8')),
        installation_drain=json.loads((previous/'drain-completed.json').read_text('utf8')),
        first_successful_sync=json.loads((previous/'sync-after.json').read_text('utf8')),
        scheduler_logs=sorted(logs),
        creation_callsite='reports/scheduler/repair-20261110/install_staged.py:125 -> tools/scheduler/recovery_review.py:hold_backlog',
        blocking_callsite='tools/scheduler/runner.py:_claim_replay -> held -> return False before _execute')
    (OUT/'root-cause-trace.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps(dict(sources=source_summary,sync_events=len(transitions),hold_seconds=[h['seconds_after_due'] for h in holds],log_lines=len(logs)),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
