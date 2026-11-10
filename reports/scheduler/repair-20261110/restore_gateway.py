"""Start a verified stopped Gateway through its installed detached supervisor."""
from pathlib import Path
from datetime import datetime,timezone
import sys,os,json,time,shutil
HOME=Path('C:/Users/win-10/AppData/Local/hermes');OUT=Path(__file__).resolve().parent
os.environ['HERMES_HOME']=str(HOME);sys.path.insert(0,str(HOME/'hermes-agent'))
from gateway.control_socket import identify_gateway,query_gateway_control
from hermes_cli import gateway_windows as windows

@windows.serialized_lifecycle
def start_if_stopped():
    identity=identify_gateway(HOME,timeout=3)
    if identity:return dict(already_running=True,identity=identity)
    pids=windows._gateway_pids()
    if pids:raise RuntimeError('Existing non-answering Gateway needs a formal drain; do not spawn a duplicate')
    before=OUT/'gateway-start-backup';before.mkdir(exist_ok=True)
    for source in (HOME/'gateway_state.json',HOME/'gateway-service/supervisor.json',HOME/'state/gateway.lifecycle.json'):
        if source.exists():shutil.copy2(source,before/source.name)
    assert windows.wait_stopped(HOME,5),'Existing supervisor authority is active'
    windows.prepare_start(HOME)
    spawned=windows._spawn_detached(home=HOME)
    return dict(already_running=False,spawned=spawned,prior_worker_verified_absent=True)

def main():
    result=start_if_stopped();print(json.dumps(result),flush=True)
    deadline=time.monotonic()+55
    while time.monotonic()<deadline:
        identity=identify_gateway(HOME,timeout=2)
        status=query_gateway_control(HOME,'status',timeout=2) if identity else None
        if identity and status and status.get('gateway_state')=='running':
            result.update(identity=identity,status=status,verified_at=datetime.now(timezone.utc).isoformat())
            (OUT/'gateway-restored.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
            print(json.dumps(dict(pid=identity.get('pid'),state=status.get('gateway_state'),platforms=status.get('platforms')),ensure_ascii=False));return
        time.sleep(1)
    (OUT/'gateway-start-pending.json').write_text(json.dumps(result,indent=2),'utf8')
    print('Gateway startup pending; inspect supervisor and control health')

if __name__=='__main__':main()
