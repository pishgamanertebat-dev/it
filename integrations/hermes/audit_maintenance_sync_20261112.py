from pathlib import Path
import hashlib, io, json, sqlite3, zipfile
import openpyxl

ROOT = Path('E:/KomatsoAI')
HOME = Path('C:/Users/win-10/AppData/Local/hermes')
OUT = ROOT/'runtime/sync-repair-20261112'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT/'before.json').exists():
        raise RuntimeError('Initial incident evidence already exists; never overwrite it')
    result = {}
    state = json.loads((HOME / 'state/mine-file-sync.json').read_text('utf8'))
    result['sync'] = {k: state.get(k) for k in ('last_run','last_successful_run','health_status','sync_healthy','last_recovery_at')}
    result['sources'] = []
    local = Path(json.loads((ROOT/'settings/maintenance_entry.json').read_text('utf8'))['source'])
    for p in (local, Path('//192.168.20.251/share')/local.name):
        info = {'path':str(p)}
        try:
            data = p.read_bytes()
            info.update(sha256=sha(data),size=len(data),attributes=p.stat().st_file_attributes,mtime=p.stat().st_mtime)
            (OUT/(sha(data)+'.xlsx')).write_bytes(data)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                import xml.etree.ElementTree as ET
                tree=ET.fromstring(z.read('xl/workbook.xml'))
                info['workbook_settings']=[ET.tostring(e,encoding='unicode') for e in tree if any(s in e.tag for s in ('fileSharing','workbookProtection'))]
            book=openpyxl.load_workbook(io.BytesIO(data))
            info['sheets']=len(book.sheetnames)
            book.close()
            info['owner_files']=[str(f) for f in p.parent.glob('~$*') if '1405' in f.name]
        except Exception as exc:
            info['error']=str(exc)
        result['sources'].append(info)
    for name,dbfile in [('scheduler',ROOT/'runtime/scheduler/runs.sqlite3'),('maintenance',ROOT/'runtime/maintenance-entry/audit.sqlite3')]:
        c=sqlite3.connect(dbfile.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
        dest=sqlite3.connect(OUT/(name+'-before.sqlite3'));c.backup(dest);dest.close()
        result[name]={}
        if name=='scheduler':
            for table in ('runs','receipts','report_recovery_hold','report_recovery_operation','report_correction_review'):
                rows=c.execute('SELECT * FROM '+table).fetchall()
                result[name][table]=[dict(r) for r in rows if 'maintenance' in json.dumps(dict(r))]
        else:
            for table in ('edits','maintenance_baseline_events','maintenance_report_messages'):
                result[name][table]=[dict(r) for r in c.execute('SELECT rowid,* FROM '+table)]
        c.close()
    (OUT/'before.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps({'sources':result['sources'],'sync':result['sync'],'recent_runs':result['scheduler']['runs'][-5:]},ensure_ascii=False))

if __name__=='__main__': main()
