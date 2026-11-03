"""Backed-up additive rollout for the owner's machinery performance workflow.

Prepare and verify the candidate first. Activation adds only the agreed file
read/subscriptions, installs the existing Function plugin in three profiles,
and appends the new daily schedule. Never sends test messages or edits Excel.
"""
from contextlib import closing
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import shutil
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from tools.authorization import AuthorizationStore
from tools.authorization.machinery_performance import CAPABILITY,REMINDER_CAPABILITY,READ,REPORT_USERS,REMINDER_USER,FILE,POLICIES,migrate
from tools.scheduler.runner import load_config
from integrations.hermes.deploy_business_admin import bootstrap

ROOT=Path(__file__).resolve().parents[2]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
OUT=ROOT/'runtime/machinery-performance-20261103/deployment'
CONFIG=ROOT/'settings/schedules.yaml'
PLUGIN=ROOT/'integrations/hermes/plugins/komatso-function-domain/__init__.py'
TABLES=('auth_roles','auth_capabilities','auth_role_capabilities','auth_role_profiles','auth_user_roles','auth_events','auth_migrations','auth_extensions')
TASK='machinery_performance_daily_report'
PROFILES=('admin','net','maintenance')


def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')


def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(store):
    with closing(store._connect()) as conn:
        conn.execute('BEGIN')
        if conn.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Authorization integrity failed')
        tables={t:conn.execute('SELECT * FROM '+t+' ORDER BY 1,2').fetchall() for t in TABLES}
        users=[r[0] for r in conn.execute("SELECT user_id FROM channel_users WHERE platform='bale' ORDER BY user_id")]
        capabilities=[r[0] for r in conn.execute('SELECT capability FROM auth_capabilities') if r[0].endswith(('daily_receive','completion_remind'))]
    people={u:dict(roles=list(store.roles(u)),profile=store.resolve_profile(u,u),scope=store.function_scope(u).__dict__,
                   capabilities=sorted({c for role in store.roles(u) for c in store.capabilities_for_role(role)})) for u in users}
    recipients={c:store.resolve_active_recipients(c).__dict__ for c in capabilities if not c.startswith('reports.machinery_performance.')}
    return json.loads(json.dumps(dict(tables=tables,people=people,recipients=recipients)))


def verify(store,before):
    after=snapshot(store)
    for table,rows in before['tables'].items():
        if any(row not in after['tables'][table] for row in rows):
            raise RuntimeError('Existing authorization row changed: '+table)
    for table in ('auth_role_profiles','auth_migrations'):
        if before['tables'][table]!=after['tables'][table]:raise RuntimeError('Profile/schema changed')
    if before['recipients']!=after['recipients']:raise RuntimeError('Existing delivery recipients changed')
    additions={t:[row for row in after['tables'][t] if row not in before['tables'][t]] for t in TABLES}
    expected={
        'auth_roles':[[role,title] for role,_,_,title in POLICIES],
        'auth_capabilities':[[CAPABILITY,'reports.machinery_performance.daily'],[REMINDER_CAPABILITY,'reports.machinery_performance.daily'],[READ,'E:\\Function\\'+FILE]],
        'auth_role_capabilities':[[role,cap] for role,cap,_,_ in POLICIES]+[[r,READ] for r in ('mechanical_staff','net_staff') if any(x[0]==r for x in before['tables']['auth_roles'])],
    }
    for table,wanted in expected.items():
        if sorted(additions[table])!=sorted(wanted):raise RuntimeError('Unexpected authorization addition: '+table)
    assignments={(u,role) for role,_,users,_ in POLICIES for u in users}
    if {(r[1],r[2]) for r in additions['auth_user_roles']}!=assignments or len(additions['auth_events'])!=3:
        raise RuntimeError('Wrong named subscriptions')
    for user,old in before['people'].items():
        current=after['people'][user]
        roles=sorted(old['roles']+[role for u,role in assignments if u==user])
        caps=set(old['capabilities'])
        if any(r in old['roles'] for r in ('mechanical_staff','net_staff')):caps.add(READ)
        if user in REPORT_USERS:caps.add(CAPABILITY)
        if user==REMINDER_USER:caps.add(REMINDER_CAPABILITY)
        if current['profile']!=old['profile'] or current['roles']!=roles or set(current['capabilities'])!=caps:
            raise RuntimeError('Unexpected user change: '+user)
        scope=dict(old['scope'])
        if READ in caps and not scope['all']:scope['files']=sorted(set(scope['files'])|{FILE})
        if current['scope']!=scope:raise RuntimeError('Unexpected file scope change: '+user)
    if store.resolve_active_recipients(CAPABILITY).recipients!=REPORT_USERS or store.resolve_active_recipients(REMINDER_CAPABILITY).recipients!=(REMINDER_USER,):
        raise RuntimeError('Wrong live recipient sets')
    return dict(success=True,integrity='ok',existing_recipients_preserved=True,profiles_preserved=True,
                only_agreed_file_scope_added=True,users_checked=len(before['people']),additions=additions)


