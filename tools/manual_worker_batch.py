"""Maintenance CLI batches existing indexed PDF and configured web operations.

The model selects evidence gaps, images and a relevant URL. Deterministic code
runs independent operations concurrently, retaining each result/error/timing.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import manual_evidence_probe as probe

ROOT = probe.ROOT


def prepared_request(path):
    path = path.resolve()
    if not path.is_relative_to((ROOT / "runtime").resolve()):
        raise ValueError("Prepared request must be inside project runtime")
    request = json.loads(path.read_text(encoding="utf-8"))
    if request.get("model") not in probe.MODELS or not isinstance(request.get("question"),str):
        raise ValueError("Verified model/question required")
    return request


def web_operation(home, operation, **params):
    executable = home.parent.parent / "hermes-agent/venv/Scripts/python.exe"
    if not executable.is_file():
        return {"success":False,"error":"Existing Hermes interpreter unavailable; no retry."}
    try:
        response = subprocess.run([str(executable),"-I",str(ROOT/"tools/manual_worker_web.py")],
            input=json.dumps(dict(operation=operation,**params)).encode("utf-8"),
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60,
            env=dict(os.environ,HERMES_HOME=str(home)),
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if response.returncode:
            raise ValueError("Web bridge failed")
        return json.loads(response.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"success":False,"error":"Configured Hermes web operation unavailable or timed out; no retry."}


def run_probe(*args):
    response = subprocess.run([sys.executable,str(ROOT/"tools/manual_evidence_probe.py"),*map(str,args)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
    return json.loads(response.stdout)


def timed(operation, function):
    start = time.perf_counter()
    try:
        result = function()
        return operation,result,round(time.perf_counter()-start,3)
    except (OSError, ValueError, subprocess.CalledProcessError):
        return operation,{"success":False,"error":operation+" failed; required evidence must be reported missing."},round(time.perf_counter()-start,3)


def batch(operations):
    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        futures=[pool.submit(timed,name,function) for name,function in operations]
        completed=[future.result() for future in futures]
    return {name:result for name,result,_ in completed}, {name:seconds for name,_,seconds in completed}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("phase",choices=("retrieve","finish"))
    ap.add_argument("--request-file",type=Path,required=True)
    ap.add_argument("--render-pages",nargs="+",type=probe.page_numbers)
    ap.add_argument("--read-pages",nargs="+",type=probe.page_numbers)
    ap.add_argument("--fault-code",default="",help="Complete displayed failure code only")
    ap.add_argument("--component",default="",help="Known relevant component, when useful")
    ap.add_argument("--web-url",help="One model-selected directly relevant URL from the retrieval results")
    args=ap.parse_args()
    request=prepared_request(args.request_file)
    home=Path(request.get("profile_home") or os.environ.get("HERMES_HOME", "")).resolve()
    if home.name != "maintenance" or home.parent.name != "profiles":
        ap.error("This batch is only enabled in Maintenance")
    operations=[]
    if args.phase=="retrieve":
        if args.render_pages or args.read_pages or args.web_url:
            ap.error("Follow-up options belong to finish")
        terms=probe.search_terms(request["question"],args.component,args.fault_code)
        query=request["model"]+" "+" ".join(terms[:3])+" shop manual troubleshooting"
        probe_args=["--request-file",args.request_file,"--packet"]
        if args.fault_code:probe_args += ["--fault-code",args.fault_code]
        if args.component:probe_args += ["--component",args.component]
        operations=[("manual_packet",lambda:run_probe(*probe_args)),
                    ("web_search",lambda:web_operation(home,"search",query=query))]
    else:
        if args.render_pages:
            pages=[str(page) for group in args.render_pages for page in group]
            if len(set(pages))>8:ap.error("At most 8 rendered pages")
            operations.append(("rendered_pages",lambda:run_probe("--model",request["model"],"--pages",*pages,"--render-only")))
        if args.read_pages:
            reads=[str(page) for group in args.read_pages for page in group]
            if len(set(reads))>8:ap.error("At most 8 read pages")
            operations.append(("additional_text",lambda:run_probe("--model",request["model"],"--pages",*reads)))
        if args.web_url:
            search_file=args.request_file.with_suffix(".web.json")
            search=json.loads(search_file.read_text(encoding="utf-8"))
            urls={item.get("url") for item in search.get("data",{}).get("web",[])}
            if args.web_url not in urls:ap.error("Select a URL returned by the prepared retrieval search")
            operations.append(("web_extract",lambda:web_operation(home,"extract",url=args.web_url)))
        if not operations:ap.error("Select image pages, needed text pages or a relevant URL")
    started=time.perf_counter()
    results,timings=batch(operations)
    if args.phase=="retrieve":
        args.request_file.with_suffix(".web.json").write_text(json.dumps(results["web_search"],ensure_ascii=True),encoding="utf-8")
    results["batch_metrics"]={"operation_seconds":timings,"wall_seconds":round(time.perf_counter()-started,3),
        "backend_operations":len(operations),"parallel":True}
    print(json.dumps(results,ensure_ascii=True))

if __name__=="__main__":
    try:main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print("manual_worker_batch: "+type(exc).__name__,file=sys.stderr)
        raise SystemExit(2)
