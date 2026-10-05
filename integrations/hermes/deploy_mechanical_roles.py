"""Canonical mechanical activation; CLI assignments, official reload, no restart or send."""
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,sys
from uuid import uuid4
import yaml
from tools.authorization import AuthorizationStore,MECHANICAL_STAFF,MECHANICAL_MANAGER,MECHANICAL_MANAGER_DEPUTY
ROOT=Path(__file__).resolve().parents[2]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
CORE=HOME/'hermes-agent'
TABLES=['auth_migrations','auth_roles','auth_capabilities','auth_role_capabilities','auth_role_profiles','auth_user_roles']
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def state(store):
    with closing(store._connect()) as c:
        if c.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise RuntimeError('Authorization integrity failed')
        if c.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('Authorization foreign keys failed')
        data={t:c.execute('SELECT * FROM '+t+' ORDER BY 1,2').fetchall() for t in TABLES}
        data['identities']=c.execute('SELECT platform,user_id,chat_id,registration_status FROM channel_users ORDER BY platform,user_id').fetchall()
        return json.loads(json.dumps(data))
def runtime_pairs():
    for plugin,profile in [('komatso-bale-registry',''),('komatso-bale-registry','profiles/maintenance'),
                           ('komatso-function-domain','profiles/maintenance'),('komatso-function-domain','profiles/admin')]:
        yield ROOT/'integrations/hermes/plugins'/plugin/'__init__.py',HOME/profile/'plugins'/plugin/'__init__.py'
