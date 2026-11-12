"""Select/aggregate only. No application imports and no live share access."""
from collect_readonly import *
import ast
import io
import re
import yaml

NOW = datetime.now(timezone.utc).isoformat()
def rows(db, sql, args=()):
    return [dict(r) for r in db.execute(sql,args)]

def scheduler():
    db = snapshot(DBS['K-SCHED'])
    auth = snapshot(DBS['K-AUTH'])
    cfg = yaml.safe_load((K/'settings/schedules.yaml').read_text('utf8'))
    facts = dict(observed_utc=NOW,registry_path=str(K/'settings/schedules.yaml'),registry_sha256=sha(K/'settings/schedules.yaml'),jobs=[])
    for job in cfg['schedules']:
        cap = job['recipient_capability']
        eligible = rows(auth,"""SELECT DISTINCT ur.user_id,ur.role FROM auth_user_roles ur
            JOIN auth_role_capabilities rc ON rc.role=ur.role
            JOIN channel_users cu ON cu.platform=ur.platform AND cu.user_id=ur.user_id
            WHERE ur.platform='bale' AND ur.active=1 AND rc.capability=?
            AND cu.registration_status='approved' AND cu.chat_id=cu.user_id ORDER BY ur.user_id,ur.role""",(cap,))
        item = dict(job, eligible_recipients=eligible)
        item['run_statuses']=rows(db,'SELECT status,COUNT(*) count FROM runs WHERE schedule_id=? GROUP BY status',(job['id'],))
        item['latest_run']=rows(db,'SELECT schedule_id,due,status,attempt,started,finished,error,data_state,notice_delivery_state,next_attempt_at FROM runs WHERE schedule_id=? ORDER BY due DESC LIMIT 1',(job['id'],))
        item['range']=rows(db,'SELECT MIN(due) first_due,MAX(due) last_due,COUNT(*) count FROM runs WHERE schedule_id=?',(job['id'],))[0]
        item['receipt_statuses']=rows(db,'SELECT delivery_kind,status,COUNT(*) count,SUM(message_id IS NOT NULL) with_message_id FROM receipts WHERE schedule_id=? GROUP BY delivery_kind,status',(job['id'],))
        item['artifact_statuses']=rows(db,'SELECT artifact_id,status,COUNT(*) count,SUM(message_id IS NOT NULL) with_message_id,SUM(sha256 IS NOT NULL) with_hash FROM office_artifact_receipts WHERE schedule_id=? GROUP BY artifact_id,status',(job['id'],))
        facts['jobs'].append(item)
    facts['all_receipt_statuses']=rows(db,'SELECT delivery_kind,status,COUNT(*) count,SUM(message_id IS NOT NULL) with_message_id FROM receipts GROUP BY delivery_kind,status')
    facts['run_success_without_report_proof']=rows(db,"""SELECT r.schedule_id,COUNT(*) count FROM runs r WHERE r.status='succeeded'
       AND NOT EXISTS(SELECT 1 FROM receipts x WHERE x.schedule_id=r.schedule_id AND x.due=r.due AND x.delivery_kind='report' AND x.status='sent' AND x.message_id IS NOT NULL)
       GROUP BY r.schedule_id""")
    facts['office_artifacts_sent_without_message_id']=rows(db,"SELECT schedule_id,artifact_id,COUNT(*) count FROM office_artifact_receipts WHERE status='sent' AND message_id IS NULL GROUP BY schedule_id,artifact_id")
    facts['recoveries']=rows(db,"""SELECT o.operation_id,o.original_schedule,o.original_due,o.target_date,o.recipient_id,o.sha256,o.source_sha256,o.status,o.message_id,o.updated,
       EXISTS(SELECT 1 FROM receipts r JOIN office_artifact_receipts a ON a.schedule_id=r.schedule_id AND a.due=r.due AND a.recipient_id=r.recipient_id
       WHERE r.schedule_id=o.receipt_schedule AND r.due=o.original_due AND r.recipient_id=o.recipient_id AND r.delivery_kind='report' AND r.status='sent' AND r.message_id=o.message_id
       AND a.artifact_id=o.operation_id AND a.status='sent' AND a.message_id=o.message_id AND a.sha256=o.sha256) receipt_chain_matches FROM report_recovery_operation o""")
    facts['holds']=rows(db,'SELECT schedule_id,status,reason,COUNT(*) count FROM report_recovery_hold GROUP BY schedule_id,status,reason')
    facts['closures']=rows(db,'SELECT schedule_id,due,deadline,closed_at,policy FROM missing_data_closures')
    facts['corrections']=rows(db,'SELECT schedule_id,due,recipient_id,artifact_id,status,reason,updated FROM report_correction_review')
    facts['alerts']=rows(db,'SELECT exception_status,recovery_status,COUNT(*) count FROM delivery_alert_case GROUP BY exception_status,recovery_status')
    facts['latest_manager_snapshot']=rows(db,'SELECT day,captured,sha256 FROM manager_daily_snapshot ORDER BY day DESC LIMIT 1')
    save('sanitized/scheduler-facts.json',facts)
    print('SCHEDULES',[(j['id'],len({r['user_id'] for r in j['eligible_recipients']}),j['latest_run'][0]['status']) for j in facts['jobs']])
    print('PROOF_GAPS',facts['run_success_without_report_proof'],facts['office_artifacts_sent_without_message_id'])
    db.close();auth.close()

