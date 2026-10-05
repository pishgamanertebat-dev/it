"""Guarded Business Admin activation: canonical DB policy, no restart or send."""
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import argparse, hashlib, importlib.util, json, shutil, subprocess, sys
import yaml
from tools.authorization import AuthorizationStore, BUSINESS_ADMIN, MECHANICAL_STAFF, FUNCTION_READ
from integrations.hermes import role_routing
ROOT=Path(__file__).resolve().parents[2]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
CORE=HOME/'hermes-agent'
TABLES=['channel_users','auth_migrations','auth_extensions','auth_roles','auth_capabilities','auth_role_profiles','auth_role_capabilities','auth_user_roles','auth_events']
PAKDEL='514458396'
IDS=[PAKDEL,'397185913','1636934401','654806764','1732374823','455740857']

def save(out,name,value):
    (out/name).write_text(json.dumps(value,ensure_ascii=True,indent=2,default=str),encoding='utf-8')

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def state(store):
    with closing(store._connect()) as c, c:
        c.execute('BEGIN')
        if c.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or c.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Authorization integrity failed')
        return json.loads(json.dumps({t:c.execute('SELECT * FROM '+t+' ORDER BY 1,2').fetchall() for t in TABLES}))

def bootstrap():
    sys.path[:0]=[str(CORE),str(CORE/'venv/Lib/site-packages')]
    import tools
    if str(CORE/'tools') not in tools.__path__:tools.__path__.append(str(CORE/'tools'))

def users(store):
    from gateway.profile_routing import parse_profile_routes,match_profile_route
    routes=parse_profile_routes(yaml.safe_load((HOME/'config.yaml').read_text(encoding='utf-8'))['gateway']['profile_routes'])
    result={}
    with patch.object(role_routing,'AuthorizationStore',return_value=store):
        for u in IDS:
            route=match_profile_route(routes,'bale',user_id=u,chat_id=u)
            result[u]={'roles':list(store.roles(u)),'db_profile':store.resolve_profile(u,u),
                       'native_profile':route.profile if route else 'default',
                       'function_scope':json.loads(json.dumps(store.function_scope(u).__dict__)),
                       'capabilities':sorted({cap for r in store.roles(u) for cap in store.capabilities_for_role(r)})}
    return result