def preflight(out):
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip()!='210dd95421b4420500d117efaff9749774591dc0':
        raise RuntimeError('Review baseline commit before activation')
    for name in ['hermetic-tests.json','native-boundary.json','native-excel-smoke.json']:
        if not json.loads((out/name).read_text(encoding='utf-8'))['success']:raise RuntimeError('Required validation failed: '+name)
    hashes=json.loads((out/'baseline-hashes.json').read_text(encoding='utf-8'))
    if any(digest(p)!=v for p,v in hashes.items()):raise RuntimeError('Protected baseline hashes changed')
    if (ROOT/'settings/schedules.yaml').read_bytes()!=(out/'schedules-before.yaml').read_bytes():
        raise RuntimeError('Schedules changed after baseline')
    before=json.loads((out/'authorization-before.json').read_text(encoding='utf-8'))
    if state(AuthorizationStore())!=before:raise RuntimeError('Authorization changed after baseline')
    old=yaml.safe_load((out/'schedules-before.yaml').read_text(encoding='utf-8'))
    proposed=yaml.safe_load((out/'schedules-proposed.yaml').read_text(encoding='utf-8'))
    if proposed['schedules'][:len(old['schedules'])]!=old['schedules']:raise RuntimeError('Existing schedules must be preserved')
    from tools.scheduler.runner import load_config
    load_config(out/'schedules-proposed.yaml')
    records=[]
    for source,target in runtime_pairs():
        compile(source.read_bytes(),str(source),'exec')
        previous=subprocess.check_output(['git','show','HEAD:'+source.relative_to(ROOT).as_posix()],cwd=ROOT)
        normalize=lambda data:data.decode('utf-8-sig').replace('\r\n','\n')
        if not target.is_file() or normalize(target.read_bytes())!=normalize(previous):raise RuntimeError('Runtime twin drift requires review')
        records.append({'canonical':str(source),'runtime':str(target),'before_sha256':digest(target),'after_sha256':digest(source)})
    return records
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manager',required=True)
    parser.add_argument('--deputy',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if args.manager==args.deputy:raise ValueError('Distinct manager and deputy required')
    assignments=[(args.manager,MECHANICAL_STAFF),(args.manager,MECHANICAL_MANAGER),
                 (args.deputy,MECHANICAL_STAFF),(args.deputy,MECHANICAL_MANAGER_DEPUTY)]
    out=Path((ROOT/'runtime/mechanical-active.txt').read_text(encoding='utf-8'))
    records=preflight(out);store=AuthorizationStore()
    with closing(store._connect()) as c:
        for user,_ in assignments:
            if c.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?",(user,)).fetchone()!=(user,'approved'):
                raise ValueError('Existing approved private identity required')
    payload={'runtime':records,'assignments':assignments}
    (out/'deployment-prepared.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    if not args.apply:
        print('Prepared reviewed payload. No production mutation.');return 0
    for index,item in enumerate(records):
        target=Path(item['runtime']);backup=out/'runtime-backups'/str(index)/target.name
        backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup)
        if digest(backup)!=item['before_sha256']:raise RuntimeError('Runtime backup mismatch')
        item['backup']=str(backup)
    store.backup(out/'backups')
    (out/'runtime-deploy.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    for item in records:
        target=Path(item['runtime']);temp=target.with_name(target.name+'.mechanical-'+uuid4().hex+'.tmp')
        shutil.copy2(item['canonical'],temp)
        if digest(temp)!=item['after_sha256']:raise RuntimeError('Staging hash mismatch')
        temp.replace(target)
        if digest(target)!=item['after_sha256']:raise RuntimeError('Runtime hash mismatch')
    sys.path[:0]=[str(CORE),str(CORE/'venv/Lib/site-packages')]
    import tools
    if str(CORE/'tools') not in tools.__path__:tools.__path__.append(str(CORE/'tools'))
    from gateway.control_socket import reload_gateway_plugins,identify_gateway
    before=identify_gateway(HOME,timeout=15)
    if not before:raise RuntimeError('Gateway control socket unavailable; DB not changed')
    (out/'gateway-identify-before.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
    reloads=[]
    for profile in ['', 'profiles/maintenance','profiles/admin']:
        result=reload_gateway_plugins(HOME,profile_home=HOME/profile,timeout=45)
        reloads.append(result)
        (out/'plugin-reloads.json').write_text(json.dumps(reloads,indent=2),encoding='utf-8')
        if not result or not result.get('reloaded'):raise RuntimeError('Official plugin reload failed; DB not changed')
        active={item['name']:item for item in result.get('activations',[])}
        if profile in ['', 'profiles/maintenance']:
            transforms=active.get('komatso-bale-registry',{}).get('activated_now',{}).get('gateway_transforms',[])
            if 'pre_gateway_dispatch' not in transforms:
                raise RuntimeError('Registration dispatch transform not active; DB not changed')
        if profile in ['profiles/maintenance','profiles/admin']:
            tools=active.get('komatso-function-domain',{}).get('deferred',{}).get('tools',[])
            if not {'function_read','function_list','function_report'}.issubset(tools):
                raise RuntimeError('Function tools not registered for new agents; DB not changed')
    backup=store.migrate_mechanical(out/'backups',assignments=assignments,actor='explicit-user-mechanical-phase')
    after=state(store)
    (out/'authorization-after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2),encoding='utf-8')
    prior=json.loads((out/'authorization-before.json').read_text(encoding='utf-8'))
    for row in prior['auth_user_roles']:
        if row not in after['auth_user_roles']:raise RuntimeError('Legacy role changed')
    source=out/'schedules-proposed.yaml';target=ROOT/'settings/schedules.yaml'
    temp=target.with_name('schedules.yaml.mechanical-'+uuid4().hex+'.tmp');shutil.copy2(source,temp);temp.replace(target)
    current=identify_gateway(HOME,timeout=15)
    if not current or current['pid']!=before['pid'] or current['code_sha']!=before['code_sha']:
        raise RuntimeError('Unexpected Gateway change')
    (out/'gateway-identify-after.json').write_text(json.dumps(current,indent=2),encoding='utf-8')
    (out/'activation.json').write_text(json.dumps({'applied_at':datetime.now(timezone.utc).isoformat(),'backup':str(backup),
        'backup_sha256':digest(backup),'integrity':'ok','foreign_keys':'ok','assignments':assignments,
        'official_reload':True,'restart':False,'core_changed':False,'bale_send':False,'schedules_enabled':True},indent=2),encoding='utf-8')
    print('Mechanical roles and assignments activated transactionally; schedules enabled; official reload successful; no restart/send.')
    return 0
if __name__=='__main__':raise SystemExit(main())
