"""Back up, validate and atomically apply project adapters, never update Hermes.

The shared library has one canonical source in the project. Reload through the
official gateway control socket after apply; no launcher or task changes.
"""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import os
import shutil
import tempfile

ROOT=Path(__file__).resolve().parents[2]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def deployment_pairs():
    pairs=[(ROOT/'integrations/hermes/plugins/komatso-maintenance-manual/__init__.py',HOME/'profiles/maintenance/plugins/komatso-maintenance-manual/__init__.py'),
           (ROOT/'integrations/hermes/profiles/maintenance/SOUL.md',HOME/'profiles/maintenance/SOUL.md')]
    twin=HOME/'plugins/komatso-maintenance-manual/__init__.py'
    if twin.exists():pairs.append((pairs[0][0],twin))
    for home in [HOME,HOME/'profiles/maintenance']:
        pairs.append((ROOT/'integrations/hermes/plugins/komatso-public-research/__init__.py',home/'plugins/komatso-public-research/__init__.py'))
    return pairs

def atomic_copy(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(prefix=target.name+'.fast-core-',dir=target.parent)
    staging=Path(temporary)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(source.read_bytes());stream.flush();os.fsync(stream.fileno())
        assert digest(staging)==digest(source)
        os.replace(staging,target)
        assert digest(target)==digest(source)
    finally:
        staging.unlink(missing_ok=True)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--report-dir',type=Path,required=True)
    ap.add_argument('--apply',action='store_true')
    args=ap.parse_args();out=args.report_dir.resolve()
    if not out.is_relative_to((ROOT/'runtime').resolve()):raise ValueError('Report must be in project runtime')
    manifest_path=out/'manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    backups={x['path']:x for x in manifest}
    pairs=deployment_pairs()
    # Complete every validation and backup before the first runtime mutation.
    for source,target in pairs:
        if source.suffix=='.py':ast.parse(source.read_text(encoding='utf-8-sig'),filename=str(source))
        if str(target) not in backups:raise ValueError('Missing pre-edit backup: '+str(target))
        record=backups[str(target)]
        assert digest(Path(record['backup']))==record['before_sha256']
        assert digest(target)==record['before_sha256'], 'Runtime changed since audit: '+str(target)
    for config in [HOME/'config.yaml',HOME/'profiles/maintenance/config.yaml']:
        assert digest(config)==backups[str(config)]['before_sha256'],'Config changed since baseline'
    if not args.apply:
        print('Validated atomic deployment:',len(pairs),'runtime twins; configuration unchanged.')
        return
    applied=[]
    try:
        for source,target in pairs:
            atomic_copy(source,target)
            applied.append(target)
    except Exception:
        for target in reversed(applied):atomic_copy(Path(backups[str(target)]['backup']),target)
        raise
    records=[{'canonical':str(source),'runtime':str(target),'after_sha256':digest(target)} for source,target in pairs]
    records.append({'canonical':str(ROOT/'integrations/hermes/shared_fast_core/__init__.py'),
                    'runtime_reference':'Single project source imported by adapters; no runtime copy',
                    'after_sha256':digest(ROOT/'integrations/hermes/shared_fast_core/__init__.py')})
    (out/'runtime-deploy.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    for record in manifest:record['after_sha256']=digest(Path(record['path']))
    manifest_path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('Applied and hash-verified',len(pairs),'atomic runtime copies. Official plugin reload required.')
if __name__=='__main__':main()
