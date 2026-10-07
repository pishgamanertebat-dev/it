"""Guarded canonical NET deployment. Two explicit phases; no transport sends/updates/tasks."""
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile
import argparse
import ast
import hashlib
import json
import os
import shutil
import sqlite3

from tools.authorization import AuthorizationStore
from tools.authorization.net import (
    migrate_net, NET_MANAGER, NET_DEPUTY, NET_CAPABILITIES,
    WO_CAPABILITIES, REPAIRS_CAPABILITIES, WO_DOMAIN, REPAIRS_DOMAIN, domain_decision,
)
ROOT=Path(__file__).resolve().parents[2]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
OUT=ROOT/'runtime/net-migration-20261007'
ASSIGNMENTS=(('641220453',NET_MANAGER),('1294822197',NET_DEPUTY))
TABLES=('channel_users','auth_roles','auth_capabilities','auth_role_capabilities','auth_user_roles',
        'auth_events','auth_role_profiles','auth_extensions','auth_migrations')

def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def snapshot(store):
    with closing(store._connect()) as c,c:
        c.execute('BEGIN')
        assert c.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        c.row_factory=sqlite3.Row
        result={t:[dict(r) for r in c.execute('SELECT * FROM '+t+' ORDER BY 1,2')] for t in TABLES}
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='auth_domain_authority'").fetchone():
            result['auth_domain_authority']=[dict(r) for r in c.execute('SELECT * FROM auth_domain_authority ORDER BY 1,2,3')]
        return result

def fleet_state():
    from tools.fleet.work_orders.core.paths import DB_PATH
    with closing(sqlite3.connect(DB_PATH.resolve().as_uri()+'?mode=ro',uri=True)) as c:
        c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        assert c.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        c.row_factory=sqlite3.Row
        return {t:[dict(r) for r in c.execute('SELECT * FROM '+t+' ORDER BY 1,2')]
                for t in ['service_work_order_users','service_staff','service_staff_roster']}

def users(store):
    # The unchanged first root route calls this same fresh resolver, followed by fixed fallbacks.
    import yaml
    cfg=yaml.safe_load((HOME/'config.yaml').read_text(encoding='utf-8'))
    fixed={str(r['user_id']):r['profile'] for r in cfg['gateway']['profile_routes'] if 'user_id' in r}
    baseline=read('users-before.json')
    return {u:dict(roles=store.roles(u),central_profile=store.resolve_profile(u,u),
                   effective_profile=store.resolve_profile(u,u) or fixed.get(u,'default'),
                   capabilities=sorted({c for r in store.roles(u) for c in store.capabilities_for_role(r)}),
                   function_scope=store.function_scope(u).__dict__) for u in baseline}

def recipients(store):
    return {cap:store.resolve_active_recipients(cap).__dict__ for cap in read('recipients-before.json')}

def protected():
    allowed={str(ROOT/p) for p in [
        'tools/fleet/work_orders/core/permissions.py','tools/fleet/work_orders/core/service.py',
        'tools/fleet/work_orders/core/review.py','tools/fleet/work_orders/core/staff_dispatch.py',
        'tools/fleet/work_orders/core/delivery.py','tools/fleet/work_orders/channels/bale/create_worker.py',
        'tools/fleet/work_orders/channels/bale/message_handler.py','tools/fleet/work_orders/channels/bale/staff_flow.py',
        'tools/fleet/repairs/entry_bale.py','tools/fleet/repairs/entry_service.py','tools/fleet/work_orders/dev/test_oil_change.py']}
    for name,record in read('files-before.json').items():
        if name not in allowed:assert digest(name)==record['sha256'],name
    assert fleet_state()==read('fleet-before.json'),'Legacy W/staff/roster changed'
    cfg=read('legacy-config-before.json')
    for name,data in cfg.items():
        assert json.loads((ROOT/'settings'/name).read_text(encoding='utf-8'))==data
    # Raw task queries are read-only. Never invoke /Run or register/reconcile a task.
    import subprocess
    for task,record in read('tasks-before.json').items():
        r=subprocess.run(['schtasks','/Query','/TN',task,'/XML'],capture_output=True)
        assert r.returncode==0 and hashlib.sha256(r.stdout).hexdigest()==record['sha256'],task