def candidate_config():
    original=CONFIG.read_text(encoding='utf-8')
    if any(j['id']==TASK for j in yaml.safe_load(original)['schedules']):raise RuntimeError('Schedule already installed')
    block='''  - id: machinery_performance_daily_report
    enabled: true
    task: machinery_performance_daily_report
    recipient_capability: reports.machinery_performance.daily_receive
    timezone: Asia/Tehran
    misfire:
      policy: replay
      horizon_days: 7
    trigger:
      type: cron
      hour: 15
      minute: 0
      start_date: "2026-11-03T00:00:00+03:30"
    params: {}

'''
    candidate=original.replace('external_misfire:\n',block+'external_misfire:\n',1)
    if candidate==original:raise RuntimeError('Schedule insertion point changed')
    candidate+='\nmachinery_performance_reminders:\n  enabled: true\n  hours: [14, 17]\n'
    path=OUT/'schedules-candidate.yaml';path.write_text(candidate,encoding='utf-8')
    load_config(path)
    old=yaml.safe_load(original);new=yaml.safe_load(candidate)
    if [j for j in new['schedules'] if j['id']!=TASK]!=old['schedules']:raise RuntimeError('Other schedules changed')
    for field,value in old.items():
        if field!='schedules' and new[field]!=value:raise RuntimeError('Existing scheduler configuration changed')
    return path


def prepare(store):
    if (OUT/'authorization-before.json').exists():raise RuntimeError('Existing evidence must not be overwritten')
    OUT.mkdir(parents=True,exist_ok=True)
    baseline=snapshot(store);save('authorization-before.json',baseline)
    backup=store.backup(OUT/'backups')
    candidate=OUT/'candidate.sqlite3';shutil.copy2(backup,candidate)
    proposed=AuthorizationStore(candidate);migrate(proposed,OUT/'candidate-backups')
    save('candidate-verification.json',verify(proposed,baseline))
    shutil.copy2(CONFIG,OUT/'schedules-before.yaml')
    candidate_config()
    protected={str(HOME/'config.yaml'):digest(HOME/'config.yaml')}
    for profile in PROFILES:
        folder=HOME/'profiles'/profile
        for name in ('config.yaml','SOUL.md'):
            p=folder/name
            if p.exists():protected[str(p)]=digest(p)
        plugin=folder/'plugins/komatso-function-domain/__init__.py'
        target=OUT/'plugins-before'/profile;target.mkdir(parents=True,exist_ok=True)
        shutil.copy2(plugin,target/'__init__.py')
    save('protected-hashes.json',protected)
    save('prepared.json',dict(ready=True,backup=str(backup),source_read_only=True,send=False,restart=False))


