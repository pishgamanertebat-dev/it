"""Real installed Hermes extraction only. Never import an Excel writer or send a message."""
import argparse
import asyncio
import importlib.abc
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--audit',type=Path,required=True)
    parser.add_argument('--actor',choices=('1732374823','654806764','641220453','387679249'),default='1732374823')
    args=parser.parse_args()
    core=HOME/'hermes-agent'
    sys.path[:0]=[str(ROOT),str(core),str(core/'venv/Lib/site-packages')]
    os.environ['HERMES_HOME']=str(HOME)
    import tools
    tools.__path__.extend([str(ROOT/'tools'),str(core/'tools')])
    class NoExcel(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            if fullname.startswith(('openpyxl','tools.fleet.repairs.entry_service','tools.fleet.repairs.entry_draft')):
                raise AssertionError('Excel/Writer forbidden in real-model extraction')
    sys.meta_path.insert(0,NoExcel())
    def no_operational_write(event,values):
        if event=='open':
            path,mode,flags=values
            if isinstance(path,(str,bytes)) and str(path).replace('\\','/').lower().startswith('e:/function/'):
                if (mode and any(c in mode for c in 'wax+')) or (flags and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT)):
                    raise AssertionError('Operational workbook write forbidden')
    sys.addaudithook(no_operational_write)
    audit=json.loads(args.audit.read_text('utf8'))
    def strings(v):
        if isinstance(v,str):yield v
        elif isinstance(v,dict):
            for item in v.values():yield from strings(item)
        elif isinstance(v,list):
            for item in v:yield from strings(item)
    raw=next(s for s in strings(audit) if '1.دامتراک 707' in s)
    from tools.fleet.repairs.entry_extract import extract
    route={}
    try:
        result=asyncio.run(extract(raw,args.actor,route_info=route))
    except Exception as exc:
        failure=dict(passed=False,real_model=True,quality_verified=False,error_type=type(exc).__name__,
            http_status=getattr(exc,'status_code',None),route={k:v for k,v in route.items() if k in ('provider','model','api_mode')},
            tools_exposed=False,excel_imports_blocked=True,operational_writes_blocked=True,transport_used=False)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(failure,indent=2),'utf8')
        raise RuntimeError('Real model acceptance failed; see sanitized evidence. Do not enable AI entry.') from exc
    # This is human-readable evidence, with explicit assertions about this sample.
    items=result['items']
    checks=dict(original_preserved=result['raw_text']==raw,
        two_473=sum(i['raw_code']=='473' for i in items)==2,
        missing_devices_retained=sum(i['raw_code'] is None for i in items)>=2,
        ambiguous_parts_preserved=any('جنت افتر' in i['raw_description'] for i in items),
        code_718_retained=any(i['raw_code']=='718' for i in items),
        no_fictional_number_7=9<=len(items)<=10,
        stated_date_preserved=result['report_date'] is not None and '13' in result['report_date'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(passed=all(checks.values()),checks=checks,extraction=result,
        real_model=True,profile='net' if args.actor=='641220453' else 'maintenance',route={k:v for k,v in route.items() if k in ('provider','model','api_mode')},tools_exposed=False,excel_imports_blocked=True,
        operational_writes_blocked=True,transport_used=False),ensure_ascii=False,indent=2),'utf8')
    print(json.dumps(checks,ensure_ascii=False))
    assert all(checks.values()),'Real model sample needs review; do not deploy'


if __name__=='__main__':main()
