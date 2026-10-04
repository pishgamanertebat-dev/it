"""Deploy canonical overflow authorization bridge to existing Hermes profile twins.

Shared project tools and schedules are canonical in E:/KomatsoAI already.
This command never edits configs, Hermes core, launchers or Scheduled Tasks.
"""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
HOME = Path('C:/Users/win-10/AppData/Local/hermes')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    source = ROOT/'integrations/hermes/plugins/komatso-bale-registry/__init__.py'
    ast.parse(source.read_text(encoding='utf-8-sig'))
    targets = [HOME/'plugins/komatso-bale-registry/__init__.py',
               HOME/'profiles/maintenance/plugins/komatso-bale-registry/__init__.py']
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    records = []
    # Validate and back up every existing twin before the first runtime write.
    for target in targets:
        if not target.is_file():
            raise FileNotFoundError('Existing runtime registry twin required')
        backup = target.with_name(f'{target.name}.{stamp}.{uuid4().hex}.bak')
        shutil.copy2(target, backup)
        assert digest(target) == digest(backup)
        records.append(dict(canonical=str(source), runtime=str(target), backup=str(backup),
                            before_sha256=digest(target), after_sha256=digest(source)))
    for record in records:
        target = Path(record['runtime'])
        temporary = target.with_name(f'{target.name}.{uuid4().hex}.tmp')
        shutil.copy2(source, temporary)
        assert digest(source) == digest(temporary)
        temporary.replace(target)
        assert digest(source) == digest(target)
    out = Path((ROOT/'runtime/authorization-active.txt').read_text())
    (out/'runtime-deploy.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    print('Two registry twins deployed and hashes verified. Native plugin reload required.')


if __name__ == '__main__':
    main()
