"""Publish prepared ADMIN-1 components. Routing activation is deliberately excluded.

No core writes, gateway restart, Hermes update, launcher or Scheduled Task changes.
Business identity assignments are CLI data, never routing source constants.
"""
from pathlib import Path
from contextlib import closing
import argparse,ast,hashlib,json,os,shutil,sqlite3,subprocess,sys
from uuid import uuid4
import yaml

ROOT=Path(__file__).resolve().parents[2]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
CORE=HOME/'hermes-agent'

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--business-admin',action='append',required=True)
    args=parser.parse_args()
    report=Path((ROOT/'runtime/admin1-active.txt').read_text(encoding='utf-8'))
    manifest=json.loads((report/'manifest.json').read_text(encoding='utf-8'))
    def backup(path):
        path=Path(path)
        if any(r['path']==str(path) for r in manifest):return
        record={'path':str(path),'existed':path.exists()}
        if path.exists():
            target=report/'backups'/str(len(manifest))/path.name;target.parent.mkdir(parents=True)
            shutil.copy2(path,target);assert digest(path)==digest(target)
            record.update(backup=str(target),sha256=digest(target))
        manifest.append(record);(report/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    # Append the project namespace without shadowing Hermes modules.
    sys.path.insert(0,str(CORE));sys.path.append(str(ROOT))
    from integrations.hermes.role_routing import AuthorizationStore
    store=AuthorizationStore()
    with closing(store._connect()) as conn:
        for user in args.business_admin:
            identity=conn.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?",(user,)).fetchone()
            if identity!=(user,'approved'):raise ValueError('Existing approved private identity required; registration is never modified')
    home=HOME/'profiles/admin'
    if home.exists():raise FileExistsError('Admin profile already exists; use a reviewed deployment update')
    pairs=[]
    for plugin in ['komatso-public-research','komatso-function-domain','komatso-parallel']:
        for source in (ROOT/'integrations/hermes/plugins'/plugin).iterdir():
            if source.is_file() and source.suffix in {'.py','.yaml','.cjs'}:
                if source.suffix=='.py':ast.parse(source.read_text(encoding='utf-8-sig'))
                pairs.append((source,home/'plugins'/plugin/source.name))
    source=ROOT/'integrations/hermes/profiles/admin/SOUL.md';pairs.append((source,home/'SOUL.md'))
    maintenance_home=HOME/'profiles/maintenance'
    backup(maintenance_home/'config.yaml')
    for source in (ROOT/'integrations/hermes/plugins/komatso-function-domain').iterdir():
        if source.is_file() and source.suffix in {'.py','.yaml'}:
            dest=maintenance_home/'plugins/komatso-function-domain'/source.name
            pairs.append((source,dest));backup(dest)
    # The existing registration bridge receives the backward-compatible cache refresh.
    registry=ROOT/'integrations/hermes/plugins/komatso-bale-registry/__init__.py'
    for dest in [HOME/'plugins/komatso-bale-registry/__init__.py',HOME/'profiles/maintenance/plugins/komatso-bale-registry/__init__.py']:
        pairs.append((registry,dest));backup(dest)
    for _,dest in pairs:
        if str(dest).startswith(str(home)):backup(dest)
    backup(home/'config.yaml')
    backup(home/'.env')
    # Bootstrap with the native fresh-profile API under an isolated temporary HOME.
    # No Maintenance clone, no copied identity/pairing/memories or bot tokens.
    bootstrap=report/('profile-bootstrap-'+uuid4().hex);bootstrap.mkdir(parents=True)
    env=dict(os.environ,HERMES_HOME=str(bootstrap),PYTHONIOENCODING='utf-8')
    script="from hermes_cli.profiles import create_profile,seed_profile_skills; p=create_profile('admin',no_alias=True,description='Business Admin: read-only company data and public research'); r=seed_profile_skills(p,quiet=True); print('Fresh native profile bootstrap complete')"
    process=subprocess.run([str(CORE/'venv/Scripts/python.exe'),'-X','utf8','-c',script],cwd=CORE,env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
    if process.returncode:raise RuntimeError('Native profile bootstrap failed')
    staged=bootstrap/'profiles/admin'
    cfg=yaml.safe_load((staged/'config.yaml').read_text(encoding='utf-8')) if (staged/'config.yaml').exists() else {}
    policy=yaml.safe_load((ROOT/'integrations/hermes/profiles/admin/config.template.yaml').read_text(encoding='utf-8'))
    for key,value in policy.items():cfg[key]=value
    root_cfg=yaml.safe_load((HOME/'config.yaml').read_text(encoding='utf-8'))
    # Share inference configuration only, never transports or organizational policy.
    cfg['_config_version']=root_cfg.get('_config_version',46)
    cfg['model']=root_cfg['model']
    cfg['web']=root_cfg.get('web',{})
    # Explicitly provision existing public research credentials only. OAuth model
    # grants continue using Hermes' native credential-pool fallback; never clone
    # refreshable auth.json or copy bot tokens/allowlists/system credentials.
    from dotenv import dotenv_values,set_key
    public_keys=dotenv_values(HOME/'.env')
    names=['PARALLEL_API_KEY','KOMATSO_PARALLEL_PYTHON','TAVILY_API_KEY']
    for key in names:
        if public_keys.get(key):set_key(str(staged/'.env'),key,public_keys[key],quote_mode='always')
    (report/'admin-research-provisioning.json').write_text(json.dumps({'provisioned_key_names':[key for key in names if public_keys.get(key)],'model_auth':'native credential pool; no auth.json clone','transport_secrets_copied':False},indent=2),encoding='utf-8')
    cfg['gateway']['media_delivery_allow_dirs']=[str(home/'document_cache/komatso'),str(home/'image_cache'),str(home/'browser_screenshots')]
    cfg['known_plugin_toolsets']={'bale':['komatso_function'], 'telegram':['komatso_function'], 'cli':['komatso_function']}
    cfg['agent']['skip_context_files']=True
    # Explicitly choose public surfaces for every Messaging fallback.
    for platform in ['discord','slack','whatsapp','signal','teams','google_chat']:
        cfg['platform_toolsets'][platform]=['web','skills_readonly','komatso_public_browser','delegation','no_mcp']
        cfg['known_plugin_toolsets'][platform]=['komatso_function']
    (staged/'config.yaml').write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False),encoding='utf-8')
    for directory in ['document_cache/komatso','image_cache','browser_screenshots']:(staged/directory).mkdir(parents=True,exist_ok=True)
    for source,dest in pairs:
        if str(dest).startswith(str(home)):
            target=staged/dest.relative_to(home);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target);assert digest(source)==digest(target)
    (report/'admin-config-resolved.yaml').write_bytes((staged/'config.yaml').read_bytes())
    # Copy completed native layout to a hidden same-volume sibling, then publish
    # atomically. The Windows native named-profile root ignores HOME overrides
    # beneath its app root, so bootstrap uses the supported external custom HOME.
    sibling=home.with_name('.admin.admin1-staging-'+uuid4().hex)
    assert sibling.resolve().is_relative_to(HOME.resolve())
    shutil.copytree(staged,sibling)
    sibling.rename(home)
    # Remove the temporary credential copy; runtime .env is the sole new owner.
    assert (staged/'.env').resolve().is_relative_to(report.resolve())
    (staged/'.env').unlink()
    records=[]
    for source,dest in pairs:
        if not str(dest).startswith(str(home)):
            dest.parent.mkdir(parents=True,exist_ok=True)
            tmp=dest.with_name(dest.name+'.admin1-'+uuid4().hex+'.tmp');shutil.copy2(source,tmp);assert digest(source)==digest(tmp);tmp.replace(dest)
        assert digest(source)==digest(dest)
        records.append({'canonical':str(source),'runtime':str(dest),'sha256':digest(source)})
    records.append({'canonical':str(report/'admin-config-resolved.yaml'),'runtime':str(home/'config.yaml'),'sha256':digest(home/'config.yaml')})
    (report/'runtime-deploy.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    maintenance_config_path=maintenance_home/'config.yaml'
    maintenance_before=yaml.safe_load(maintenance_config_path.read_text(encoding='utf-8'))
    maintenance_config=yaml.safe_load(maintenance_config_path.read_text(encoding='utf-8'))
    maintenance_config['capability_toolsets_resolver']='integrations.hermes.role_routing.resolve_toolsets'
    maintenance_config['plugins']['enabled'].append('komatso-function-domain')
    # Suppress native automatic plugin-toolset inclusion. Only the trusted
    # per-identity capability resolver may explicitly select the Function toolset.
    for platform in maintenance_config['platform_toolsets']:
        known=maintenance_config.setdefault('known_plugin_toolsets',{}).setdefault(platform,[])
        if 'komatso_function' not in known:known.append('komatso_function')
    # Base surfaces, SOUL, device routing and every original key remain intact.
    for key,value in maintenance_before.items():
        if key not in {'plugins','known_plugin_toolsets'}:assert maintenance_config[key]==value
    text=yaml.safe_dump(maintenance_config,allow_unicode=True,sort_keys=False)
    tmp=maintenance_config_path.with_name('config.yaml.admin1-new');tmp.write_text(text,encoding='utf-8');tmp.replace(maintenance_config_path)
    (report/'maintenance-config-resolved.yaml').write_bytes(maintenance_config_path.read_bytes())
    records.append({'canonical':str(report/'maintenance-config-resolved.yaml'),'runtime':str(maintenance_config_path),'sha256':digest(maintenance_config_path)})
    (report/'runtime-deploy.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    from gateway.control_socket import reload_gateway_plugins,rescan_gateway_profiles
    reloads=[]
    for profile_home in [HOME,HOME/'profiles/maintenance']:
        result=reload_gateway_plugins(HOME,profile_home=profile_home,timeout=45)
        reloads.append(result)
        if not result or not result.get('reloaded'):raise RuntimeError('Official registration plugin reload failed; migration not started')
    (report/'registry-reloads.json').write_text(json.dumps(reloads,indent=2),encoding='utf-8')
    # A second consistent backup immediately precedes migration and assignments.
    migration_backup=store.migrate_admin(report/'backups')
    store.assign_roles([(user,'business_admin') for user in args.business_admin],actor='explicit-user-admin1-assignment')
    with closing(store._connect()) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Authorization integrity failure')
        state={t:conn.execute('SELECT * FROM '+t).fetchall() for t in ['auth_migrations','auth_roles','auth_capabilities','auth_role_capabilities','auth_role_profiles','auth_user_roles']}
    (report/'authorization-after.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest.append({'path':str(store.path),'backup':str(migration_backup),'sha256':digest(migration_backup),'integrity':'ok','purpose':'immediate-pre-migration'})
    (report/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    # Prepare (never publish) the Role -> Profile resolver entry and removal of
    # obsolete static assignments. The untouched legacy routes remain active.
    activation=yaml.safe_load((HOME/'config.yaml').read_text(encoding='utf-8'))
    routes=activation['gateway']['profile_routes']
    with closing(store._connect()) as conn:
        supervisors={row[0] for row in conn.execute("SELECT user_id FROM auth_user_roles WHERE platform='bale' AND role='office_supervisor' AND active=1")}
    routes=[r for r in routes if str(r.get('user_id','')) not in supervisors]
    routes.insert(0,{'name':'organization-role-profiles','platform':'bale','resolver':'integrations.hermes.role_routing.resolve_profile'})
    activation['gateway']['profile_routes']=routes
    (report/'gateway-config-for-future-activation.yaml').write_text(yaml.safe_dump(activation,allow_unicode=True,sort_keys=False),encoding='utf-8')
    result=rescan_gateway_profiles(HOME,timeout=45)
    (report/'profile-rescan.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('Admin profile published; authorization migration/assignments complete; 10:00 schedule prepared and disabled.')
    print('Routing activation intentionally pending. No core files/config routes changed, no Gateway restart.')
    print(json.dumps({'rescan':result,'migration_backup':str(migration_backup)},ensure_ascii=True))

if __name__=='__main__':main()