def runtime():
    state = json.loads((M/'state/mine-file-sync.json').read_text('utf8'))
    allowed = ['schema_version','reliability_schema_version','layout','last_attempt_at','last_success_at','last_successful_run','last_completed_scan','last_clock_at','sync_healthy','health_status','outage','outage_since','consecutive_source_failures','next_attempt_at','last_failure_classification','last_recovery_at']
    sync = {k:state.get(k) for k in allowed}
    run = state.get('last_run',{})
    sync['last_run'] = {k:run.get(k) for k in ['status','source_enumerated','started_at','finished_at','errors','scanned','copied','updated','skipped_locked','skipped_unstable','collisions','maintenance_guard','fault_merge','failure_classification','duration_seconds']}
    sync['guard_evidence'] = {k:v for k,v in run.items() if ('guard' in k or 'conflict' in k or 'merge' in k)}
    save('sanitized/sync-facts.json',dict(observed_utc=NOW,source_state=str(M/'state/mine-file-sync.json'),**sync))
    gateway = json.loads((M/'gateway_state.json').read_text('utf8'))
    gateway = {k:gateway.get(k) for k in ['pid','gateway_state','restart_requested','active_agents','platforms','updated_at','code_sha','code_version','served_profiles','session_store']}
    gateway['source']=str(M/'gateway_state.json');gateway['observed_utc']=NOW
    save('sanitized/gateway-facts.json',gateway)
    jobs=json.loads((M/'cron/jobs.json').read_text('utf8'))
    safe=[{k:j.get(k) for k in ['id','name','script','no_agent','schedule','enabled','state','last_run_at','last_status','last_error','failure_streak','next_run_at']} for j in jobs['jobs']]
    save('sanitized/cron-facts.json',dict(observed_utc=NOW,jobs=safe))
    h = dict(observed_utc=NOW)
    for label, path, keys in [('health',H/'integrations/bale/.local/health.json',['last_successful_poll_utc','worker_alive']),('process',H/'integrations/bale/.local/process.json',['state','pid','started_at_utc'])]:
        d=json.loads(path.read_text('utf8'));h[label]={k:d.get(k) for k in keys}
    manifest=H/'integrations/bale/.local/installed_manifest.json'
    d=json.loads(manifest.read_text('utf8'))
    h['initial_install_manifest_matches']={name:(sha(H/'integrations/bale'/name)==digest) for name,digest in d['files'].items() if (H/'integrations/bale'/name).is_file()}
    h['initial_install_manifest_is_current']=all(h['initial_install_manifest_matches'].values())
    save('sanitized/hrms-runtime.json',h)
    print('SYNC',sync)

