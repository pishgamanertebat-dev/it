"""Guarded minimal metalwork activation; no transport, reload, restart or Hermes update."""
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from unittest.mock import patch
import argparse, ast, hashlib, json, logging, os, shutil, sqlite3, sys
import yaml
from tools.authorization import AuthorizationStore, METALWORK_STAFF, METALWORK_DRIVER_RECEIVE
from integrations.hermes import role_routing
ROOT=Path(__file__).resolve().parents[2]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
CORE=HOME/'hermes-agent'
EXISTING=['397185913','514458396','1636934401','654806764','1732374823','455740857']
TABLES=['auth_migrations','auth_extensions','auth_roles','auth_capabilities','auth_role_capabilities',
        'auth_role_profiles','auth_user_roles','auth_events']


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def save(out,name,data):
    (out/name).write_text(json.dumps(data,ensure_ascii=True,indent=2,default=str),encoding='utf-8')

def state(store):
    with closing(store._connect()) as c, c:
        c.execute('BEGIN')
        if c.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or c.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Authorization integrity or foreign keys failed')
        return json.loads(json.dumps({t:c.execute('SELECT * FROM '+t+' ORDER BY 1,2').fetchall() for t in TABLES}))

def identities(store):
    with closing(store._connect()) as c:
        c.row_factory=sqlite3.Row
        return [dict(r) for r in c.execute('SELECT * FROM channel_users ORDER BY platform,user_id')]

def bootstrap():
    os.environ['HERMES_HOME']=str(HOME)
    sys.path.insert(1,str(CORE))
    sys.path.extend([str(CORE/'venv/Lib/site-packages'),str(ROOT/'tools')])
    import tools
    if str(CORE/'tools') not in tools.__path__:tools.__path__.append(str(CORE/'tools'))

def users(store, target):
    from gateway.profile_routing import parse_profile_routes, match_profile_route
    cfg=yaml.safe_load((HOME/'config.yaml').read_text(encoding='utf-8'))
    routes=parse_profile_routes(cfg['gateway']['profile_routes'])
    results={}
    with patch.object(role_routing,'AuthorizationStore',return_value=store):
        for user in EXISTING+[target]:
            route=match_profile_route(routes,'bale',user_id=user,chat_id=user)
            roles=store.roles(user)
            results[user]={'roles':roles,'db_profile':store.resolve_profile(user,user),
                           'effective_profile':route.profile if route else 'default',
                           'capabilities':sorted({cap for role in roles for cap in store.capabilities_for_role(role)}),
                           'function_scope':store.function_scope(user).__dict__}
    return json.loads(json.dumps(results))

def recipient_sets(store):
    with closing(store._connect()) as c:
        caps=[r[0] for r in c.execute("SELECT capability FROM auth_capabilities WHERE capability LIKE '%daily_receive'")
              if r[0]!=METALWORK_DRIVER_RECEIVE]
    return {cap:store.resolve_active_recipients(cap).__dict__ for cap in caps}

def work_order_state(target):
    from tools.fleet.work_orders.core.permissions import check_work_order_permission
    from tools.fleet.work_orders.core.paths import DB_PATH
    with closing(sqlite3.connect(DB_PATH.resolve().as_uri()+'?mode=ro',uri=True)) as c:
        roles=c.execute('SELECT * FROM service_work_order_users ORDER BY bale_id').fetchall()
    return {'users':roles,'target_permission':check_work_order_permission(target).__dict__}

def surface(store, target):
    """Actual native resolver and tool definitions, with no tool execution or messages."""
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_cli.plugins import discover_plugins
    from model_tools import get_tool_definitions
    tree=ast.parse((CORE/'gateway/run_turn.py').read_text(encoding='utf-8'))
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_resolve_enabled_toolsets_for_source')
    namespace={'SessionSource':object,'logger':logging.getLogger('metalwork-surface')}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<native-resolver>','exec'),namespace)
    profile_home=HOME/'profiles/maintenance'
    token=set_hermes_home_override(profile_home)
    try:
        discover_plugins()
        cfg=yaml.safe_load((profile_home/'config.yaml').read_text(encoding='utf-8'))
        source=SimpleNamespace(user_id=target,chat_id=target,chat_type='dm')
        runner=SimpleNamespace(_delivery_adapter_for=lambda source:None)
        with patch.object(role_routing,'AuthorizationStore',return_value=store):
            selected=namespace['_resolve_enabled_toolsets_for_source'](runner,cfg,source,'bale')
        definitions=get_tool_definitions(selected,quiet_mode=True,skip_tool_search_assembly=True)
        names=sorted(d['function']['name'] for d in definitions)
        forbidden={'terminal','PowerShell','execute_code','read_file','write_file','patch','search_files',
                   'skill_manage','process_manage'}
        if forbidden.intersection(names) or any(n.startswith('function_') for n in names):
            raise RuntimeError('Unexpected operational or host tool surface')
        if not {'maintenance_manual_evidence','maintenance_partbook_lookup','web_search','web_extract','delegate_task'}<=set(names):
            raise RuntimeError('Existing shared technical surface unavailable')
        return {'toolsets':selected,'tools':names,'generic_host_tools':False,'function_tools':False}
    finally:reset_hermes_home_override(token)

