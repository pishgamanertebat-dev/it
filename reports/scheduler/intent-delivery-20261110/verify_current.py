"""Read-only bounded source/ledger audit. Never ticks a job or constructs a sender."""
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT=Path('E:/KomatsoAI');OUT=Path(__file__).resolve().parent
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
sys.path.insert(0,str(ROOT))

def sha(data):return hashlib.sha256(data).hexdigest()
def load(path):return json.loads(Path(path).read_text('utf-8-sig'))
def save(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),'utf8')

def source_child(path):
    path=Path(path);a=path.stat();one=path.read_bytes();two=path.read_bytes();b=path.stat()
    assert (a.st_size,a.st_mtime_ns)==(b.st_size,b.st_mtime_ns) and sha(one)==sha(two),'UnstableSource'
    target=OUT/'sources'/(sha(one)+'.xlsx');target.parent.mkdir(exist_ok=True)
    if target.exists():assert sha(target.read_bytes())==sha(one)
    else:target.write_bytes(one)
    print(json.dumps(dict(path=str(path),status='ok',sha256=sha(one),size=len(one),
        mtime_utc=datetime.fromtimestamp(b.st_mtime,timezone.utc).isoformat(),snapshot=str(target))))

def sources():
    old=load(ROOT/'reports/scheduler/repair-20261110/current-sources.json');result={}
    for name in ('drivers','maintenance_local','oil','performance','overflow'):
        meta=old.get(name)
        if not meta:continue
        local=Path(meta['source']);mine=Path(r'\\192.168.20.251\share')/local.name
        pair={}
        for kind,path in (('server',local),('mine',mine)):
            try:
                p=subprocess.run([sys.executable,'-B','-X','utf8',str(Path(__file__)),'source',str(path)],
                    capture_output=True,text=True,encoding='utf8',timeout=35,creationflags=subprocess.CREATE_NO_WINDOW)
                pair[kind]=json.loads(p.stdout) if p.returncode==0 else dict(status='error',returncode=p.returncode)
            except subprocess.TimeoutExpired:pair[kind]=dict(status='source_timeout',limit_seconds=35)
        pair['hash_equal']=pair['server'].get('sha256') is not None and pair['server'].get('sha256')==pair['mine'].get('sha256')
        result[name]=pair
    save(OUT/'current-sources.json',dict(checked_at=datetime.now(timezone.utc).isoformat(),files=result))
    print(json.dumps({k:dict(equal=v['hash_equal'],server=v['server']['status'],mine=v['mine']['status']) for k,v in result.items()}))

def snapshot_db(source,destination):
    with sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(destination) as b:
        a.backup(b);assert b.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

