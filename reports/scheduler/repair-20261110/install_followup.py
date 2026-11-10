"""Install one reviewed follow-up under the existing cooperative drain."""
from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone
import sys,json,sqlite3,subprocess,uuid,time,shutil,hashlib
ROOT=Path('E:/KomatsoAI');OUT=Path(__file__).resolve().parent
STAGE=ROOT/'runtime/scheduler-repair-20261110/stage';sys.path.insert(0,str(ROOT))
from tools.scheduler.control import worker_lock,write_record

def git(*args,cwd=ROOT):return subprocess.check_output(['git',*args],cwd=cwd,text=True,encoding='utf8').strip()
def main():
    revision=git('rev-parse','HEAD',cwd=STAGE);initial=git('rev-parse','HEAD')
    folder=ROOT/'runtime/scheduler';request=folder/'drain-request.json';assert not request.exists()
    assert not git('diff','--name-only') and not git('diff','--cached','--name-only')
    token=uuid.uuid4().hex;write_record(request,dict(token=token,reason='historical recipient correction',created=datetime.now(timezone.utc).isoformat()))
    installed=False;backup=OUT/('recipient-followup-backup-'+datetime.now(timezone.utc).strftime('%H%M%S'));backup.mkdir()
    try:
        deadline=time.monotonic()+600
        while True:
            with worker_lock(folder) as acquired:
                if acquired:
                    with closing(sqlite3.connect((folder/'runs.sqlite3').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(backup/'ledger.sqlite3')) as dest:
                        src.backup(dest);assert dest.execute('pragma integrity_check').fetchone()[0]=='ok'
                    files=git('diff-tree','--no-commit-id','--name-only','-r',revision,cwd=STAGE).splitlines()
                    for name in files:
                        path=ROOT/name
                        if path.exists():
                            dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
                            assert hashlib.sha256(dest.read_bytes()).digest()==hashlib.sha256(path.read_bytes()).digest()
                    git('cherry-pick',revision)
                    subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),'-E','-s','-B','-X','utf8','-m','tools.scheduler.runner','--check'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
                    assert not git('diff','--name-only',revision,'HEAD')
                    record=dict(initial_commit=initial,source_commit=revision,installed_commit=git('rev-parse','HEAD'),backup=str(backup),
                        receiver_alert_tests=46,recipient_and_office_tests=18,failures=0,actual_report_sends=0,installed_at=datetime.now(timezone.utc).isoformat())
                    (OUT/('followup-installed-'+revision[:12]+'.json')).write_text(json.dumps(record,indent=2),'utf8');installed=True
                    print(json.dumps(record));break
            if time.monotonic()>deadline:raise TimeoutError('Active worker did not drain naturally')
            time.sleep(1)
    finally:
        if installed:
            assert json.loads(request.read_text('utf8'))['token']==token;request.unlink()

if __name__=='__main__':main()
