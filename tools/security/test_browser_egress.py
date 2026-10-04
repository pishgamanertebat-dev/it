"""Opt-in public Chromium + controlled private stub E2E; no production LAN."""
import http.server
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import unittest

LIVE="--live-public" in sys.argv
if LIVE: sys.argv.remove("--live-public")
ROOT=Path(__file__).resolve().parents[2]
RUNTIME=Path("C:/Users/win-10/AppData/Local/hermes")
class BrowserEgress(unittest.TestCase):
    @unittest.skipUnless(LIVE,"Use --live-public for public Internet + controlled loopback stub smoke")
    def test_public_browser_and_private_subrequest_fence(self):
        os.environ["HERMES_HOME"]=str(RUNTIME)
        sys.path.insert(0,str(RUNTIME/"hermes-agent"))
        plugin=ROOT/"integrations/hermes/plugins/komatso-public-research"
        spec=importlib.util.spec_from_file_location("public_browser_e2e",plugin/"__init__.py",submodule_search_locations=[str(plugin)])
        mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
        b=sys.modules[spec.name+".browser"]
        hits=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path);self.send_response(200);self.end_headers();self.wfile.write(b"PRIVATE_STUB_SENTINEL")
            def log_message(self,*_): pass
        listener=http.server.ThreadingHTTPServer(("127.0.0.1",0),Handler)
        threading.Thread(target=listener.serve_forever,daemon=True).start()
        target="http://127.0.0.1:"+str(listener.server_address[1])+"/private-stub"
        def call(action,args):
            return json.loads(b.browser_action(action,args,task_id="phase1-controlled-egress-test"))
        try:
            public=call("navigate",{"url":"https://example.com"})
            self.assertTrue(public.get("success"),public)
            self.assertIn("Example Domain",public["title"])
            self.assertTrue(call("screenshot",{}).get("success"))
            response=call("console",{"expression":"fetch("+json.dumps(target)+").then(r=>r.text()).catch(()=> 'DENIED')"})
            self.assertNotIn("PRIVATE_STUB_SENTINEL",json.dumps(response))
            self.assertFalse(hits)
            self.assertTrue(call("console",{"expression":"document.body.innerHTML='<a id=probe href="+target+">local stub</a>'; 'ready'"}).get("success"))
            self.assertFalse(call("click",{"target":"#probe"}).get("success"))
            self.assertFalse(hits)
        finally:
            b.close_all();listener.shutdown();listener.server_close()
if __name__=="__main__":unittest.main()
