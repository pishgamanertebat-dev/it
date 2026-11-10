"""Read-only exact-date source and Sync comparison; pinned evidence only."""
from pathlib import Path
from datetime import datetime,timezone
import sys,io,json,hashlib,sqlite3,zipfile
import openpyxl
ROOT=Path('E:/KomatsoAI');sys.path.insert(0,str(ROOT))
from tools.fleet.repairs.maintenance_baseline import baseline_reader,sheet_state,xml,additions
from tools.fleet.maintenance_daily.report import scan as maintenance_scan
from tools.fleet.oil_balance.report import scan as oil_scan
from tools.fleet.machinery_performance.report import scan as performance_scan
from integrations.hermes.function_domain.driver_report import load_driver
from tools.fleet.overflow.report import load_report
OUT=Path(__file__).resolve().parent
DATES=[f'1405/08/{day:02d}' for day in range(15,20)]

def save(name,data):
    p=OUT/name
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),'utf8')

def pinned(label,path):
    with baseline_reader(path) as f:data=f.read()
    sha=hashlib.sha256(data).hexdigest()
    dest=OUT/'sources'/(label+'-'+sha+'.xlsx');dest.parent.mkdir(exist_ok=True)
    if not dest.exists():dest.write_bytes(data)
    assert hashlib.sha256(dest.read_bytes()).hexdigest()==sha
    with zipfile.ZipFile(io.BytesIO(data)) as z:assert z.testzip() is None
    return data,dict(source=str(path),snapshot=str(dest),sha256=sha,size=len(data),captured=datetime.now(timezone.utc).isoformat())

def compare(local,mine):
    books=[openpyxl.load_workbook(io.BytesIO(x)) for x in (local,mine)]
    a,b=books;result=[]
    try:
        for name in sorted(set(a.sheetnames)|set(b.sheetnames)):
            if name not in a or name not in b:result.append(dict(sheet=name,missing=True));continue
            old,new=a[name],b[name];x,y=sheet_state(old),sheet_state(new)
            cells=[]
            for address in sorted(set(x['cells'])|set(y['cells'])):
                left,right=old[address],new[address]
                if x['cells'].get(address)==y['cells'].get(address):continue
                cells.append(dict(cell=address,old=left.value,new=right.value,
                    kind='value_or_formula' if (left.value,left.data_type)!=(right.value,right.data_type) else 'style_comment_link'))
            props={k:dict(old=x[k],new=y[k]) for k in x if k!='cells' and x[k]!=y[k]}
            if cells or props:result.append(dict(sheet=name,cells=cells,structure=props))
        guard='accepted'
        try:additions(a,b)
        except Exception as exc:guard=str(exc)
        return dict(sheets=result,guard=guard,sheetnames_equal=a.sheetnames==b.sheetnames,
            workbook_security_equal=xml(a.security)==xml(b.security),calculation_equal=xml(a.calculation)==xml(b.calculation),
            defined_names_equal=dict(a.defined_names)==dict(b.defined_names))
    finally:
        for book in books:book.close()

def main():
    sources={};data={}
    paths=dict(drivers=Path('E:/Function/گزارش روزانه رانندگان2.xlsx'),overflow=Path('E:/Function/سرریز روزانه.xlsx'),
        maintenance_local=Path('E:/Function/تعمیرات 1405.xlsx'),maintenance_mine=Path(r'\\192.168.20.251\share\تعمیرات 1405.xlsx'),
        oil=Path('E:/Function/موازنه روغن مصرفی.xlsx'),performance=Path('E:/Function/عملکرد ماشین آلات.xlsx'))
    for label,path in paths.items():
        try:
            raw,meta=pinned(label,path);data[label]=raw;sources[label]=meta;reports=[]
            for day in DATES:
                try:
                    if label=='drivers':r=load_driver(day,data=raw)
                    elif label.startswith('maintenance'):r=maintenance_scan(raw,day)
                    elif label=='oil':r=oil_scan(raw,day)
                    elif label=='performance':r=performance_scan(raw,day)
                    else:
                        r=load_report(day,Path(meta['snapshot']))
                    reports.append(dict(date=day,report=r))
                except Exception as exc:reports.append(dict(date=day,error=type(exc).__name__,reason=getattr(exc,'reason',str(exc)),details=getattr(exc,'details',None)))
            meta['reports']=reports
        except Exception as exc:sources[label]=dict(error=type(exc).__name__,errno=getattr(exc,'errno',None),winerror=getattr(exc,'winerror',None))
        save('current-sources.json',sources)
    if all(x in data for x in ('maintenance_local','maintenance_mine')):
        save('maintenance-differences.json',compare(data['maintenance_local'],data['maintenance_mine']))
    print(json.dumps({k:{p:v for p,v in val.items() if p!='reports'} for k,val in sources.items()},ensure_ascii=False))

if __name__=='__main__':main()