def ledger(label):
    now=datetime.now(timezone.utc);path=OUT/('ledger-'+label+'.sqlite3');snapshot_db(ROOT/'runtime/scheduler/runs.sqlite3',path)
    c=sqlite3.connect(path);c.row_factory=sqlite3.Row
    tables={r[0]:[dict(x) for x in c.execute('SELECT * FROM '+r[0])] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    save(OUT/('ledger-'+label+'.json'),dict(captured_at_utc=now.isoformat(),captured_at_tehran=now.astimezone(ZoneInfo('Asia/Tehran')).isoformat(),tables=tables))
    from tools.scheduler.tasks import overflow_report_date
    target=lambda d:overflow_report_date(datetime.fromisoformat(d))
    runs=[dict(r,data_date=target(r['due'])) for r in tables['runs'] if target(r['due'])>='1405/08/15']
    proposals=load(ROOT/'reports/scheduler/repair-20261110/recovery-plan.json')['ready_proposals']
    recovery=[]
    for p in proposals:
        receipts=[r for r in tables['receipts'] if r['schedule_id']==p['proposed_receipt_schedule_id'] and r['recipient_id']==p['recipient_id']]
        artifacts=[r for r in tables['office_artifact_receipts'] if r['recipient_id']==p['recipient_id'] and
            (r['schedule_id']==p['proposed_receipt_schedule_id'] or (r['sha256']==p['pdf_sha256']))]
        original=[r for r in tables['receipts'] if r['schedule_id']==p['original_job'] and r['due']==p['original_due'] and r['recipient_id']==p['recipient_id']]
        hold=[r for r in tables['report_recovery_hold'] if r['schedule_id']==p['original_job'] and r['due']==p['original_due']]
        recovery.append(dict(operation_id=p['operation_id'],date=p['data_date'],recipient=p['recipient_id'],
            approved=p['approved'],current_receipts=receipts,current_artifacts=artifacts,original_receipts=original,hold=hold,
            awaiting_owner=not p['approved'] and not receipts and not artifacts and bool(hold)))
    driver=[r for r in tables['office_artifact_receipts'] if r['message_id']=='7366']
    alerts=[r for r in tables['delivery_alert_receipt'] if r['updated'][:10]=='2026-11-10']
    daily=[r for r in tables['manager_daily_receipt'] if r['day']=='2026-11-10']
    summaries=[r for r in tables['manager_daily_snapshot'] if r['day']=='2026-11-10']
    summary_integrity=[]
    for s in summaries:
        messages=json.loads(s['messages']);payload=json.loads(s['payload'])
        summary_integrity.append(dict(hash_valid=sha(s['payload'].encode())==s['sha256'],parts=len(messages),
            captured=s['captured'],counts=payload['counts'],
            expected_receipt_keys_unique=len(daily)==len({(r['day'],r['part'],r['operator_id']) for r in daily}),
            message_ids_unique=len([r['message_id'] for r in daily if r['message_id']])==len({r['message_id'] for r in daily if r['message_id']}),
            owner_closed=[r for r in payload['unresolved'] if r['target'] in {'1405/08/15','1405/08/16'}]))
    selected=[r for r in runs if r['data_date']=='1405/08/18' and r['schedule_id'] in ('oil_balance_daily_report','machinery_performance_daily_report')]
    data18=[dict(r,receipts=[x for x in tables['receipts'] if x['schedule_id']==r['schedule_id'] and x['due']==r['due']]) for r in selected]
    result=dict(captured_at=now.isoformat(),recovery=recovery,pakdel_7366=driver,data18_1500=data18,
        past_1500=now.astimezone(ZoneInfo('Asia/Tehran')).strftime('%H:%M')>='15:00',
        past_1730=now.astimezone(ZoneInfo('Asia/Tehran')).strftime('%H:%M')>='17:30',
        manager_receipts=daily,summary_integrity=summary_integrity,today_alert_receipts=alerts,
        alert_keys_unique=len(alerts)==len({(r['schedule_id'],r['due'],r['episode'],r['alert_kind'],r['operator_id']) for r in alerts}),
        actual_sends_performed=0)
    save(OUT/('delivery-'+label+'.json'),result);c.close()
    print(json.dumps(dict(label=label,at=now.isoformat(),recovery_waiting=sum(r['awaiting_owner'] for r in recovery),
        data18=[dict(job=r['schedule_id'],status=r['status'],error=r['error']) for r in data18],
        manager_receipts=len(daily),alerts=len(alerts))))

def artifacts():
    from tools.fleet.maintenance_daily.report import scan,render,validate_pdf
    s=load(OUT/'current-sources.json')['files']['maintenance_local']
    proposals=load(ROOT/'reports/scheduler/repair-20261110/recovery-plan.json')['ready_proposals'];checks={}
    for p in proposals:
        if p['data_date'] in checks:continue
        raw=Path(p['source_snapshot']).read_bytes();pdf=Path(p['pdf']);report=scan(raw,p['data_date'])
        # Wrapped/continued cells require the renderer's exact fragment geometry.
        # Reconstruct it privately from the pinned source, then validate ORIGINAL bytes.
        private=OUT/('validation-layout-'+p['data_date'].replace('/','-')+'.pdf')
        render(report,private)
        save(private.with_suffix('.expected.json'),report)
        checks[p['data_date']]=dict(source_snapshot_valid=sha(raw)==p['source_sha256'],
            source_matches_server=s['server'].get('sha256')==p['source_sha256'],
            source_matches_mine=s['mine'].get('sha256')==p['source_sha256'],
            pdf_sha256=sha(pdf.read_bytes()),pdf_hash_valid=sha(pdf.read_bytes())==p['pdf_sha256'],
            private_reference_sha256=sha(private.read_bytes()),
            validation=validate_pdf(pdf,report),transport_calls=0)
    save(OUT/'recovery-pdf-validation.json',checks)
    print(json.dumps({k:dict(hash_ok=v['pdf_hash_valid'],fresh=v['source_matches_server'] and v['source_matches_mine']) for k,v in checks.items()}))

def sync():
    state=load(HOME/'state/mine-file-sync.json');save(OUT/'sync-state.json',state)
    failures=[r for r in state.get('recent_runs',[]) if r.get('failure_classification')=='source_timeout']
    print(json.dumps(dict(health_status=state.get('health_status'),sync_healthy=state['last_run'].get('sync_healthy'),
        last_successful_run=state.get('last_successful_run'),last_recovery_at=state.get('last_recovery_at'),
        recent_timeouts=[dict(started=r.get('started_at'),phase=r.get('phase'),worker=r.get('worker_lifecycle')) for r in failures[-3:]],
        maintenance_guard=state['last_run'].get('maintenance_guard'))))

def guard_proof():
    from tools.fleet.repairs import maintenance_baseline as baseline,sync_service,entry_service as entry
    files=load(OUT/'current-sources.json')['files']
    m=files['maintenance_local'];raw=Path(m['mine']['snapshot']).read_bytes()
    source=Path(m['server']['path'])
    snapshot_db(ROOT/'runtime/maintenance-entry/audit.sqlite3',OUT/'maintenance-audit.sqlite3')
    c=sqlite3.connect((OUT/'maintenance-audit.sqlite3').as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    event=dict(c.execute('SELECT * FROM maintenance_baseline_events WHERE source=?',(str(source),)).fetchone())
    payload=json.loads(event['payload']);base_path=Path(payload['snapshot']);base_raw=base_path.read_bytes()
    assert sha(base_raw)==payload['initial_hash'];base=baseline.validate_workbook(base_raw);mine=baseline.validate_workbook(raw)
    try:
        changed=baseline.additions(base,mine)
        misc=[name for name in base.sheetnames if 'متفرقه' in name]
        misc_proof={}
        for name in misc:
            before,after=baseline.sheet_state(base[name]),baseline.sheet_state(mine[name])
            last=max((cell.row for cell in base[name]._cells.values() if cell.value is not None),default=0)
            misc_proof[name]=dict(structural_guard_passed=True,
                changed_fields=[k for k in before if before[k]!=after[k]],baseline_last_content_row=last,
                historical_cells_preserved=all(before['cells'].get(k)==after['cells'].get(k)
                    for k in before['cells'] if __import__('openpyxl').utils.cell.coordinate_from_string(k)[1]<=last),
                additions=[k for k in after['cells'] if k not in before['cells']])
        structure=dict(baseline_event=event['event_id'],baseline_hash=sha(base_raw),current_hash=sha(raw),
            baseline_to_current_additions_accepted=True,changed_sheets=changed,misc_sheet_structure=misc_proof,
            mine_server_hash_equal=m['hash_equal'],rules='existing fail-closed additions() including cells, styles, printing, panes and zoom')
    finally:base.close();mine.close();c.close()
    snapshot_db(ROOT/'runtime/repairs-entry/audit.sqlite3',OUT/'repairs-audit.sqlite3')
    c=sqlite3.connect((OUT/'repairs-audit.sqlite3').as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    d=files['drivers'];source=Path(d['server']['path']);state=c.execute('SELECT * FROM fault_sync_sources WHERE source=?',(str(source),)).fetchone()
    overlays,latest,upstream,local=sync_service.journal_state(c,source,state,ROOT/'runtime/repairs-entry')
    dr=dict(server_sha256=d['server']['sha256'],mine_sha256=d['mine']['sha256'],
        server_equals_journal_head=d['server']['sha256']==latest,mine_equals_recorded_upstream=d['mine']['sha256']==upstream,
        protected_bale_cells=len(overlays),protected_local_changes=local)
    assert dr['server_equals_journal_head'] and dr['mine_equals_recorded_upstream']
    c.close();save(OUT/'sync-guard-proof.json',dict(maintenance=structure,drivers=dr))
    print(json.dumps(dict(maintenance_additions_ok=True,misc_structure=misc_proof,driver_difference_explained_by_journal=True),ensure_ascii=False))

def pakdel():
    from integrations.hermes.function_domain.driver_report import load_driver
    from tools.fleet.repairs.report import section_columns
    c=sqlite3.connect((ROOT/'runtime/scheduler/runs.sqlite3').as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    receipt=dict(c.execute("SELECT * FROM office_artifact_receipts WHERE message_id='7366'").fetchone())
    siblings=[dict(r) for r in c.execute('SELECT * FROM office_artifact_receipts WHERE schedule_id=? AND due=? AND recipient_id=?',
        (receipt['schedule_id'],receipt['due'],receipt['recipient_id']))];c.close()
    meta=load(OUT/'current-sources.json')['files']['drivers']['server'];data=Path(meta['snapshot']).read_bytes()
    report=load_driver('1405/08/18',data=data);columns=section_columns(meta['snapshot'],report['sheet'],'mechanical')
    assert receipt['recipient_id']=='514458396' and receipt['artifact_id']=='mechanical' and receipt['status']=='sent'
    old=load(ROOT/'reports/scheduler/repair-20261110/final-evidence.json')
    historical=[r for r in old['artifacts'] if r.get('message_id')=='7366']
    assert historical and historical[0]['sha256']==receipt['sha256']
    expected=dict(date=report['date'],sheet=report['sheet'],source_sha256=sha(data),
        inventory_rows=len(report['inventory']),nonempty_descriptions=len(report['sections']['mechanical']['rows']),
        selected_export_columns=columns,
        descriptions=[dict(code=r[2],description=r[3]) for r in report['sections']['mechanical']['rows']])
    record=dict(receipt=receipt,sibling_artifacts=siblings,expected=expected,expected_mime_type='application/pdf',
        receipt_hash_matches_prior_audit=True,source_unchanged_since_delivery_audit=meta['sha256']=='d062a9ce21b0e5133180f9264020f5f72d3b008316f808d8a713a702bc29cd05',
        content_gate='build_driver_pdf -> complete per-cell validation -> office_delivery hash check -> document -> artifact receipt',
        original_pdf_retained=False,independent_original_pdf_content_recheck=False,
        limitation='Original temporary delivery PDF is not retained; receipt/type/destination and enforced validation path are verified, received bytes cannot be reopened.',
        transport_calls=0)
    save(OUT/'pakdel-7366.json',record)
    print(json.dumps(dict(recipient=receipt['recipient_id'],artifact=receipt['artifact_id'],sha256=receipt['sha256'],
        expected_descriptions=expected['nonempty_descriptions'],original_pdf_independently_rechecked=False)))

def summary_preview():
    from tools.scheduler.runner import load_config
    from tools.scheduler.daily_summary import snapshot,format_messages
    from tools.authorization import AuthorizationStore
    _,_,jobs=load_config();c=sqlite3.connect((OUT/'ledger-installed.sqlite3').as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    now=datetime.now(timezone.utc);data=snapshot(c,jobs,now,AuthorizationStore());messages=format_messages(data);c.close()
    selected=[r for r in data['unresolved'] if r['target'] in {'1405/08/15','1405/08/16'}]
    assert selected and all(r['state']=='closed' and r.get('owner_closed') and not r['problems'] for r in selected)
    result=dict(preview_only=True,transport_calls=0,captured_at=now.isoformat(),parts=len(messages),counts=data['counts'],
        owner_closed_count=len(selected),owner_closed_verification=True,
        messages=messages,payload=data)
    save(OUT/'summary-preview.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('messages','payload')},ensure_ascii=False))

if __name__=='__main__':
    if sys.argv[1]=='source':source_child(sys.argv[2])
    elif sys.argv[1]=='ledger':ledger(sys.argv[2])
    else:globals()[sys.argv[1]]()
