"""Source samples, performance, legacy invariants and final evidence receipts."""
from __future__ import annotations
import argparse
import contextlib
import io
import json
from pathlib import Path
import random
import re
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tools/fleet')]
import manual_evidence_probe as manual
import partbook_index as index
import partbook_lookup as lookup
import pymupdf

OUT=ROOT/'runtime/roc-l8-onboarding'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',type=Path,required=True);args=ap.parse_args()
    with contextlib.closing(sqlite3.connect(args.db)) as con:
        baseline=json.loads((OUT/'legacy-baseline.json').read_text(encoding='utf-8'))
        legacy={bid:index.book_digest(con,bid)==b['digest'] and list(con.execute('select * from books where book_id=?',(bid,)).fetchone())==b['row'] for bid,b in baseline.items()}
        assert all(legacy.values()),legacy
        stats={bid:json.loads(con.execute('select value from meta where key=?',('stats:'+bid,)).fetchone()[0]) for bid in ('ROC-L8-D2','ROC-L8-LF-D3')}
        header_crosscheck=[]
        for bid in stats:
            header_pages=[];no_parts=[]
            with pymupdf.open(ROOT/index.BOOKS[bid]['pdf']) as pdf:
                for pi,page in enumerate(pdf):
                    text=re.sub(r'\s+',' ',page.get_text())
                    if all(token in text for token in ['Ref no','Part no','Qty','Description','Remarks']):
                        header_pages.append(pi+1)
                        if 'NO PARTS AVAILABLE FOR THIS ARTICLE' in text:no_parts.append(pi+1)
            recorded=[r[0] for r in con.execute("SELECT pdf_page FROM atlas_pages WHERE book_id=? AND page_kind='table'",(bid,))]
            rows=[r[0] for r in con.execute('SELECT DISTINCT pdf_page FROM part_occurrences WHERE book_id=?',(bid,))]
            empty=sorted(set(header_pages)-set(rows))
            if set(header_pages)!=set(recorded) or set(empty)!=set(no_parts):
                raise ValueError('Table headers or source empty-table statements not accounted for: '+bid)
            header_crosscheck.append(dict(book_id=bid,independently_detected_header_pages=len(header_pages),recorded_table_pages=len(recorded),
                tables_with_rows=len(rows),tables_without_rows=empty,source_no_parts_available=no_parts,unaccounted_headers=[]))
        samples=[];rng=random.Random(20261115)
        for bid in stats:
            rows=con.execute("select pn_raw,fig_no,item_raw,pdf_page from part_occurrences where book_id=? and pn_norm is not null order by pdf_page,row_ordinal",(bid,)).fetchall()
            for pn,fig,ref,page in rng.sample(rows,32):
                argv=['--model','ROC-L8','--book',bid,'--part-number',pn,'--figure',fig,'--verify','--limit','200','--db',str(args.db)]
                if ref:argv+=['--item-ref',ref]
                with contextlib.redirect_stdout(io.StringIO()):r=lookup.main(argv)
                candidates=[c for c in r['candidates'] if c['pdf_pages']==[page] and c['item_raw']==ref]
                passed=bool(candidates) and all(c['pdf_verification']['status']=='VERIFIED' for c in candidates)
                samples.append(dict(book_id=bid,part_number=pn,figure=fig,item_ref=ref,pdf_page=page,verified=passed))
        manual_perf=[]
        for model,q in [('ROC-L8','feed'),('ROC-L8','compressor'),('ROC-L8','hydraulic'),('ROC-L8','PLC'),('PC800-8R','engine starting')]:
            path=ROOT/manual.MODELS[model]/'manual_sections.json';meta=json.loads(path.read_text(encoding='utf-8'))
            terms=manual.search_terms(q);start=time.perf_counter()
            result=manual.probe(path,meta,manual.route(meta['sections'],terms,q),terms)
            manual_perf.append(dict(model=model,question=q,seconds=round(time.perf_counter()-start,4),
                searched_pages=result['searched_pages'],evidence_pages=[e['pdf_page'] for e in result['evidence']],full_fallback=result['full_manual_fallback']))
        query_perf=[]
        from types import SimpleNamespace
        con.row_factory=sqlite3.Row
        for books in [['ROC-L8-D2'],['ROC-L8-D2','ROC-L8-LF-D3'],['PC800-8']]:
            for pn in ['3222 3115 62','0147 1405 03'] if books[0].startswith('ROC') else ['209-01-42232']:
                a=SimpleNamespace(resolved_book_ids=books,part_number=pn,part_number_prefix=None,figure=None,item=None,item_ref=None,group=None,query=None,limit=10)
                trace=[];con.set_trace_callback(trace.append);times=[]
                for _ in range(20):
                    start=time.perf_counter();rows=lookup.search(con,a);times.append((time.perf_counter()-start)*1000)
                con.set_trace_callback(None)
                query_perf.append(dict(books=books,part_number=pn,queries_per_lookup=len(trace)/20,mean_ms=round(sum(times)/len(times),4),
                    min_ms=round(min(times),4),max_ms=round(max(times),4),rows=len(rows),book_ids_in_results=sorted({r['book_id'] for r in rows})))
        result=dict(legacy_preservation=legacy,book_stats=stats,source_samples=samples,sample_passed=sum(s['verified'] for s in samples),
                    sample_count=len(samples),manual_performance=manual_perf,part_query_performance=query_perf,
                    all_rows_verified=False,all_machine_fit_verified=False,header_crosscheck=header_crosscheck)
        (OUT/'table-header-crosscheck.json').write_text(json.dumps(header_crosscheck,indent=2),encoding='utf-8')
        (OUT/'acceptance-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        if all(s['verified'] for s in samples):
            from partbook_atlas import document_digest
            (OUT/'accepted-candidate-digests.json').write_text(json.dumps({bid:dict(index=index.book_digest(con,bid),atlas=document_digest(con,bid)) for bid in stats},indent=2),encoding='utf-8')
        print(json.dumps({k:result[k] for k in ('legacy_preservation','sample_passed','sample_count','manual_performance','part_query_performance')},ensure_ascii=True))
        if not all(s['verified'] for s in samples):raise SystemExit('Source sample verification failed; do not promote index')

if __name__=='__main__':main()