def verify(store, *, assigned):
    current=users(store);baseline=read('users-before.json')
    targets={u for u,_ in ASSIGNMENTS} if assigned else set()
    for u in baseline:
        if u not in targets:
            assert json.loads(json.dumps(current[u]))==baseline[u],('User changed',u)
    assert json.loads(json.dumps(recipients(store)))==read('recipients-before.json')
    for role in [NET_MANAGER,NET_DEPUTY]:
        assert set(store.capabilities_for_role(role))==set(NET_CAPABILITIES)
    if assigned:
        for u,role in ASSIGNMENTS:
            assert current[u]['effective_profile']=='net' and current[u]['central_profile']=='net'
            assert current[u]['roles']==(role,)
            assert set(current[u]['capabilities'])==set(NET_CAPABILITIES)
            assert current[u]['function_scope']['all']
            for domain,caps in [(WO_DOMAIN,WO_CAPABILITIES),(REPAIRS_DOMAIN,REPAIRS_CAPABILITIES)]:
                for cap in caps:
                    d=domain_decision(u,domain,[cap],store=store)
                    assert d.source=='central' and d.allowed,(u,cap,d)
            assert not store.has_capability(u,'maintenance.entries.append')
    protected()
    return dict(success=True,users=current,recipients=recipients(store),authorization_integrity='ok',
                authorization_foreign_keys='ok',fleet_integrity='ok',fleet_foreign_keys='ok',
                legacy_staff_and_roster_unchanged=True,schedules_unchanged=True,tasks_unchanged=True)

def freeze_gate():
    for name in ['net1-tests.json','native-surface.json','native-security-tests.json','reader-security-tests.json','native-live-surface.json','native-transport-and-research.json']:
        assert read(name)['success'],('NET-1 required validation failed',name)
    names=[ROOT/p for p in [
        'integrations/hermes/deploy_net.py','tools/authorization/net.py','tools/authorization/test_net.py','tools/authorization/run_net_checks.py','tools/fleet/work_orders/dev/test_oil_change.py',
        'tools/fleet/work_orders/core/permissions.py','tools/fleet/work_orders/core/service.py',
        'tools/fleet/work_orders/core/review.py','tools/fleet/work_orders/core/staff_dispatch.py',
        'tools/fleet/work_orders/core/delivery.py','tools/fleet/work_orders/channels/bale/create_worker.py',
        'tools/fleet/work_orders/channels/bale/message_handler.py','tools/fleet/work_orders/channels/bale/staff_flow.py',
        'tools/fleet/repairs/entry_bale.py','tools/fleet/repairs/entry_service.py',
        'integrations/hermes/profiles/net/config.template.yaml','integrations/hermes/profiles/net/SOUL.md']]
    for name in names:
        if name.suffix=='.py':ast.parse(name.read_text(encoding='utf-8'))
    value=dict(success=True,tests=read('net1-tests.json')['run'],
               hashes={str(p):digest(p) for p in names},
               evidence_hashes={name:digest(OUT/name) for name in
                               ['net1-tests.json','native-surface.json','native-security-tests.json','reader-security-tests.json','native-live-surface.json','native-transport-and-research.json']})
    save('net1-gate.json',value)

def gate():
    value=read('net1-gate.json');assert value['success']
    for name,h in value['hashes'].items():assert digest(name)==h,('Untested change',name)
    for name,h in value['evidence_hashes'].items():assert digest(OUT/name)==h,('Changed evidence',name)

