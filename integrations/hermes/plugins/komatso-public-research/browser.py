"""Fixed-command local Chromium worker; no shell, arbitrary argv, or host paths."""
import atexit
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid
from .network import PublicProxy, public_url

_workers={}
_lock=threading.RLock()

def runtime_paths():
    from hermes_constants import get_process_hermes_home
    home=get_process_hermes_home()
    candidates=[home,*home.parents]
    for base in candidates:
        node=base/"node/node.exe"
        module=base/"hermes-agent/node_modules/playwright-core"
        if node.is_file() and (module/"package.json").is_file():
            cache=Path(os.environ["LOCALAPPDATA"])/"ms-playwright"
            executables=sorted(cache.glob("chromium-*/chrome-win*/chrome.exe"),key=lambda p:int(p.parents[1].name.split("-")[-1]),reverse=True)
            if not executables:
                raise RuntimeError("An installed managed Chromium is required")
            return node,module,executables[0]
    raise RuntimeError("Managed Node and Playwright Core are required for public browser")

class BrowserWorker:
    def __init__(self):
        node,module,chromium=runtime_paths()
        self.lock=threading.RLock()
        self._proxy_lock=threading.Lock()
        self.proxy=PublicProxy()
        self.responses=queue.Queue(maxsize=16)
        # Location vars only. No Hermes/.env/LLM/browser/cloud credentials reach the renderer.
        env={k:v for k,v in os.environ.items() if k.upper() in {
            "SYSTEMROOT","WINDIR","USERPROFILE","LOCALAPPDATA","APPDATA","TEMP","TMP","PATH"
        }}
        try:
            self.proc=subprocess.Popen(
                [str(node),str(Path(__file__).with_name("browser_worker.cjs")),str(module),self.proxy.url,str(chromium)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                env=env,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
            )
            threading.Thread(target=self._read,name="public-browser-results",daemon=True).start()
            response=self.responses.get(timeout=45)
            if not response.get("ready"):
                raise RuntimeError(response.get("error","Public browser did not start"))
        except Exception:
            self.close()
            raise
    def _read(self):
        try:
            while True:
                line=self.proc.stdout.readline(16*1024*1024+1)
                if not line: break
                if len(line)>16*1024*1024: break
                self.responses.put(json.loads(line),timeout=2)
        except (ValueError,OSError,queue.Full):
            pass
        finally:
            try: self.responses.put({"success":False,"error":"Public browser worker stopped"},timeout=1)
            except queue.Full: pass
            self._close_proxy()
            self.proc.stdout.close()
            if self.proc.stdin and not self.proc.stdin.closed:
                try: self.proc.stdin.close()
                except OSError: pass
            with _lock:
                for key,worker in list(_workers.items()):
                    if worker is self: _workers.pop(key,None)
    def _close_proxy(self):
        with self._proxy_lock:
            proxy=getattr(self,"proxy",None)
            self.proxy=None
        if proxy: proxy.close()
    def call(self,action,args):
        with self.lock:
            if self.proc.poll() is not None: raise RuntimeError("Public browser session expired; navigate again")
            self.proc.stdin.write((json.dumps({"id":uuid.uuid4().hex,"action":action,"args":args})+"\n").encode())
            self.proc.stdin.flush()
            try:
                return self.responses.get(timeout=50)
            except queue.Empty:
                self.close()
                raise RuntimeError("Public browser request timed out; session closed")
    def close(self):
        proc=getattr(self,"proc",None)
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(timeout=5)
            except (OSError,subprocess.TimeoutExpired):
                from agent.deadline import kill_process_tree
                kill_process_tree(proc.pid)
                proc.wait(timeout=5)
        self._close_proxy()

def browser_action(action,args,task_id=None):
    try:
        if action=="navigate": public_url(args.get("url",""))
        from hermes_constants import hermes_home_key, get_hermes_home
        from gateway.session_context import get_session_env
        owner=task_id if task_id and task_id != "default" else get_session_env("HERMES_SESSION_ID","")
        if not owner: return json.dumps({"success":False,"error":"A browser session id is required"})
        key=(hermes_home_key(),owner)
        with _lock:
            worker=_workers.get(key)
            if action=="close":
                if worker: worker.close(); _workers.pop(key,None)
                return json.dumps({"success":True,"closed":True})
            if worker is None or worker.proc.poll() is not None:
                if worker: worker.close()
                if action!="navigate": return json.dumps({"success":False,"error":"Navigate to a public page first"})
                worker=BrowserWorker(); _workers[key]=worker
        result=worker.call(action,args)
        if result.get("success") and result.get("url"):
            try:
                public_url(result["url"])
            except ValueError:
                worker.call("clear",{})
                raise ValueError("Blocked: browser navigation or redirect targets a private/local address")
        if result.get("png"):
            folder=get_hermes_home()/"images/public-browser"
            folder.mkdir(parents=True,exist_ok=True)
            path=folder/(uuid.uuid4().hex+".png")
            path.write_bytes(base64.b64decode(result.pop("png"),validate=True))
            result["screenshot_path"]=str(path)
            from tools.browser_tool_vision import _native_vision_result
            result=_native_vision_result(path,args.get("question","Describe this public page"),False,result,None)
            result["success"]=True
        return json.dumps(result,ensure_ascii=False,default=str)
    except Exception as exc:
        return json.dumps({"success":False,"error":str(exc)[:1200]})

def close_all():
    with _lock:
        workers=list(_workers.values()); _workers.clear()
    for worker in workers:
        worker.close()
atexit.register(close_all)