def activate(store):
    baseline=read('authorization-before.json')
    if snapshot(store)!=baseline:raise RuntimeError('Authorization baseline changed')
    for path,expected in read('protected-hashes.json').items():
        if digest(path)!=expected:raise RuntimeError('Profile configuration changed')
    if CONFIG.read_bytes()!=(OUT/'schedules-before.yaml').read_bytes():raise RuntimeError('Existing schedules changed')
    backup=migrate(store,OUT/'backups')
    result=verify(store,baseline);save('live-verification.json',result)
    installed={}
    for profile in PROFILES:
        target=HOME/'profiles'/profile/'plugins/komatso-function-domain/__init__.py'
        shutil.copy2(PLUGIN,target)
        if digest(target)!=digest(PLUGIN):raise RuntimeError('Plugin install hash mismatch')
        installed[profile]=digest(target)
    partial=CONFIG.with_suffix('.performance.tmp')
    partial.write_bytes((OUT/'schedules-candidate.yaml').read_bytes());partial.replace(CONFIG)
    load_config(CONFIG)
    save('activation.json',dict(active=True,backup=str(backup),plugin_hashes=installed,schedule=TASK,
                                 schedule_sha256=digest(CONFIG),send=False,restart=False))


def surfaces(store, *, plugin_path=None, evidence='native-surfaces.json'):
    """Actual native toolset selector and registered schema, without an LLM/send."""
    bootstrap()
    from hermes_constants import set_hermes_home_override,reset_hermes_home_override,hermes_home_key
    from hermes_cli.plugins import PluginManifest,PluginManager,PluginContext,LoadedPlugin
    from gateway.run_turn import GatewayTurnMixin
    from integrations.hermes import role_routing
    result={}
    with closing(store._connect()) as c:
        staff=[r[0] for r in c.execute("SELECT user_id FROM auth_user_roles WHERE platform='bale' AND role='mechanical_staff' AND active=1 ORDER BY user_id")]
        net_staff=[r[0] for r in c.execute("SELECT user_id FROM auth_user_roles WHERE platform='bale' AND role='net_staff' AND active=1 ORDER BY user_id")]
    representatives=[('admin',REPORT_USERS[0]),('net',REMINDER_USER),*[("maintenance",u) for u in staff],*[("net",u) for u in net_staff]]
    for profile,user in representatives:
        home=HOME/'profiles'/profile;cfg=yaml.safe_load((home/'config.yaml').read_text(encoding='utf-8'))
        token=set_hermes_home_override(home)
        try:
            name='komatso-function-domain';spec=importlib.util.spec_from_file_location('performance_native_'+profile+'_'+user,plugin_path or home/'plugins'/name/'__init__.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            manager=PluginManager(hermes_home_key(home));manifest=PluginManifest(name=name,source='project')
            manager._plugins[name]=LoadedPlugin(manifest=manifest,enabled=True);module.register(PluginContext(manifest,manager));manager._discovered=True
            with patch.object(role_routing,'AuthorizationStore',return_value=store),patch('hermes_cli.plugins.get_plugin_manager',return_value=manager):
                selected=GatewayTurnMixin._resolve_enabled_toolsets_for_source(SimpleNamespace(_delivery_adapter_for=lambda s:None),cfg,SimpleNamespace(user_id=user,chat_id=user,chat_type='dm'),'bale')
            if 'komatso_function' not in selected or set(selected)&{'terminal','file','code_execution','connections','all'}:
                raise RuntimeError('Unsafe/incomplete native toolsets')
            if 'machinery_performance' not in module.PROPERTIES['kind']['enum']:raise RuntimeError('Missing performance schema')
            result[profile+':'+user]=dict(toolsets=selected,file_scope=store.function_scope(user).__dict__,performance_schema=True)
        finally:reset_hermes_home_override(token)
    save(evidence,dict(success=True,users=result,no_shell_or_terminal=True))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare','activate','verify'])
    args=parser.parse_args();store=AuthorizationStore()
    if args.action=='prepare':prepare(store)
    elif args.action=='activate':activate(store)
    else:
        print(json.dumps(verify(store,read('authorization-before.json')),ensure_ascii=False))
        print(json.dumps(surfaces(store),ensure_ascii=False))
    print(args.action+' succeeded')


if __name__=='__main__':main()