def deploy_profile():
    profile=HOME/'profiles/net'
    records=read('profile-plan.json')
    for r in records:
        assert digest(r['staged'])==r['sha256']
        target=profile/r['relative']
        assert target.resolve().is_relative_to(profile.resolve())
        assert not target.exists() or digest(target)==r['sha256'],('Unexpected NET profile file',str(target))
    deployed=[]
    for r in records:
        target=profile/r['relative'];target.parent.mkdir(parents=True,exist_ok=True)
        with NamedTemporaryFile(dir=target.parent,prefix=target.name+'.net-',delete=False) as f:
            tmp=Path(f.name);f.write(Path(r['staged']).read_bytes());f.flush();os.fsync(f.fileno())
        try:os.replace(tmp,target)
        finally:tmp.unlink(missing_ok=True)
        assert digest(target)==r['sha256']
        deployed.append(dict(canonical=r['canonical'],runtime=str(target),sha256=digest(target)))
    save('runtime-deployment.json',deployed)

def delta_check(before,after,assigned):
    for t,rows in before.items():
        assert all(r in after.get(t,[]) for r in rows),('Existing authorization row changed',t)
    assert after['channel_users']==before['channel_users']
    assert after['auth_migrations']==before['auth_migrations']
    old_roles=before['auth_user_roles']
    added=[r for r in after['auth_user_roles'] if r not in old_roles]
    if assigned:
        assert {(r['platform'],r['user_id'],r['role'],r['active']) for r in added}=={('bale',u,role,1) for u,role in ASSIGNMENTS}
        markers=after['auth_domain_authority']
        assert {(r['platform'],r['user_id'],r['domain']) for r in markers}=={('bale',u,d) for u,_ in ASSIGNMENTS for d in [WO_DOMAIN,REPAIRS_DOMAIN]}
    else:assert not added and not after['auth_domain_authority']

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--phase',required=True,choices=['freeze','net1','net2'])
    args=ap.parse_args();store=AuthorizationStore()
    if args.phase=='freeze':freeze_gate();print('NET-1 validation gate frozen');return
    gate();protected()
    if args.phase=='net2' and (OUT/'net2-production.json').exists():
        expected=read('authorization-after.json')
        current=snapshot(store)
        # Normal message handling updates this observational timestamp.
        # Identity status/chat, every grant, role/resource/map and permanent marker remain exact.
        def authority_state(value):
            return {t:([{k:v for k,v in row.items() if k!='updated_at'} for row in rows]
                       if t=='channel_users' else rows) for t,rows in value.items()}
        assert authority_state(current)==authority_state(expected),'Production NET-2 authority changed'
        verify(store,assigned=True)
        print('NET-2 already active; verified idempotent no-op')
        return
    if args.phase=='net1':
        before=read('authorization-before.json')
        assert snapshot(store)==before,'Production authorization baseline changed'
        backup=migrate_net(store,OUT/'backups')
        after=snapshot(store);delta_check(before,after,False)
        result=verify(store,assigned=False)
        deploy_profile()
        save('authorization-net1.json',after)
        save('net1-production.json',dict(**result,backup=str(backup),backup_sha256=digest(backup),assignments_performed=False))
        print('NET-1 production infrastructure PASS; no assignments')
    else:
        assert read('net1-production.json')['success'] and not read('net1-production.json')['assignments_performed']
        before=read('authorization-net1.json')
        assert snapshot(store)==before,'Production NET-1 baseline changed'
        # Both appointments and markers share one transaction; no legacy activation.
        backup=migrate_net(store,OUT/'backups',assignments=ASSIGNMENTS,actor='explicit-user-NET-2-20261007')
        after=snapshot(store);delta_check(before,after,True)
        result=verify(store,assigned=True)
        save('authorization-after.json',after)
        save('net2-production.json',dict(**result,backup=str(backup),backup_sha256=digest(backup),assignments=ASSIGNMENTS))
        print('NET-2 production assignments and central authority PASS')
if __name__=='__main__':main()
