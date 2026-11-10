"""Exercise reviewed guard against real snapshots and a disposable Journal; no publish."""
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import sys,json,sqlite3,shutil,hashlib
ROOT=Path('E:/KomatsoAI');OUT=Path(__file__).resolve().parent
STAGE=ROOT/'runtime/scheduler-repair-20261110/stage';sys.path.insert(0,str(STAGE))
from tools.fleet.repairs.maintenance_baseline import sync_from_baseline,validate_workbook,additions

def main():
    sources=json.loads((OUT/'current-sources.json').read_text('utf8'))
    original=Path(sources['maintenance_local']['snapshot']).read_bytes()
    incoming=Path(sources['maintenance_mine']['snapshot']).read_bytes()
    runtime=OUT/'sync-rehearsal';runtime.mkdir(exist_ok=True)
    live=ROOT/'runtime/maintenance-entry'
    with sqlite3.connect((live/'audit.sqlite3').as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(runtime/'audit.sqlite3') as dst:
        src.backup(dst);assert dst.execute('pragma integrity_check').fetchone()[0]=='ok'
    (runtime/'backups').mkdir(exist_ok=True)
    for p in (live/'backups').glob('*.xlsx'):
        shutil.copy2(p,runtime/'backups'/p.name)
    source=Path(sources['maintenance_local']['source'])
    calls=[]
    def publish(path,before,candidate,db,actor,request,result,**kw):
        assert path==source and before==original and candidate==incoming
        calls.append(dict(candidate_sha256=hashlib.sha256(candidate).hexdigest(),result=result))
        return dict(result,no_publish=True)
    with closing(sqlite3.connect(runtime/'audit.sqlite3')) as db:
        db.row_factory=sqlite3.Row
        before=[tuple(r) for r in db.execute('SELECT * FROM edits')]
        with patch('tools.fleet.repairs.maintenance_baseline.publish_candidate',publish):
            result=sync_from_baseline(db,source,original,incoming,runtime)
        assert before==[tuple(r) for r in db.execute('SELECT * FROM edits')]
    assert source.read_bytes()==original
    data=dict(result=result,calls=calls,journal_unchanged=True,operational_workbook_unchanged=True)
    (OUT/'sync-rehearsal.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps(data,ensure_ascii=False))

if __name__=='__main__':main()