def load_plugin(home,name):
    path=home/'plugins'/name/'__init__.py'
    spec=importlib.util.spec_from_file_location('pakdel_'+name.replace('-','_'),path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module

def surface(store,profile,user):
    from hermes_constants import set_hermes_home_override,reset_hermes_home_override,hermes_home_key
    from hermes_cli.plugins import PluginManifest,PluginManager,PluginContext,LoadedPlugin
    from gateway.run_turn import GatewayTurnMixin
    from gateway.session_context import set_session_vars,clear_session_vars
    from toolsets import resolve_toolset
    home=HOME/'profiles'/profile;cfg=yaml.safe_load((home/'config.yaml').read_text(encoding='utf-8'))
    token=set_hermes_home_override(home)
    tokens=set_session_vars(platform='bale',chat_type='dm',user_id=user,chat_id=user)
    try:
        manager=PluginManager(hermes_home_key(home))
        names=['komatso-function-domain','komatso-public-research']
        if profile=='maintenance':names.append('komatso-maintenance-manual')
        for name in names:
            module=load_plugin(home,name);manifest=PluginManifest(name=name,source='project')
            manager._plugins[name]=LoadedPlugin(manifest=manifest,enabled=True)
            module.register(PluginContext(manifest,manager))
        manager._discovered=True
        with patch.object(role_routing,'AuthorizationStore',return_value=store),patch('hermes_cli.plugins.get_plugin_manager',return_value=manager):
            selected=GatewayTurnMixin._resolve_enabled_toolsets_for_source(SimpleNamespace(_delivery_adapter_for=lambda s:None),cfg,SimpleNamespace(user_id=user,chat_id=user,chat_type='dm'),'bale')
            actual={n for ts in selected for n in resolve_toolset(ts)}
        public={n for n in actual if n.startswith('public_browser_')}
        expected={'web_search','web_extract','skills_list','skill_view','delegate_task'}|public
        if store.function_scope(user):expected|={'function_'+n for n in ['list','search','metadata','read','attach','report']}
        if profile=='maintenance':expected|={'maintenance_manual_evidence','maintenance_partbook_lookup'}
        if actual!=expected or len(public)!=17:raise RuntimeError('Unexpected actual tool surface: '+repr(sorted(actual)))
        return {'toolsets':selected,'tools':sorted(actual),'public_browser_count':len(public),'skills_inline_shell':cfg['skills']['inline_shell']}
    finally:clear_session_vars(tokens);reset_hermes_home_override(token)

def validate(store,before,old_users):
    after=state(store);current=users(store)
    for table in ['channel_users','auth_migrations','auth_roles','auth_capabilities','auth_role_capabilities']:
        if after[table]!=before[table]:raise RuntimeError('Protected authorization rows changed: '+table)
    for row in before['auth_user_roles']:
        if row not in after['auth_user_roles']:raise RuntimeError('Legacy role assignment changed')
    expected_new={('bale',PAKDEL,BUSINESS_ADMIN),('bale','1636934401',MECHANICAL_STAFF)}
    added=[r for r in after['auth_user_roles'] if r not in before['auth_user_roles']]
    if {tuple(r[:3]) for r in added}!=expected_new:raise RuntimeError('Unexpected assignments')
    if [r for r in after['auth_role_profiles'] if r not in before['auth_role_profiles']]!=[[BUSINESS_ADMIN,'admin',10,1]]:
        raise RuntimeError('Unexpected role mapping delta')
    if any(row not in after['auth_role_profiles'] for row in before['auth_role_profiles']):raise RuntimeError('Specialized mapping changed')
    for u in ['397185913','654806764','1732374823','455740857']:
        if current[u]!=old_users[u]:raise RuntimeError('Regression for '+u)
    p=current[PAKDEL];m=current['1636934401']
    if p['roles']!=[BUSINESS_ADMIN] or p['native_profile']!='admin' or p['capabilities']!=[FUNCTION_READ] or not p['function_scope']['all']:
        raise RuntimeError('Pakdel acceptance failed')
    if set(m['roles'])!={BUSINESS_ADMIN,MECHANICAL_STAFF} or m['native_profile']!='maintenance' or not m['function_scope']['all']:
        raise RuntimeError('Existing business Maintenance reader regression')
    for cap,resource in before['auth_capabilities']:
        if cap.endswith('daily_receive'):
            if PAKDEL in store.resolve_active_recipients(cap).recipients or '1636934401' in store.resolve_active_recipients(cap).recipients:
                raise RuntimeError('Unexpected scheduled receive')
    return {'users':current,'admin_surface':surface(store,'admin',PAKDEL),'maintenance_surface':surface(store,'maintenance','1636934401'),'integrity':'ok','foreign_keys':'ok'}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--preserve-maintenance-user',required=True,choices=['1636934401'],help='Explicit operator approval of the missing existing Mechanical Staff role')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();out=args.evidence;store=AuthorizationStore();bootstrap()
    from gateway.control_socket import identify_gateway
    before=json.loads((out/'authorization-before.json').read_text())
    old_users=json.loads((out/'users-before.json').read_text())
    if state(store)!=before:raise RuntimeError('Authorization changed since baseline; review required')
    if not json.loads((out/'tests.json').read_text())['success']:raise RuntimeError('Required tests did not pass')
    hashes=json.loads((out/'protected-hashes.json').read_text())
    if any(digest(p)!=v for p,v in hashes.items()):raise RuntimeError('Protected runtime/security/schedules changed')
    identity=[r for r in before['channel_users'] if r[:2]==['bale',PAKDEL]]
    if len(identity)!=1 or identity[0][2]!=PAKDEL or identity[0][5]!='approved':raise RuntimeError('Pakdel not approved/private')
    initial=identify_gateway(HOME,timeout=15)
    if not initial or initial['pid']!=json.loads((out/'gateway-before.json').read_text())['pid']:raise RuntimeError('Gateway baseline changed')
    save(out,'admin-surface-before.json',surface(store,'admin','397185913'))
    assignments=[(PAKDEL,BUSINESS_ADMIN),(args.preserve_maintenance_user,MECHANICAL_STAFF)]
    fresh=store.backup(out/'backups');candidate=out/'candidate.sqlite3';shutil.copy2(fresh,candidate)
    proposed=AuthorizationStore(candidate)
    proposed.migrate_business_admin_profile(out/'candidate-backups',assignments=assignments,actor='explicit-user-pakdel-business-admin')
    result=validate(proposed,before,old_users);save(out,'candidate-verification.json',result)
    save(out,'deployment-prepared.json',{'assignments':assignments,'mapping':[BUSINESS_ADMIN,'admin',10,1],'backup':str(fresh),'candidate_validated':True,'restart':False,'reload_required':False})
    if not args.apply:
        print('Prepared and verified candidate; no production mutation.');return
    # DB readers already query mappings per identity; no core or plugin payload changed.
    if state(store)!=before or any(digest(p)!=v for p,v in hashes.items()):raise RuntimeError('Pre-commit baseline changed')
    backup=store.migrate_business_admin_profile(out/'backups',assignments=assignments,actor='explicit-user-pakdel-business-admin')
    save(out,'activation.json',{'active':True,'backup':str(backup),'backup_sha256':digest(backup),'assignments':assignments,'restart':False,'reload':False,'hermes_update':False,'message_send':False})
    save(out,'authorization-after.json',state(store));save(out,'production-verification.json',validate(store,before,old_users))
    final=identify_gateway(HOME,timeout=15)
    if not final or any(final[k]!=initial[k] for k in ['pid','start_time','code_sha','code_version']):raise RuntimeError('Gateway changed')
    save(out,'gateway-after.json',final)
    if any(digest(p)!=v for p,v in hashes.items()):raise RuntimeError('Protected hashes changed')
    print('PAKDEL ACTIVE: approved/private, business_admin -> admin priority 10, read-only Function. Existing profiles retained. No restart/reload/update/send.')

if __name__=='__main__':main()
