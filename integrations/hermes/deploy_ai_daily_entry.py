"""Review and install only Phase 4 after real-model acceptance; no operational test writes."""
from contextlib import closing
from datetime import datetime
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo

ROOT=Path('E:/KomatsoAI')
OUT=ROOT/'runtime/ai-daily-entry-20261109'
CANDIDATE=OUT/'candidate'
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
sys.path.insert(0,str(ROOT))
REPORT='reports/hermes/ai-assisted-daily-entry-20261109.md'
FILES=[
    'tools/bale_ui/runtime.py','tools/fleet/repairs/entry_bale.py','tools/fleet/repairs/entry_service.py',
    'tools/fleet/repairs/entry_extract.py','tools/fleet/repairs/entry_draft_store.py',
    'tools/fleet/repairs/entry_draft.py','tools/fleet/repairs/entry_ai_bale.py',
    'tools/fleet/repairs/test_ai_daily_entry.py','tools/fleet/repairs/test_ai_daily_manual.py',
    'tools/fleet/repairs/rehearse_ai_daily_entry.py','settings/ai_daily_entry.json',
    'integrations/hermes/verify_ai_daily_entry.py','integrations/hermes/verify_ai_daily_model.py',
    'integrations/hermes/verify_ai_daily_excel.ps1','integrations/hermes/deploy_ai_daily_entry.py',REPORT,
]


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path):return json.loads(Path(path).read_text('utf-8-sig'))
def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),'utf8')


def git(*args):
    result=subprocess.run(['git',*args],cwd=ROOT,capture_output=True,text=True,encoding='utf8')
    if result.returncode:raise RuntimeError('Git failed: '+' '.join(args)+'\n'+result.stdout+result.stderr)
    return result.stdout.strip()


def publication(name):
    # Acceptance exercises the enabled path with network forbidden. This is the
    # sole activation switch, published only after all independent checks pass.
    if name=='settings/ai_daily_entry.json':return b'{\n  "enabled": true\n}\n'
    return (CANDIDATE/name).read_bytes()


def check():
    assert load(OUT/'model-real.json')['passed'], 'Real language acceptance is mandatory; no mock substitution'
    assert load(OUT/'model-draft-validation.json')['passed']
    assert load(OUT/'real-copy-final/evidence.json')['passed']
    assert load(OUT/'hedayati-manual/evidence.json')['passed']
    native=load(OUT/'real-copy-final/native-excel.json')
    assert native['normal_open'] and native['readonly'] and not native['repair_mode']
    assert digest(native['path']).upper()==native['sha256']
    tests=load(OUT/'acceptance-release/acceptance.json')
    assert tests['passed'] and tests['tests_run']>=311
    assert tests['failures']==tests['errors']==tests['skipped']==0
    for name,expected in tests['tested_code'].items():
        assert digest(CANDIDATE/name)==expected, 'Code changed after acceptance: '+name
    assert digest(ROOT/'templates/repairs/daily_report.xlsx')==digest(CANDIDATE/'templates/repairs/daily_report.xlsx')
    assert git('rev-parse','HEAD')=='66fb8a79662210840fa1189e8f41e31cf0bd8a7c'
    assert not git('diff','--name-only') and not git('diff','--cached','--name-only')
    before={name.replace('\\','/'):value for name,value in load(OUT/'before-manifest.json').items()}
    for name,expected in before.items():
        if ':' in name:continue  # Live inputs can receive independent mine updates.
        assert digest(ROOT/name)==expected,'Project changed during review: '+name
        if name not in FILES:assert digest(CANDIDATE/name)==expected,'Protected candidate changed: '+name
    plugins=load(OUT/'delta-installed.json')
    for profile,record in plugins.items():
        path=(HOME/'plugins/komatso-bale-registry/__init__.py' if profile=='default'
              else HOME/f'profiles/{profile}/plugins/komatso-bale-registry/__init__.py')
        assert digest(path)==record['sha256'],'Installed registry changed: '+profile
    changes={}
    for name in FILES:
        source=CANDIDATE/name;assert source.is_file(),name
        if source.suffix=='.py':ast.parse(source.read_text('utf8'))
        if name not in before and name!=REPORT:assert not (ROOT/name).exists(),'Unexpected existing target: '+name
        changes[name]=dict(before=digest(ROOT/name) if (ROOT/name).exists() else None,
                           after=hashlib.sha256(publication(name)).hexdigest())
    empty=OUT/'empty-review.txt';empty.write_bytes(b'')
    for name in FILES:
        review=OUT/'review-data'/name;review.parent.mkdir(parents=True,exist_ok=True)
        review.write_bytes(publication(name))
        result=subprocess.run(['git','diff','--no-index','--check','--',str(empty),str(review)],cwd=ROOT,capture_output=True,text=True,encoding='utf8')
        assert result.returncode in (0,1) and not result.stdout.strip(), 'Whitespace preflight: '+result.stdout+result.stderr
    save('reviewed.json',dict(passed=True,files=changes,tests=tests['tests_run'],
        real_model=True,native_excel=True,activation='enabled=true only after graceful drain',
        scope='Drivers2 only; no Sync, maintenance permissions, work-order or Hermes core changes'))
    return changes


