"""Gracefully reload the idle gateway after the reviewed project-only update."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .entry_service import ROOT, permission
from .maintenance_service import permission as maintenance_permission

HERMES = Path(r'C:\Users\win-10\AppData\Local\hermes')


def main():
    for actor in ('641220453', '455740857'):
        settings = permission(actor)
        if not Path(settings['source']).is_file() or not Path(settings['template']).is_file():
            raise RuntimeError('Source or blank template is missing')
    for actor in ('455740857', '654806764'):
        settings = maintenance_permission(actor)
        if not Path(settings['source']).is_file():
            raise RuntimeError('Maintenance workbook is missing')
    for name in ('work_order.json', 'repairs_entry.json', 'maintenance_entry.json'):
        path = ROOT / 'runtime/bale_ui' / name
        if path.exists() and any(value.get('stage') == 'BUSY' for _, value in json.loads(path.read_text(encoding='utf-8'))):
            raise RuntimeError('A form operation is running; wait for it to finish')
    state = json.loads((HERMES/'gateway_state.json').read_text(encoding='utf-8'))
    if state.get('gateway_state') != 'running' or state.get('active_agents') != 0:
        raise RuntimeError('Gateway is busy; retry when idle')
    os.environ['HERMES_HOME'] = str(HERMES)
    sys.path.insert(0, str(HERMES/'hermes-agent'))
    sys.path.append(str(HERMES/'hermes-agent/venv/Lib/site-packages'))
    import tools
    tools.__path__.append(str(HERMES/'hermes-agent/tools'))
    from gateway.status import get_running_pid
    from hermes_cli.gateway_windows import _drain_gateway_pid
    current = get_running_pid()
    if current != state['pid']:
        raise RuntimeError('Gateway process changed; inspect before restarting')
    print(f'Gracefully stopping idle gateway {current}', flush=True)
    if not _drain_gateway_pid(current, 30):
        raise RuntimeError('Graceful stop is still pending; no duplicate process was started')
    subprocess.Popen(['wscript.exe', str(HERMES/'gateway-service/Hermes_Gateway.vbs')],
                     cwd=str(HERMES), creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        time.sleep(2)
        try:
            state = json.loads((HERMES/'gateway_state.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if state.get('pid') != current and state.get('gateway_state') == 'running' and state.get('platforms', {}).get('bale', {}).get('state') == 'connected':
            print(json.dumps({'old_pid': current, 'new_pid': state['pid'], 'bale': 'connected'}))
            return
    raise RuntimeError('Gateway launched; connection not yet verified')


if __name__ == '__main__':
    main()