def validate(store, before, before_users, before_recipients, target):
    after=state(store)
    expected={
        'auth_roles':[[METALWORK_STAFF,'نیروی آهنگری']],
        'auth_capabilities':[[METALWORK_DRIVER_RECEIVE,'reports.driver_daily.metalwork']],
        'auth_role_capabilities':[[METALWORK_STAFF,METALWORK_DRIVER_RECEIVE]],
        'auth_role_profiles':[[METALWORK_STAFF,'maintenance',100,1]],
    }
    for table in TABLES:
        if any(row not in after[table] for row in before[table]):raise RuntimeError('Existing authorization changed: '+table)
        added=[row for row in after[table] if row not in before[table]]
        if table in expected and added!=expected[table]:raise RuntimeError('Unexpected authorization delta: '+table)
        if table=='auth_migrations' and added:raise RuntimeError('Schema version changed')
        if table=='auth_extensions' and (len(added)!=1 or added[0][0]!='metalwork_roles_v1'):
            raise RuntimeError('Unexpected extension marker')
        if table=='auth_user_roles' and (len(added)!=1 or added[0][:4]!=['bale',target,METALWORK_STAFF,1]):
            raise RuntimeError('Unexpected role assignment')
        if table=='auth_events' and (len(added)!=1 or added[0][1:5]!=['bale',target,METALWORK_STAFF,'assigned']):
            raise RuntimeError('Unexpected audit event')
    current=users(store,target)
    for user in EXISTING:
        if current[user]!=before_users[user]:raise RuntimeError('Existing user regression: '+user)
    person=current[target]
    if (person['roles']!=[METALWORK_STAFF] or person['effective_profile']!='maintenance'
            or person['db_profile']!='maintenance' or person['capabilities']!=[METALWORK_DRIVER_RECEIVE]
            or person['function_scope']!={'all':False,'files':[]}):
        raise RuntimeError('Target least-privilege profile acceptance failed')
    if recipient_sets(store)!=before_recipients:raise RuntimeError('Existing recipient set changed')
    if store.resolve_active_recipients(METALWORK_DRIVER_RECEIVE).recipients!=(target,):
        raise RuntimeError('Target capability recipient resolution failed')
    return {'users':current,'existing_recipient_sets':before_recipients,'metalwork_recipients':[target],
            'integrity':'ok','foreign_keys':'ok'}