def authority():
    from tools.authorization import AuthorizationStore
    from tools.authorization.net import repairs_sections
    store=AuthorizationStore();tables={}
    with closing(store._connect()) as db:
        for name in ('auth_user_roles','auth_domain_authority','auth_role_capabilities','auth_capabilities','auth_role_profiles'):
            rows=db.execute('SELECT * FROM '+name+' ORDER BY rowid').fetchall()
            tables[name]=hashlib.sha256(json.dumps(rows,ensure_ascii=False).encode()).hexdigest()
    settings=load(ROOT/'settings/repairs_entry.json')
    people={actor:dict(roles=list(store.roles(actor)),profile=store.resolve_profile(actor,actor),
                        sections=list(repairs_sections(actor,settings)))
            for actor in ('1732374823','654806764','641220453','387679249')}
    return dict(tables=tables,people=people)


def install():
    changes=check();assert not (OUT/'installed.json').exists(),'Already installed'
    from integrations.hermes.deploy_seven_day_entry import native, deployment_locks, backup_database, busy
    identify,query,windows=native()
    identity,status=identify(HOME,timeout=10),query(HOME,'status',timeout=10)
    assert identity and status['gateway_state']=='running' and not status.get('active_agents')
    for name in ('work_order.json','repairs_entry.json','maintenance_entry.json','daily_ai.json'):
        path=ROOT/'runtime/bale_ui'/name
        assert not path.exists() or not busy(load(path)),'A form worker is busy; retry after natural completion'
    baseline_authority=authority();save('live-authority-before.json',baseline_authority)
    backup=OUT/('activation-backup-'+uuid.uuid4().hex[:8]);backup.mkdir()
    stopped=False;published=[];staged=False;committed=False
    try:
        with deployment_locks():
            from tools.fleet.repairs.entry_service import source_reader
            input_hashes={}
            for kind in ('repairs','maintenance'):
                source=Path(load(ROOT/f'settings/{kind}_entry.json')['source'])
                with source_reader(source) as stream:data=stream.read()
                (backup/source.name).write_bytes(data);input_hashes[str(source)]=hashlib.sha256(data).hexdigest()
                backup_database(ROOT/f'runtime/{kind}-entry/audit.sqlite3',backup/f'{kind}-audit.sqlite3')
            backup_database(ROOT/'reports/telegram_usage/telegram_users.db',backup/'auth.sqlite3')
            for name in FILES:
                if (ROOT/name).exists():
                    target=backup/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target)
            for path in (ROOT/'runtime/bale_ui').glob('*.json'):
                target=backup/'bale_ui'/path.name;target.parent.mkdir(exist_ok=True);shutil.copy2(path,target)
            sync_hash=digest(HOME/'scripts/mine_file_sync.py')
            save('gateway-before-activation.json',dict(identity=identity,status=status))
            windows.request_stop(HOME);stopped=True
            assert windows._drain_gateway_pid(identity['pid'],30),'Drain incomplete; no force kill permitted'
            assert windows.wait_stopped(HOME,10) and not identify(HOME,timeout=2)
            for name in FILES:
                target=(ROOT/name).resolve()
                assert target.is_relative_to(ROOT.resolve())
                target.parent.mkdir(parents=True,exist_ok=True)
                temporary=target.with_name('.ai-daily-'+uuid.uuid4().hex+'.tmp')
                try:
                    with temporary.open('xb') as stream:
                        stream.write(publication(name));stream.flush();os.fsync(stream.fileno())
                    os.replace(temporary,target);published.append(name)
                finally:temporary.unlink(missing_ok=True)
                assert digest(target)==changes[name]['after']
            assert all(digest(path)==value for path,value in input_hashes.items())
            assert authority()==baseline_authority
            staged=True
            git('add','--',*[name for name in FILES if name!=REPORT]);git('add','-f','--',REPORT)
            assert set(git('diff','--cached','--name-only').splitlines())==set(FILES)
            git('diff','--cached','--check')
            output=git('commit','-m','Add AI-assisted daily drafts with two independent write approvals')
            committed=True
            save('installed.json',dict(success=True,commit=git('rev-parse','HEAD'),commit_output=output,
                installed_tehran=datetime.now(ZoneInfo('Asia/Tehran')).isoformat(),backup=str(backup),files=changes,
                old_pid=identity['pid'],workbooks_before_after_install=input_hashes,sync_script_sha256=sync_hash,
                test_messages_sent=0,operational_test_writes=0,authority_unchanged=True))
    except BaseException:
        if staged and not committed:
            git('restore','--staged','--',*FILES)
        if published and not committed:
            for name in reversed(published):
                original=backup/name;target=(ROOT/name).resolve()
                assert target.is_relative_to(ROOT.resolve())
                if original.exists():shutil.copy2(original,target)
                else:target.unlink(missing_ok=True)
        raise
    finally:
        if stopped:
            windows.prepare_start(HOME);spawned=windows._spawn_detached()
            windows._wait_for_gateway_ready(timeout_s=30,confirm_s=2)
            deadline=time.monotonic()+60;fresh=None;fresh_status=None
            while time.monotonic()<deadline:
                fresh=identify(HOME,timeout=2)
                fresh_status=query(HOME,'status',timeout=2) if fresh else None
                if fresh and fresh_status and fresh_status.get('gateway_state')=='running':break
                time.sleep(1)
            save('gateway-restart.json',dict(spawned=spawned,identity=fresh,status=fresh_status))
            assert fresh,'Gateway restart requires review'
    verify()


