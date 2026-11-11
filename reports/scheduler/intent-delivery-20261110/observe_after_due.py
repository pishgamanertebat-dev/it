"""One bounded read-only observer for today's 15:00 and 17:30 obligations.

No scheduler tick, sender, DB mutation, task registration or operational Excel write.
Stops after the 17:45 deduplication observation. Local reports are the only outputs.
"""
from datetime import datetime,timezone
import json
import msvcrt
import os
from pathlib import Path
import shutil
import socket
import time
from zoneinfo import ZoneInfo

import verify_current as audit
from build_report import build

OUT=Path(__file__).resolve().parent;TZ=ZoneInfo('Asia/Tehran')

def state(value):
    value=dict(value,pid=os.getpid(),updated_at=datetime.now(timezone.utc).isoformat(),read_only=True,
               transport_calls=0,operational_excel_writes=0)
    temporary=OUT/'observer-state.tmp';audit.save(temporary,value);os.replace(temporary,OUT/'observer-state.json')

def no_network(*args,**kw):raise AssertionError('Observer cannot use HTTP or a message transport')

def compare():
    first=OUT/'delivery-after1730.json';second=OUT/'delivery-after1740.json'
    if not first.exists() or not second.exists():return
    a,b=audit.load(first),audit.load(second)
    receipts=lambda x:{(r['day'],r['part'],r['operator_id']):(r['status'],r['message_id']) for r in x['manager_receipts']}
    left,right=receipts(a),receipts(b)
    prior_terminal={k:v for k,v in left.items() if v[0] in {'sent','uncertain'}}
    valid=b['summary_integrity']
    from tools.scheduler.delivery_alert import _operators
    from tools.authorization import AuthorizationStore
    operators=_operators(AuthorizationStore())
    expected={("2026-11-10",part,operator) for s in valid for part in range(s['parts']) for operator in operators}
    proof=dict(first_observed=a['captured_at'],second_observed=b['captured_at'],
        first_receipts=len(left),second_receipts=len(right),
        prior_terminal_receipts_unchanged=all(right.get(k)==v for k,v in prior_terminal.items()),
        no_new_summary_receipt_keys=set(left)==set(right),
        summary_exists=bool(valid),summary_hash_valid=bool(valid) and all(s['hash_valid'] for s in valid),
        all_observed_summary_parts_sent=bool(right) and all(v[0]=='sent' and v[1] for v in right.values()),
        every_expected_part_and_operator_has_receipt=bool(expected) and expected<=set(right),
        message_ids_unique=bool(valid) and all(s['message_ids_unique'] for s in valid),
        owner_dates_all_closed=bool(valid) and all(r.get('owner_closed') and r['state']=='closed' for s in valid for r in s['owner_closed']),
        today_alert_receipt_count_first=len(a['today_alert_receipts']),today_alert_receipt_count_second=len(b['today_alert_receipts']),
        transport_calls=0)
    audit.save(OUT/'post-due-comparison.json',proof)

def main():
    lock=(OUT/'observer.lock').open('a+b');lock.seek(0,2)
    if not lock.tell():lock.write(b'0');lock.flush()
    lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:raise SystemExit('Observer already running')
    socket.socket.connect=no_network
    import requests
    requests.Session.request=no_network
    points=[('after1500',15,5),('after1510',15,15),('after1730',17,35),('after1740',17,45)]
    try:
        failures=[]
        for label,hour,minute in points:
            due=datetime(2026,11,10,hour,minute,tzinfo=TZ)
            if datetime.now(TZ).date()!=due.date():raise RuntimeError('Observer is scoped to 2026-11-10 only')
            if (OUT/('delivery-'+label+'.json')).exists():continue
            state(dict(status='waiting',next_check=due.isoformat(),label=label))
            while datetime.now(TZ)<due:time.sleep(min(30,max(.1,(due-datetime.now(TZ)).total_seconds())))
            state(dict(status='checking',label=label))
            for name,action in [('ledger',lambda:audit.ledger(label)),('sync',audit.sync)]:
                try:action()
                except Exception as exc:failures.append(dict(label=label,stage=name,error_type=type(exc).__name__))
            if label in {'after1510','after1740'}:
                for name,action in [('sources',audit.sources),('guard',audit.guard_proof),('artifacts',audit.artifacts),('pakdel',audit.pakdel)]:
                    try:action()
                    except Exception as exc:failures.append(dict(label=label,stage=name,error_type=type(exc).__name__))
                if (OUT/'current-sources.json').exists():shutil.copy2(OUT/'current-sources.json',OUT/('sources-'+label+'.json'))
            compare();build()
            state(dict(status='checked',label=label,failures=failures))
        state(dict(status='complete' if not failures else 'complete_with_evidence_gaps',last_check=points[-1][0],failures=failures))
    except Exception as exc:
        state(dict(status='failed',error_type=type(exc).__name__))
        raise
    finally:
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()

if __name__=='__main__':main()
