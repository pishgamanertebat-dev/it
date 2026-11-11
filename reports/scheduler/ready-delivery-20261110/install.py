"""Atomic installation under the existing cooperative scheduler drain and lock."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib,json,os,shutil,sqlite3,subprocess,sys,time,uuid
from pathlib import Path

ROOT=Path('E:/KomatsoAI');STAGE=ROOT/'runtime/ready-delivery-stage-20261110'
OUT=Path(__file__).resolve().parent;PY=ROOT/'.venv/Scripts/python.exe'
sys.path.insert(0,str(ROOT))
from tools.scheduler.control import worker_lock,write_record

FILES=[
 'tools/fleet/maintenance_daily/prepared.py','tools/scheduler/report_errors.py','tools/scheduler/misfire.py',
 'tools/scheduler/recovery_review.py','tools/scheduler/recovery_delivery.py','tools/fleet/maintenance_daily/delivery.py',
 'tools/scheduler/preflight.py','tools/scheduler/delivery_alert.py','tools/scheduler/runner.py',
 'tools/scheduler/test_preflight.py','tools/scheduler/test_recovery_delivery.py','tools/scheduler/test_recovery_review.py',
 'tools/scheduler/test_source_wakeup.py','tools/fleet/maintenance_daily/test_prepared.py',
 'tools/fleet/maintenance_daily/test_report.py','tools/scheduler/README.md']


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ledger_rows(conn):
    return {t:sorted([list(r) for r in conn.execute('SELECT * FROM '+t)],key=repr)
            for t in ('runs','receipts','office_artifact_receipts')}


def run(args):
    result=subprocess.run([str(PY),'-X','utf8',*args],cwd=ROOT,capture_output=True,text=True,encoding='utf8')
    if result.returncode:raise RuntimeError(result.stderr[-2500:])
    return result.stdout


def main():
    acceptance=json.loads((OUT/'acceptance.json').read_text('utf8'))
    baseline=json.loads((OUT/'net-baseline-class.json').read_text('utf8'))
    assert (not acceptance['external_attempts'] and acceptance['errors']==0
            and set(acceptance['failed_ids'])==set(baseline['failed_ids'])
            and not baseline['errors'] and not baseline['external_attempts']), 'Unresolved new regression'
    expected=json.loads((OUT/'tested-candidate.json').read_text('utf8'))
    assert expected=={p:sha(STAGE/p) for p in FILES},'Candidate changed after validation'
    token=uuid.uuid4().hex;control=ROOT/'runtime/scheduler';request=control/'drain-request.json'
    assert not request.exists(),'Another deployment has a drain pending'
    backup=OUT/('activation-backup-'+token[:8]);backup.mkdir()
    write_record(request,dict(token=token,reason='owner-authorized ready-artifact delivery repair',created=datetime.now(timezone.utc).isoformat()))
    installed=[];before_holds=None
    try:
        for attempt in range(90):
            with worker_lock(control) as acquired:
                if not acquired:
                    time.sleep(1)
                    continue
                with closing(sqlite3.connect(control/'runs.sqlite3')) as conn:
                    conn.row_factory=sqlite3.Row
                    with closing(sqlite3.connect(backup/'ledger.sqlite3')) as dest:conn.backup(dest)
                    before=ledger_rows(conn)
                    before_holds=[dict(r) for r in conn.execute('SELECT * FROM report_recovery_hold ORDER BY schedule_id,due')]
                with closing(sqlite3.connect((ROOT/'reports/telegram_usage/telegram_users.db').as_uri()+'?mode=ro',uri=True)) as conn:
                    with closing(sqlite3.connect(backup/'authorization.sqlite3')) as dest:conn.backup(dest)
                for name in FILES:
                    target=ROOT/name;dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True)
                    if target.exists():shutil.copy2(target,dest)
                    temp=target.with_name('.ready-install-'+token+'.tmp')
                    temp.write_bytes((STAGE/name).read_bytes());os.replace(temp,target);installed.append(name)
                assert {p:sha(ROOT/p) for p in FILES}==expected
                index=subprocess.check_output(['git','diff','--cached','--name-only'],cwd=ROOT,text=True)
                assert not index.strip(),'Unrelated staged files must be preserved'
                subprocess.run(['git','add','--',*FILES],cwd=ROOT,check=True,capture_output=True)
                subprocess.run(['git','commit','-m','Pin maintenance artifacts and preserve data and technical retry boundaries'],cwd=ROOT,check=True,capture_output=True)
                code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
                check=run(['-m','tools.scheduler.runner','--check'])
                # New interpreter sees the complete installed module set. No transport.
                finalized=json.loads(run([str(OUT/'reconcile.py')]))
                with closing(sqlite3.connect(control/'runs.sqlite3')) as conn:
                    conn.row_factory=sqlite3.Row
                    assert ledger_rows(conn)==before,'Original run/receipt rows changed'
                    integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
                    after_holds=[dict(r) for r in conn.execute('SELECT * FROM report_recovery_hold ORDER BY schedule_id,due')]
                allowed={('maintenance_daily_report',p['original_due']) for p in json.loads((ROOT/'reports/scheduler/repair-20261110/recovery-plan.json').read_text('utf8'))['ready_proposals']}
                assert [r for r in before_holds if (r['schedule_id'],r['due']) not in allowed]==[r for r in after_holds if (r['schedule_id'],r['due']) not in allowed]
                record=dict(installed_at=datetime.now(timezone.utc).isoformat(),backup=str(backup),files=expected,
                    code_commit=code_commit,
                    scheduler_check=check,recovery=finalized,integrity=integrity,
                    historical_runs_and_receipts_unchanged=True,unrelated_holds_unchanged=True,
                    gateway_restart=False,workbook_writes=False,transport_calls=0)
                (OUT/'installed.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),'utf8')
                print(json.dumps(record,ensure_ascii=False,indent=2));break
        else:raise TimeoutError('Scheduler did not yield the worker lock')
    except Exception:
        # Do not roll back recovered guards after a ledger recovery transition.
        # Retain the drain and all evidence for a controlled repair if that occurs.
        (OUT/'installation-failure.json').write_text(json.dumps(dict(installed=installed,backup=str(backup)),indent=2),'utf8')
        raise
    finally:
        if (OUT/'installed.json').exists() and request.exists():
            current=json.loads(request.read_text('utf8'))
            if current.get('token')==token:request.unlink()


if __name__=='__main__':main()
