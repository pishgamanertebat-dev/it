"""Stage or atomically deploy shared technical surfaces; never restart/update Hermes."""
from pathlib import Path
import argparse, ast, copy, hashlib, json, os, sys, tempfile
import yaml
ROOT=Path('E:/KomatsoAI');HOME=Path('C:/Users/win-10/AppData/Local/hermes')
OUT=ROOT/'runtime/shared-technical-answering-20261005'
PLUGIN='komatso-technical-docs';TOOLSET='komatso_technical_docs'
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def desired(config,profile):
    result=copy.deepcopy(config)
    plugins=result.setdefault('plugins',{}).setdefault('enabled',[])
    plugins[:]=[p for p in plugins if p!='komatso-maintenance-manual']
    if PLUGIN not in plugins:plugins.append(PLUGIN)
    for platform in ('bale','telegram'):
        selected=result['platform_toolsets'][platform]
        selected[:]=[TOOLSET if s=='komatso_maintenance' else s for s in selected]
        if TOOLSET not in selected:selected.insert(0,TOOLSET)
        if profile=='default' and 'delegation' not in selected:selected.insert(-1,'delegation')
    # Known exclusions prevent auto-inclusion on CLI and non-selected channels.
    for platform in result['platform_toolsets']:
        known=result.setdefault('known_plugin_toolsets',{}).setdefault(platform,[])
        if TOOLSET not in result['platform_toolsets'][platform] and TOOLSET not in known:known.append(TOOLSET)
        if TOOLSET in result['platform_toolsets'][platform]:known[:]=[s for s in known if s!=TOOLSET]
    if profile=='default':
        disabled=result.setdefault('agent',{}).get('disabled_toolsets',[])
        disabled[:]=[s for s in disabled if s!='delegation']
    allowed={'plugins','platform_toolsets','known_plugin_toolsets'}|({'agent'} if profile=='default' else set())
    for key in set(config)|set(result):
        if key not in allowed:assert config.get(key)==result.get(key),(profile,key)
    if profile=='default':
        before=copy.deepcopy(config['agent']);before['disabled_toolsets']=[s for s in before['disabled_toolsets'] if s!='delegation']
        assert before==result['agent']
        assert not result.get('capability_toolsets_resolver')
        assert 'komatso-function-domain' not in plugins
    assert config.get('capability_toolsets_resolver')==result.get('capability_toolsets_resolver')
    return result

def atomic(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=target.name+'.technical-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(source.read_bytes());stream.flush();os.fsync(stream.fileno())
        assert digest(Path(tmp))==digest(source)
        os.replace(tmp,target)
        assert digest(target)==digest(source)
    finally:Path(tmp).unlink(missing_ok=True)

def pairs():
    result=[]
    for name,home in [('default',HOME),('maintenance',HOME/'profiles/maintenance'),('admin',HOME/'profiles/admin')]:
        staged=OUT/'staged'/name
        result.append((staged/'config.yaml',home/'config.yaml'))
        plugin_root=ROOT/'integrations/hermes/plugins'/PLUGIN
        result.append((plugin_root/'plugin.yaml',home/'plugins'/PLUGIN/'plugin.yaml'))
        result.append((plugin_root/'RUNTIME_LOADER.py',home/'plugins'/PLUGIN/'__init__.py'))
    result.append((ROOT/'integrations/hermes/profiles/maintenance/SOUL.md',HOME/'profiles/maintenance/SOUL.md'))
    # Warm old sessions keep their old schema; a tiny loader still points at the single source.
    result.append((ROOT/'integrations/hermes/plugins/komatso-maintenance-manual/__init__.py',HOME/'profiles/maintenance/plugins/komatso-maintenance-manual/__init__.py'))
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');ap.add_argument('--reload',action='store_true');args=ap.parse_args()
    baseline={r['path']:r for r in json.loads((OUT/'baseline.json').read_text(encoding='utf-8'))}
    if args.reload:
        sys.path.insert(0,str(HOME/'hermes-agent'))
        from gateway.control_socket import reload_gateway_plugins
        results=[]
        for name,home in [('default',HOME),('maintenance',HOME/'profiles/maintenance'),('admin',HOME/'profiles/admin')]:
            result=reload_gateway_plugins(HOME,profile_home=home,timeout=45);results.append({'profile':name,'result':result})
            (OUT/'plugin-reloads.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
            if not result or not result.get('reloaded') or PLUGIN not in result.get('plugins',[]):raise RuntimeError('Official reload did not activate Technical Docs')
        print(json.dumps(results,indent=2));return
    for name,home in [('default',HOME),('maintenance',HOME/'profiles/maintenance'),('admin',HOME/'profiles/admin')]:
        source=Path(baseline[str(home/'config.yaml')]['backup'])
        assert digest(source)==baseline[str(home/'config.yaml')]['before_sha256']
        staged=OUT/'staged'/name;staged.mkdir(parents=True,exist_ok=True)
        config=desired(yaml.safe_load(source.read_text(encoding='utf-8')),name)
        (staged/'config.yaml').write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False),encoding='utf-8')
    plans=pairs()
    for source,target in plans:
        if source.suffix=='.py':ast.parse(source.read_text(encoding='utf-8-sig'))
        if str(target) in baseline:
            assert digest(target)==baseline[str(target)]['before_sha256'],'Runtime changed since baseline: '+str(target)
        else:assert not target.exists(),'Untracked deployment target exists: '+str(target)
    if not args.apply:print('Staged and validated',len(plans),'runtime files; no runtime mutation');return
    applied=[]
    try:
        for source,target in plans:atomic(source,target);applied.append(target)
    except Exception:
        for target in reversed(applied):
            if str(target) in baseline:atomic(Path(baseline[str(target)]['backup']),target)
            else:target.unlink(missing_ok=True)
        raise
    (OUT/'runtime-deploy.json').write_text(json.dumps([{'canonical':str(s),'runtime':str(t),'sha256':digest(t)} for s,t in plans],indent=2),encoding='utf-8')
    print('Applied',len(plans),'hash-verified files; official reload still required')
if __name__=='__main__':main()
