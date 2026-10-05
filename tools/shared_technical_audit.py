from pathlib import Path
import hashlib, json, shutil
import yaml
ROOT=Path('E:/KomatsoAI')
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
OUT=ROOT/'runtime/shared-technical-answering-20261005'
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    manifest=OUT/'baseline.json'
    if manifest.exists(): raise RuntimeError('Baseline already exists; never overwrite')
    files=set()
    for folder in [ROOT/'integrations/hermes/plugins/komatso-maintenance-manual',ROOT/'integrations/hermes/profiles',ROOT/'settings',HOME/'gateway-service',HOME/'cron',ROOT/'tools/scheduler',ROOT/'integrations/hermes/shared_fast_core']:
        files.update(p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    files.update([ROOT/'AGENTS.md',ROOT/'tools/manual_worker_batch.py',ROOT/'tools/manual_worker_web.py',ROOT/'integrations/hermes/role_routing.py',HOME/'SOUL.md'])
    for home in [HOME,HOME/'profiles/maintenance',HOME/'profiles/admin']:
        files.add(home/'config.yaml')
        files.update(p for p in (home/'plugins').rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts)
        if (home/'SOUL.md').exists(): files.add(home/'SOUL.md')
    for folder in [ROOT/'data',ROOT/'tools']: files.update(folder.rglob('*.db'))
    models=json.loads((ROOT/'tools/manual_models.json').read_text(encoding='utf-8'))
    for folder in set(models.values()):
        files.update((ROOT/folder).glob('*.pdf'))
        files.add(ROOT/folder/'AGENTS.md')
        files.add(ROOT/folder/'manual_sections.json')
    backup=OUT/'backups';backup.mkdir(exist_ok=True)
    records=[]
    for path in sorted(files):
        if not path.is_file():continue
        record={'path':str(path),'before_sha256':digest(path)}
        if path.suffix.lower() not in {'.db','.pdf'}:
            target=backup/(hashlib.sha256(str(path).encode()).hexdigest()[:16]+'-'+path.name)
            shutil.copy2(path,target)
            assert digest(target)==record['before_sha256']
            record['backup']=str(target)
        records.append(record)
    manifest.write_text(json.dumps(records,indent=2),encoding='utf-8')
    (OUT/'gateway-before.json').write_bytes((HOME/'gateway_state.json').read_bytes())
    surfaces={}
    for name,home in [('default',HOME),('maintenance',HOME/'profiles/maintenance'),('admin',HOME/'profiles/admin')]:
        config=yaml.safe_load((home/'config.yaml').read_text(encoding='utf-8'))
        surfaces[name]={key:config.get(key) for key in ['platform_toolsets','known_plugin_toolsets','capability_toolsets_resolver','agent','delegation']}
    (OUT/'config-surfaces-before.json').write_text(json.dumps(surfaces,indent=2),encoding='utf-8')
    print(json.dumps({'backed_up':len(records),'output':str(OUT),'surfaces':surfaces},indent=2))
if __name__=='__main__':main()