def rules_and_outputs():
    rule_items=[]
    allowed=['schema_version','version','group','label','effective_from','effective_to','start','end','break','fingerprint_deadline','required_exit','overtime_allowed','overtime_policy','overtime_entry_before','overtime_threshold_minutes','overtime_threshold_scope','overtime_column','overtime_source_contract','friday_overtime_policy','payroll_deduction_policy','presence_deduction_policy','night_interval_policy','paid_break','fixed_work_minutes','display_nonpayable_extra_presence','monthly_leave_quota_minutes','thursday_end','working_weekdays','calendar_work_days','supersedes']
    catalog=json.loads((OUT/'evidence/sanitized/rule-catalog.json').read_text('utf8'))
    for r in catalog:
        if r['rule_file']:
            d=json.loads((H/'rules'/r['rule_file']).read_text('utf-8-sig'))
            rule_items.append(dict(source=r['rule_file'],scope_count=r['personnel_scope_count'],**{k:d[k] for k in allowed if k in d}))
    save('sanitized/rule-contracts.json',rule_items)
    # Latest saved run is not automatically a published result or a live inbox job.
    manifests=[p for p in (H/'runtime/bale').glob('*/reports/*/*/manifest.json')]
    latest=sorted(manifests,key=lambda p:p.stat().st_mtime,reverse=True)[:5]
    result=[]
    for p in latest:
        d=json.loads(p.read_text('utf8'))
        item=dict(path=str(p),sha256=sha(p),modified_utc=stat(p)['modified_utc'],keys=sorted(d))
        for key in ['schema_version','content_id','started_at_utc','finished_at_utc','employees','forms_generated','hr_approval_state','publication_decision','status','run_status','period','scope','month','year']:
            if key in d and key!='scope': item[key]=d[key]
        hashes=d.get('code_sha256',{})
        item['code_matches_current']={name:sha(H/name)==digest for name,digest in hashes.items() if (H/name).is_file()}
        item['current_code_match_count']=sum(item['code_matches_current'].values())
        item['recorded_code_count']=len(hashes)
        item['nearby_files']=[str(f.relative_to(p.parent)) for f in p.parent.iterdir() if f.is_file()]
        # Record shape and aggregates only. Never retain names, punch rows or ids.
        for name in ['result.json','daily.json','summary.json','employee_summary.json']:
            f=p.parent/name
            if f.is_file():
                obj=json.loads(f.read_text('utf8'))
                item[name+'_shape']=dict(type=type(obj).__name__,count=len(obj),keys=list(obj) if isinstance(obj,dict) else [])
        result.append(item)
    save('sanitized/hrms-saved-runs.json',result)
    print('HRMS_LATEST',[(r['path'],r.get('hr_approval_state'),r.get('publication_decision'),r['current_code_match_count'],r['recorded_code_count']) for r in result])

def source_index():
    groups=[(K/'tools/scheduler','*.py'),(K/'tools/authorization','*.py'),(K/'tools/fleet/repairs','*.py'),(K/'tools/fleet/maintenance_daily','*.py'),(K/'tools/fleet/oil_balance','*.py'),(K/'tools/fleet/machinery_performance','*.py'),(K/'tools/fleet/work_orders','**/*.py'),(K/'integrations/hermes','*.py'),(H/'hrms','*.py'),(H/'integrations/bale/hrms_bale','*.py')]
    result=[]
    for folder,pattern in groups:
        for p in sorted(folder.glob(pattern)):
            if p.name.startswith('test') or '__pycache__' in str(p):continue
            s=p.read_text('utf-8-sig')
            try:
                tree=ast.parse(s)
                anchors=[dict(symbol=n.name,line=n.lineno,end_line=n.end_lineno) for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
            except SyntaxError:anchors=[]
            result.append(dict(path=str(p),sha256=sha(p),lines=len(s.splitlines()),anchors=anchors))
    for p in [M/'scripts/mine_file_sync.py',M/'plugins/komatso-bale-registry/__init__.py',K/'integrations/hermes/plugins/komatso-bale-registry/__init__.py']:
        result.append(dict(path=str(p),sha256=sha(p),lines=len(p.read_text('utf8').splitlines())))
    save('inventory/code-index.json',result)
    matched=sha(M/'plugins/komatso-bale-registry/__init__.py')==sha(K/'integrations/hermes/plugins/komatso-bale-registry/__init__.py')
    save('sanitized/installed-code.json',dict(observed_utc=NOW,komatso_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=K,text=True).strip(),bale_plugin_byte_match=matched,hrms_git='not_a_repository',loaded_python_module_hashes='unknown_no_process_memory_inspection'))
    # Extract DDL constants from installed code. Never pretend this is a live DB schema.
    inferred={}
    for key,path in [('H-PERSONNEL',H/'hrms/personnel.py'),('H-BOT',H/'integrations/bale/hrms_bale/store.py')]:
        tree=ast.parse(path.read_text('utf8'))
        ddl=[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'CREATE TABLE' in n.value]
        db=sqlite3.connect(':memory:')
        db.row_factory=sqlite3.Row
        for s in ddl:db.executescript(s)
        inferred[key]=dict(confidence='code_schema_only_not_live_verified',path=str(path),tables=rows(db,"SELECT name,sql FROM sqlite_master WHERE type IN ('table','trigger')"))
        db.close()
    save('inventory/hrms-code-schemas.json',inferred)

if __name__=='__main__':
    scheduler();runtime();rules_and_outputs();source_index()