def atomic_schedule(source,target):
    with NamedTemporaryFile(prefix='metalwork-',suffix='.tmp',dir=target.parent,delete=False) as f:
        temporary=Path(f.name);f.write(source.read_bytes());f.flush();os.fsync(f.fileno())
    try:os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user',required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();out=args.evidence;target=args.user;store=AuthorizationStore()
    bootstrap()
    from gateway.control_socket import identify_gateway
    from tools.scheduler.runner import load_config
    before=json.loads((out/'authorization-before.json').read_text(encoding='utf-8'))
    baseline_identities=json.loads((out/'identities-before.json').read_text(encoding='utf-8'))
    hashes=json.loads((out/'protected-hashes.json').read_text(encoding='utf-8'))
    schedule=ROOT/'settings/schedules.yaml';old_schedule=out/'files-before/settings/schedules.yaml'
    proposed=out/'schedules-proposed.yaml'
    for file in ['tests.json','native-smoke.json']:
        if not json.loads((out/file).read_text(encoding='utf-8'))['success']:raise RuntimeError('Required validation failed: '+file)
    if state(store)!=before or identities(store)!=baseline_identities:raise RuntimeError('Identity/authorization baseline changed')
    if any(digest(path)!=value for path,value in hashes.items()):raise RuntimeError('Protected runtime file changed')
    if schedule.read_bytes()!=old_schedule.read_bytes():raise RuntimeError('Existing schedules changed')
    old=yaml.safe_load(old_schedule.read_text(encoding='utf-8'))
    new=yaml.safe_load(proposed.read_text(encoding='utf-8'))
    if {k:v for k,v in old.items() if k!='schedules'}!={k:v for k,v in new.items() if k!='schedules'}:
        raise RuntimeError('Scheduler global settings changed')
    if new['schedules'][:-1]!=old['schedules'] or len(new['schedules'])!=len(old['schedules'])+1:
        raise RuntimeError('Existing schedules must remain unchanged with one new job')
    job=new['schedules'][-1]
    if job!=dict(id='driver_daily_metalwork',enabled=True,task='metalwork_driver_daily',
                 recipient_capability=METALWORK_DRIVER_RECEIVE,timezone='Asia/Tehran',
                 trigger=dict(type='cron',hour=9,minute=0),params={}):raise RuntimeError('Unexpected metalwork schedule')
    load_config(proposed)
    initial=identify_gateway(HOME,timeout=15)
    baseline=json.loads((out/'gateway-before.json').read_text(encoding='utf-8'))
    if not initial or initial['pid']!=baseline['pid']:raise RuntimeError('Gateway unavailable or baseline changed')
    original_users=users(store,target);original_recipients=recipient_sets(store)
    save(out,'users-before.json',original_users);save(out,'recipients-before.json',original_recipients)
    wo_before=work_order_state(target);save(out,'work-order-before.json',wo_before)
    if wo_before['target_permission']['status']!='DENIED':raise RuntimeError('Existing Work Order privilege requires review')
    pairing_path=HOME/'profiles/maintenance/platforms/pairing/bale-approved.json'
    pairing=json.loads(pairing_path.read_text(encoding='utf-8'))
    if target not in pairing:raise RuntimeError('Existing Maintenance pairing required')
    save(out,'target-pairing.json',{'profile':'maintenance','approved':True,'record':pairing[target],'changed':False})
    backup=store.backup(out/'backups');candidate=out/'candidate.sqlite3';shutil.copy2(backup,candidate)
    candidate_store=AuthorizationStore(candidate)
    candidate_store.migrate_metalwork(out/'candidate-backups',assignments=[target],actor='explicit-user-metalwork-phase')
    candidate_result=validate(candidate_store,before,original_users,original_recipients,target)
    save(out,'candidate-verification.json',candidate_result)
    save(out,'target-tool-surface.json',surface(candidate_store,target))
    after_candidate=state(candidate_store)
    candidate_store.migrate_metalwork(out/'candidate-backups',assignments=[target,target],actor='explicit-user-metalwork-phase')
    if state(candidate_store)!=after_candidate:raise RuntimeError('Candidate migration is not idempotent')
    save(out,'candidate-idempotency.json',{'success':True,'duplicate_rows':False,'duplicate_events':False})
    save(out,'prepared.json',{'user':target,'candidate_validated':True,'backup':backup,
                             'new_job':job,'restart':False,'reload_required':False,'production_send':False})
    if not args.apply:
        print('Candidate and native profile surface verified; production assignment and schedule unchanged.');return
    if state(store)!=before or identities(store)!=baseline_identities or schedule.read_bytes()!=old_schedule.read_bytes():
        raise RuntimeError('Pre-commit identity/authorization/schedule baseline changed')
    if any(digest(path)!=value for path,value in hashes.items()):raise RuntimeError('Pre-commit protected file changed')
    fresh=store.migrate_metalwork(out/'backups',assignments=[target],actor='explicit-user-metalwork-phase')
    result=validate(store,before,original_users,original_recipients,target)
    if identities(store)!=baseline_identities or work_order_state(target)!=wo_before:
        raise RuntimeError('Identity or Work Order state changed; schedule not enabled')
    atomic_schedule(proposed,schedule)
    load_config(schedule)
    save(out,'authorization-after.json',state(store));save(out,'production-verification.json',result)
    final=identify_gateway(HOME,timeout=15)
    if not final or any(final[k]!=initial[k] for k in ['pid','start_time','code_sha','code_version']):
        raise RuntimeError('Gateway identity changed')
    save(out,'gateway-after.json',final)
    if any(digest(path)!=value for path,value in hashes.items()):raise RuntimeError('Protected file changed after activation')
    save(out,'activation.json',{'active':True,'backup':fresh,'backup_sha256':digest(fresh),
                              'schedule_sha256':digest(schedule),'restart':False,'reload':False,
                              'hermes_update':False,'windows_tasks_changed':False,'production_send':False})
    print('METALWORK ACTIVE: Maintenance priority 100; receive-only capability; daily 09:00 Tehran. No restart/reload/update/send.')

if __name__=='__main__':main()