def verify():
    from integrations.hermes.deploy_seven_day_entry import native
    installed=load(OUT/'installed.json');assert installed['success']
    for name,record in installed['files'].items():assert digest(ROOT/name)==record['after']
    assert digest(HOME/'scripts/mine_file_sync.py')==installed['sync_script_sha256']
    assert authority()==load(OUT/'live-authority-before.json')
    identify,query,_=native();identity,status=identify(HOME,timeout=10),query(HOME,'status',timeout=10)
    assert identity and identity['pid']!=installed['old_pid'] and status['gateway_state']=='running'
    assert {'default','admin','maintenance','net'}<=set(identity['served_profiles'])
    assert all(status['platforms'][p]['state']=='connected' for p in ('bale','telegram'))
    assert load(ROOT/'settings/ai_daily_entry.json')['enabled'] is True
    save('final-verification.json',dict(passed=True,gateway=identity,status=status,
        sync=load(HOME/'state/mine-file-sync.json'),authority_unchanged=True,
        operational_test_writes=0,test_messages_sent=0))
    print(json.dumps(dict(passed=True,commit=installed['commit'],gateway_pid=identity['pid'],
        bale=status['platforms']['bale']['state'],telegram=status['platforms']['telegram']['state'])))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('phase',choices=('check','install','verify'))
    args=parser.parse_args();globals()[args.phase]()
