"""Reviewed staged deployment with task drain, immutable backups and no report sends."""
from contextlib import contextmanager,closing
from pathlib import Path
from datetime import datetime,timezone
import sys,os,json,sqlite3,hashlib,shutil,subprocess,time,importlib.util,uuid

ROOT=Path('E:/KomatsoAI');OUT=Path(__file__).resolve().parent
STAGE=ROOT/'runtime/scheduler-repair-20261110/stage'
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
PY=ROOT/'.venv/Scripts/python.exe'
TARGET='c48b5cb'
PHASES=['a22d802','ce0850d','9815d6c','a6d2ab8','42e0e30','d899875','6b7ebbf','ef5c6ce']
sys.path.insert(0,str(STAGE))

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),'utf8')
def command(args,cwd=ROOT):
    p=subprocess.run(args,cwd=cwd,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=60)
    if p.returncode:raise RuntimeError('Command failed: '+str(args[:3])+' '+p.stderr[-1500:])
    return p.stdout.strip()
def git(*args,cwd=ROOT):return command(['git',*args],cwd)
def ps(code):return command(['powershell.exe','-NoProfile','-NonInteractive','-Command',"$ErrorActionPreference='Stop'; "+code])
def backup_db(source,target):
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Invalid SQLite backup')

@contextmanager
def sync_locks():
    path=HOME/'scripts/mine_file_sync.py';spec=importlib.util.spec_from_file_location('deployment_live_sync',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    locks=[]
    try:
        for name in ('mine-file-sync.lock','mine-file-sync-worker.lock'):
            lock=module.acquire_lock(HOME/'state'/name)
            if lock is None:raise ValueError('Active Sync worker; wait for natural completion')
            locks.append(lock)
        state=json.loads((HOME/'state/mine-file-sync-lifecycle.json').read_text('utf8'))
        if module.prior_worker_status(state) not in ('terminated','identity_replaced'):raise ValueError('Sync worker termination unverified')
        from tools.fleet.repairs.entry_service import writer_lock
        with writer_lock(ROOT/'runtime/maintenance-entry'):yield
    finally:
        for lock in reversed(locks):module.release_lock(lock)

def receipt_rows(db):
    return {table:[tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY 1,2,3,4')]
        for table in ('receipts','office_artifact_receipts')}

def main():
    assert not (OUT/'installed.json').exists(),'Already installed'
    assert json.loads((OUT/'scheduler-acceptance.json').read_text('utf8'))['passed']
    assert json.loads((OUT/'manual-ai-net-regression/acceptance.json').read_text('utf8'))['passed']
    assert not git('status','--porcelain','--untracked-files=no',cwd=STAGE)
    assert git('rev-parse','--short','HEAD',cwd=STAGE)==TARGET
    initial=git('rev-parse','HEAD');assert initial.startswith('28142e9')
    assert not git('diff','--name-only') and not git('diff','--cached','--name-only')
    changes=git('diff','--name-status',initial,TARGET).splitlines()
    assert all(line[0] in 'AMD' and len(line.split('\t'))==2 for line in changes)
    backup=OUT/('activation-backup-'+datetime.now(timezone.utc).strftime('%H%M%S'));backup.mkdir()
    files={}
    for line in changes:
        action,name=line.split('\t');source=ROOT/name
        if source.exists():
            dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
            assert sha(dest)==sha(source);files[name]=dict(action=action,before=sha(source),backup=str(dest))
        else:files[name]=dict(action=action,before=None)
    task=json.loads(ps("Get-ScheduledTask -TaskName 'KomatsoAI Schedules' | Select-Object TaskName,State,@{n='Enabled';e={$_.Settings.Enabled}} | ConvertTo-Json"))
    assert task['Enabled'],'Scheduler was already disabled; do not change owner state'
    export=backup/'scheduler-task.xml'
    ps("Export-ScheduledTask -TaskName 'KomatsoAI Schedules' | Set-Content -Encoding Unicode -LiteralPath '"+str(export)+"'")
    save('deployment-before.json',dict(initial_commit=initial,target=TARGET,task=task,files=files))
    backup_db(ROOT/'runtime/scheduler/runs.sqlite3',backup/'ledger-before-drain.sqlite3')
    from tools.scheduler.control import worker_lock,write_record
    control=ROOT/'runtime/scheduler';token=uuid.uuid4().hex
    draining=False;installed=[]
    try:
        write_record(control/'drain-request.json',dict(token=token,reason='owner-authorized staged scheduler repair',created=datetime.now(timezone.utc).isoformat()))
        draining=True
        # Bootstrap only the tested drain shim, with the module present before
        # the entrypoint references it. Existing workers finish their old code.
        for name in ('tools/scheduler/control.py','tools/scheduler/test_control.py'):
            target=ROOT/name;temp=target.with_name('.bootstrap-'+uuid.uuid4().hex+'.tmp')
            temp.write_bytes((STAGE/name).read_bytes());os.replace(temp,target)
        runner=ROOT/'tools/scheduler/runner.py';original=runner.read_text('utf8')
        marker='        return tick(args.config, args.state)'
        assert original.count(marker)==1
        changed=original.replace(marker,'        from .control import scheduled_tick\n        return scheduled_tick(lambda: tick(args.config,args.state),args.state)')
        temp=runner.with_name('.bootstrap-'+uuid.uuid4().hex+'.tmp');temp.write_text(changed,'utf8');os.replace(temp,runner)
        git('add','tools/scheduler/control.py','tools/scheduler/test_control.py','tools/scheduler/runner.py')
        git('commit','-m','Add cooperative scheduler drain without changing Windows task permissions')
        bootstrap=git('rev-parse','HEAD')
        command([str(PY),'-E','-s','-B','-X','utf8','-m','tools.scheduler.runner','--tick'])
        ack=json.loads((control/'drain-ack.json').read_text('utf8'));assert ack['token']==token and ack['state']=='drained'
        save('drain-acknowledged.json',dict(bootstrap_commit=bootstrap,ack=ack,task_acl_unchanged=True))
        print('Cooperative drain acknowledged; waiting for old workers naturally',flush=True)
        deadline=time.monotonic()+600
        while True:
            active=ps("@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -match '-m tools.scheduler.runner --tick'}).Count")
            if active=='0':break
            if time.monotonic()>deadline:raise TimeoutError('Scheduler did not drain; no forced termination')
            time.sleep(2)
        with worker_lock(control) as acquired, sync_locks():
            assert acquired,'Scheduler worker lock is not drained'
            from tools.fleet.repairs.maintenance_baseline import baseline_reader
            source_records={}
            for name in ('repairs','maintenance'):
                config=json.loads((ROOT/f'settings/{name}_entry.json').read_text('utf8'));source=Path(config['source'])
                with baseline_reader(source) as stream:data=stream.read()
                dest=backup/source.name;dest.write_bytes(data);assert sha(dest)==hashlib.sha256(data).hexdigest()
                source_records[str(source)]=sha(dest)
                backup_db(ROOT/f'runtime/{name}-entry/audit.sqlite3',backup/f'{name}-audit.sqlite3')
            backup_db(ROOT/'reports/telegram_usage/telegram_users.db',backup/'authorization.sqlite3')
            backup_db(ROOT/'runtime/scheduler/runs.sqlite3',backup/'ledger.sqlite3')
            assert sha(HOME/'scripts/mine_file_sync.py')
            from tools.scheduler.runner import connect,load_config,_expire_missing_reports
            from tools.scheduler.recovery_review import hold_backlog
            from tools.scheduler.daily_summary import ensure_schema
            from tools.authorization import AuthorizationStore
            now=datetime.now(timezone.utc);jobs=load_config(STAGE/'settings/schedules.yaml')[2]
            store=AuthorizationStore(ROOT/'reports/telegram_usage/telegram_users.db')
            with closing(connect(ROOT/'runtime/scheduler/runs.sqlite3')) as db:
                original_receipts=receipt_rows(db)
                expired={j['id']:sorted(_expire_missing_reports(db,j,now)) for j in jobs}
                holds=hold_backlog(db,jobs,now,store)
                ensure_schema(db)
                # Correction records supplement, never replace, initial receipts.
                c=json.loads((ROOT/'runtime/metalwork-pdf-fix-20261109/correction-manifest.json').read_text('utf8'))
                due='2026-11-09T05:30:00+00:00'
                with db:
                    for user,mid in [('397185913','7347'),('514458396','7348')]:
                        evidence=dict(receipt_schedule_id=c['receipt_schedule_id'],receipt_due=c['receipt_due'],message_id=mid,pdf_sha256=c['pdf_sha256'])
                        row=db.execute('SELECT * FROM office_artifact_receipts WHERE schedule_id=? AND due=? AND recipient_id=? AND message_id=?',(c['receipt_schedule_id'],c['receipt_due'],user,mid)).fetchone()
                        assert row and row['status']=='sent' and row['sha256']==c['pdf_sha256']
                        db.execute('INSERT OR IGNORE INTO report_correction_review VALUES (?,?,?,?,?,?,?,?)',
                            ('driver_daily_office_supervisor',due,user,'metalwork','sent','Validated independent correction; initial receipt preserved',json.dumps(evidence),now.isoformat()))
                    db.execute('INSERT OR IGNORE INTO report_correction_review VALUES (?,?,?,?,?,?,?,?)',
                        ('driver_daily_metalwork',due,'387679249','metalwork','needs_content_review',
                         'Original 7321 payload and recipient-specific hash not retained; content defect unverified',
                         json.dumps(dict(original_message_id='7321',candidate_pdf=c['pdf'],candidate_sha256=c['pdf_sha256'])),now.isoformat()))
                assert receipt_rows(db)==original_receipts,'Historical receipts changed during migration'
                assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            save('historical-transition.json',dict(expired=expired,holds=holds,report_receipts_unchanged=True,recorded_at=now.isoformat()))
            for revision in PHASES:
                print('Installing checkpoint '+revision,flush=True)
                git('cherry-pick',revision)
                check=command([str(PY),'-E','-s','-B','-X','utf8','-m','tools.scheduler.runner','--check'])
                installed.append(dict(commit=git('rev-parse','HEAD'),source_commit=revision,check=check,installed_at=datetime.now(timezone.utc).isoformat()))
                save('deployment-checkpoints.json',installed)
            assert all(sha(path)==expected for path,expected in source_records.items()),'Workbook changed during code deployment'
            assert not git('diff','--name-only',TARGET,'HEAD'),'Installed tree differs from tested candidate'
            for name,entry in files.items():entry['after']=sha(ROOT/name) if (ROOT/name).exists() else None
            save('installed.json',dict(success=True,commits=installed,backup=str(backup),files=files,
                source_sha256=source_records,original_report_receipts_unchanged=True,
                actual_test_messages=0,actual_historical_report_sends=0,excel_test_writes=0,
                gateway_restart=False,installed_at=datetime.now(timezone.utc).isoformat()))
            print('All checkpoints installed; receipts and source workbooks preserved',flush=True)
    except BaseException as exc:
        save('deployment-failure.json',dict(error=type(exc).__name__,commits=installed,backup=str(backup)))
        raise
    finally:
        if draining and (OUT/'installed.json').exists():
            request=json.loads((control/'drain-request.json').read_text('utf8'));assert request['token']==token
            (control/'drain-request.json').unlink()
            save('drain-completed.json',dict(token=token,completed=datetime.now(timezone.utc).isoformat(),task_trigger_unchanged=True))
            print('Cooperative drain released; original task trigger remains enabled',flush=True)
        elif draining:
            print('Drain retained until the concrete installation failure is repaired',flush=True)

if __name__=='__main__':main()
