"""Back up and activate Admin's quiet Bale display; canonical plugin uses a live loader."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
HOME = Path('C:/Users/win-10/AppData/Local/hermes')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    profile = HOME/'profiles/admin'
    target = profile/'config.yaml'
    original = target.read_bytes()
    before = yaml.safe_load(original.decode('utf-8'))
    after = copy.deepcopy(before)
    after.setdefault('display', {}).setdefault('platforms', {}).setdefault('bale', {})['tool_progress'] = 'off'
    if after == before:
        print('Admin Bale tool progress is already off; no config write.')
        return
    if 'display' not in before:
        staged = original.rstrip() + b"\ndisplay:\n  platforms:\n    bale:\n      tool_progress: 'off'\n"
    else:
        staged = yaml.safe_dump(after, allow_unicode=True, sort_keys=False).encode('utf-8')
    if yaml.safe_load(staged.decode('utf-8')) != after:
        raise RuntimeError('Staged configuration does not match the intended one-setting change')
    loader = profile/'plugins/komatso-technical-docs/__init__.py'
    if loader.read_bytes() != (ROOT/'integrations/hermes/plugins/komatso-technical-docs/RUNTIME_LOADER.py').read_bytes():
        raise RuntimeError('Admin Technical Docs is not using the canonical runtime loader')
    if not args.apply:
        print('Validated: only display.platforms.bale.tool_progress will change to off.')
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out = ROOT/'runtime/admin-media-fix-20261026'/stamp
    out.mkdir(parents=True, exist_ok=False)
    backup = out/'config.yaml.before'
    backup.write_bytes(original)
    if backup.read_bytes() != original or target.read_bytes() != original:
        raise RuntimeError('Backup verification failed or configuration changed during preparation')
    fd, temporary = tempfile.mkstemp(prefix='.admin-media-', dir=profile)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(staged)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if target.read_bytes() != staged:
        raise RuntimeError('Activated configuration verification failed; restore the retained backup')
    record = {'target': str(target), 'backup': str(backup),
              'before_sha256': hashlib.sha256(original).hexdigest(),
              'after_sha256': hashlib.sha256(staged).hexdigest(),
              'change': 'display.platforms.bale.tool_progress=off',
              'gateway_restart_performed': False}
    (out/'activation.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
