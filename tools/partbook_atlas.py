"""Atlas Copco bilingual Ref/PN tables. Geometry is in displayed page space.

No OCR, no serial inference, and no cross-book deduplication. Unreadable PN
cells remain NULL; source rows and all continuation/illustration links survive.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import pymupdf

PN = re.compile(r'^\d{4}\s+\d{4}\s+\d{2}$|^[A-Z0-9][A-Z0-9.-]{4,24}$')
REF = re.compile(r'^[A-Za-z0-9][A-Za-z0-9,:./*-]{0,23}$')
DETAIL_SCHEMA = '''
CREATE TABLE IF NOT EXISTS atlas_pages (
 book_id TEXT, pdf_page INTEGER, printed_page TEXT, title TEXT,
 assembly_number TEXT, revision TEXT, drawing TEXT, table_sheet INTEGER,
 table_sheets INTEGER, page_kind TEXT, fig_no TEXT, verification_status TEXT,
 PRIMARY KEY(book_id,pdf_page));
CREATE TABLE IF NOT EXISTS atlas_rows (
 occ_id INTEGER PRIMARY KEY, benamning TEXT, see_page_raw TEXT,
 see_page_targets TEXT, remarks TEXT, assembly_number TEXT, revision TEXT);
'''

def peak_working_set_mib():
    """Own-process Windows peak, including the additive preservation checks."""
    import sys
    if sys.platform!='win32':return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in
            ('peak','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile')]
    counters=Counters();counters.cb=ctypes.sizeof(counters)
    kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentProcess.restype=wintypes.HANDLE
    psapi=ctypes.WinDLL('psapi');psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb):return None
    return round(counters.peak/1024**2,2)

def document_digest(con,book_id):
    """Bind manufacturer-specific page/row provenance as well as shared rows."""
    pages=con.execute('SELECT * FROM atlas_pages WHERE book_id=? ORDER BY pdf_page',(book_id,)).fetchall()
    rows=con.execute('SELECT a.* FROM atlas_rows a JOIN part_occurrences o ON o.occ_id=a.occ_id WHERE o.book_id=? ORDER BY a.occ_id',(book_id,)).fetchall()
    return hashlib.sha256(json.dumps([list(map(list,pages)),list(map(list,rows))],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def parse_page(page):
    from partbook_index import page_words, cluster_rows, rect_str
    words=page_words(page)
    lines=cluster_rows(words)
    text=page.get_text()
    title_lines=[r for r in lines if r and max(w[3] for w in r)<80 and any('/' in w[4] for w in r)]
    title=' '.join(w[4] for w in title_lines[0]) if title_lines else ''
    footer=[r for r in lines if r and min(w[1] for w in r)>page.rect.height-75]
    assembly=revision=drawing=None
    for r in footer:
        s=' '.join(w[4] for w in r)
        m=re.fullmatch(r'(\d{4} \d{4} \d{2})\s+([0-9]{3}[A-Z]\w*)',s)
        if m: assembly,revision=m.groups()
        m=re.fullmatch(r'([A-Z]\d{6,})',s)
        if m: drawing=m[1]
    # Printed page is an isolated numeric footer label, not an assumed offset.
    labels=[r[0][4] for r in footer if len(r)==1 and r[0][4].isdigit() and len(r[0][4])<5]
    printed=labels[-1] if len(labels)==1 else None
    sheet=re.search(r'\(Sheet\s+(\d+)\s+of\s+(\d+)\)',text)
    meta=dict(printed_page=printed,title=title,assembly_number=assembly,revision=revision,drawing=drawing,
              table_sheet=int(sheet[1]) if sheet else 1,table_sheets=int(sheet[2]) if sheet else 1)
    headers=[r for r in lines if {'Description','Qty','Remarks'}.issubset({w[4] for w in r})
             and any(w[4]=='Ref' for w in r)]
    if not headers:
        graphics=bool(page.get_images())
        if not graphics and title and not drawing:
            graphics=any((d['rect']*page.rotation_matrix).intersects(pymupdf.Rect(20,80,page.rect.width-20,page.rect.height-90))
                         for d in page.get_drawings())
        meta['page_kind']=('contents' if 'LIST OF CONTENTS' in text else
            'cross_reference' if 'Part no' in text and 'Description' in text and 'See page' in text else
            'illustration' if title and (drawing or graphics) else
            'identity_only' if title else 'front_or_blank' if len(text.strip())<100 else 'unclassified')
        return meta,[],[]
    h=headers[0]; pos={w[4]:w[0] for w in h}
    if not all(k in pos for k in ('Part','Qty','Description','See','Remarks')):
        meta['page_kind']='ambiguous_header';return meta,[],[]
    sw=next((w[0] for w in h if w[4].startswith('Ben')),None)
    if sw is None:
        meta['page_kind']='ambiguous_header';return meta,[],[]
    bounds=[0,pos['Part']-5,pos['Qty']-25,pos['Description']-4,sw-4,pos['See']-4,pos['Remarks']-4,page.rect.width]
    top=max(w[3] for r in lines for w in r if h[0][1]-1 <= w[1] <= h[0][1]+16)+1
    bottom=page.rect.height-65
    body=[w for w in words if w[1]>=top and w[3]<bottom]
    anchors=[]
    for line in cluster_rows(body):
        ref_words=[w for w in line if w[0]<bounds[1]]
        pn_words=[w for w in line if bounds[1]<=w[0]<bounds[2]]
        qty_words=[w for w in line if bounds[2]<=w[0]<bounds[3]]
        desc_words=[w for w in line if bounds[3]<=w[0]<bounds[4]]
        ref=' '.join(w[4] for w in ref_words)
        pn=' '.join(w[4] for w in pn_words)
        # Some service-kit rows have no printed Ref. They must not be appended
        # to the preceding numbered row. Preserve the empty Ref explicitly.
        if (ref and (REF.fullmatch(ref) or ref in ('-','*'))) or (not ref and pn_words and desc_words and
                re.fullmatch(r'\d+(?:[.,]\d+)?(?:M|MM)?|AR|A/R|-',' '.join(w[4] for w in qty_words),re.I)):
            anchors.append((0,min(w[1] for w in line),0,0,ref))
    anchors.sort(key=lambda w:w[1])
    rows=[]; anomalies=[]
    for i,anchor in enumerate(anchors):
        y0=anchor[1]-.8;y1=anchors[i+1][1]-.8 if i+1<len(anchors) else bottom
        row_words=[w for w in body if y0<=(w[1]+w[3])/2<y1]
        cells=[]
        for lo,hi in zip(bounds,bounds[1:]):
            cell=[w for w in row_words if lo<=w[0]<hi]
            cells.append(' '.join(w[4] for r in cluster_rows(cell) for w in r))
        ref,pn,qty,desc,ben,see,remarks=cells
        flags=[]
        if not anchor[4]:flags.append('ref_not_printed')
        if anchor[4] in ('-','*'):flags.append('ref_marker_only')
        if ref!=anchor[4]: flags.append('ambiguous_ref_cell')
        if pn and not PN.fullmatch(pn):
            flags.append('source_part_number_symbol' if len(pn)==1 and not pn.isalnum() else 'ambiguous_part_number_cell')
            pn=None
        if not pn: flags.append('part_number_not_readable_or_reference_only')
        if not re.fullmatch(r'\d+(?:[.,]\d+)?(?:M|MM)?|AR|A/R|-',qty,re.I):flags.append('ambiguous_quantity')
        if not desc:flags.append('missing_description')
        bbox=pymupdf.Rect(0,y0,page.rect.width,y1)
        raw='\n'.join(' '.join(w[4] for w in r) for r in cluster_rows(row_words))
        rows.append(dict(ref=anchor[4],pn=pn,qty=qty,description=desc,benamning=ben,see_page_raw=see,
                         remarks=remarks,bbox=rect_str(bbox),raw_text=raw,flags=flags))
    # Preserve an explicit reason for a header page without extracted rows.
    if not rows:anomalies.append('source_states_no_parts_available' if 'NO PARTS AVAILABLE FOR THIS ARTICLE' in text else 'table_header_without_ref_rows')
    meta['page_kind']='table'
    return meta,rows,anomalies

def build(book_id,db_path):
    import partbook_index as pb
    book=pb.BOOKS[book_id];pdf=pb.ROOT/book['pdf'];started=time.perf_counter()
    db_path=Path(db_path);db_path.parent.mkdir(parents=True,exist_ok=True)
    tmp=db_path.with_suffix('.atlas-building')
    if tmp.exists(): tmp.unlink()
    con=sqlite3.connect(tmp)
    try:
        if db_path.exists():
            source=sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True)
            try:source.backup(con)
            finally:source.close()
        else:con.executescript(pb.SCHEMA)
        con.executescript(DETAIL_SCHEMA)
        preserved={b:pb.book_digest(con,b) for b, in con.execute('select book_id from books where book_id<>?',(book_id,))}
        # This path is explicitly additive. Replacing a ROC build requires a fresh
        # staging copy without ROC books; never overwrite any existing book here.
        if con.execute('select 1 from books where book_id=?',(book_id,)).fetchone():
            raise ValueError('Atlas book already present; build a fresh candidate, not an in-place rebuild')
        con.execute('BEGIN')
        pages=[];all_rows=[];figs={};last_fig=None;pending=None;unlinked=[];flag_counts=Counter();groups=[];current_group='ATLAS000'
        with pymupdf.open(pdf) as doc:
            for pi,page in enumerate(doc):
                pno=pi+1;meta,rows,anomalies=parse_page(page)
                meta.update(pdf_page=pno,fig_no=None,anomalies=anomalies,rotation=page.rotation)
                title=meta['title']
                if meta['page_kind']=='contents':
                    if groups:groups[-1]['end']=pno-1
                    current_group=f'ATLAS{len(groups)+1:03}'
                    groups.append(dict(code=current_group,title=title or 'UNKNOWN catalogue group',start=pno,end=len(doc)))
                if meta['page_kind']=='illustration':
                    pending=meta
                elif meta['page_kind']=='table':
                    if (last_fig and meta['table_sheet']>1 and not meta['assembly_number']
                            and figs[last_fig]['title']==title and figs[last_fig]['last']==pno-1
                            and meta['drawing'] and meta['drawing']==figs[last_fig].get('drawing')):
                        meta['assembly_number']=figs[last_fig]['assembly']
                        meta['revision']=figs[last_fig]['revision']
                        meta['anomalies'].append('continuation_identity_from_matching_title_sheet_and_drawing')
                    continues=(last_fig and meta['table_sheet']>1 and
                               figs[last_fig]['assembly']==meta['assembly_number'] and
                               figs[last_fig]['title']==title and
                               figs[last_fig]['revision']==meta['revision'] and
                               figs[last_fig]['last']==pno-1)
                    if continues:fig=last_fig
                    else:
                        first=(pending['pdf_page'] if pending and pending['pdf_page']==pno-1 and pending['title']==title else pno)
                        fig=f"{(meta['assembly_number'] or 'UNKNOWN').replace(' ','')}:{meta['revision'] or 'UNKNOWN'}:p{first}"
                        figs[fig]=dict(title=title,assembly=meta['assembly_number'],revision=meta['revision'],first=first,last=pno,rows=0,
                                       drawing=pending['drawing'] if first!=pno else meta['drawing'],group=current_group)
                        if first!=pno:pending['fig_no']=fig
                        else:unlinked.append(pno)
                    meta['fig_no']=fig;last_fig=fig;figs[fig]['last']=pno
                    for ordinal,row in enumerate(rows,1):
                        all_rows.append((fig,pno,ordinal,meta,row));figs[fig]['rows']+=1
                        flag_counts.update(row['flags'])
                    pending=None
                elif meta['page_kind']!='identity_only':
                    # An intervening page cannot be silently skipped to join figures.
                    pending=None
                pages.append(meta)
            page_count=len(doc)
        label_map={}
        for p in pages:
            if p['printed_page']:label_map.setdefault(p['printed_page'],[]).append(p)
        for fig,meta in figs.items():
            con.execute('INSERT INTO figures VALUES (?,?,?,?,?,?,?)',(book_id,fig,meta['title'],meta['group'],meta['first'],meta['last'],meta['rows']))
        con.execute('INSERT INTO groups VALUES (?,?,?,?,?)',(book_id,'ATLAS000','Front catalogue assemblies',1,groups[0]['start']-1 if groups else page_count))
        for group in groups:
            con.execute('INSERT INTO groups VALUES (?,?,?,?,?)',(book_id,group['code'],group['title'],group['start'],group['end']))
        for p in pages:
            con.execute('INSERT INTO atlas_pages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (book_id,p['pdf_page'],p['printed_page'],p['title'],p['assembly_number'],p['revision'],p['drawing'],p['table_sheet'],p['table_sheets'],p['page_kind'],p['fig_no'],'ROUTING_ONLY'))
            if p['fig_no']:
                con.execute('INSERT INTO figure_pages VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (book_id,p['fig_no'],p['pdf_page'],p['printed_page'],p['rotation'],int(p['page_kind']=='illustration'),None,None,int(p['table_sheet']>1),p['title']))
        occ=con.execute('select coalesce(max(occ_id),0) from part_occurrences').fetchone()[0]
        unresolved=0
        for fig,pno,ordinal,meta,row in all_rows:
            occ+=1;pn_norm=pb.norm_pn(row['pn'])[0] if row['pn'] else None
            con.execute('INSERT INTO part_occurrences VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (occ,book_id,fig,pno,ordinal,row['ref'],int(row['ref']) if row['ref'].isdigit() else None,None,0,
                 row['pn'],pn_norm,row['description'],0,row['qty'],None,row['bbox'],'atlas-bilingual-1',row['raw_text'],
                 'needs_verification',json.dumps(row['flags'])))
            targets=[]
            for label in re.findall(r'Page\s+(\d+)',row['see_page_raw'],re.I):
                matches=label_map.get(label,[])
                target=dict(printed_page=label,status='RESOLVED' if len(matches)==1 else 'UNKNOWN',
                            pdf_pages=[p['pdf_page'] for p in matches])
                targets.append(target)
                if len(matches)!=1:unresolved+=1
            con.execute('INSERT INTO atlas_rows VALUES (?,?,?,?,?,?,?)',
                (occ,row['benamning'],row['see_page_raw'],json.dumps(targets),row['remarks'],meta['assembly_number'],meta['revision']))
            con.execute('INSERT INTO parts_fts(rowid,description,figure_title,group_title,remarks,pn_raw) VALUES (?,?,?,?,?,?)',
                (occ,row['description'],meta['title'],next((g['title'] for g in groups if g['code']==figs[fig]['group']),'Atlas Copco'),row['remarks']+' '+row['benamning'],row['pn'] or ''))
        for fig,meta in figs.items():
            con.execute('INSERT INTO figures_fts(fig_no,title,group_title) VALUES (?,?,?)',(fig,meta['title'],next((g['title'] for g in groups if g['code']==meta['group']),'Atlas Copco')))
        sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
        con.execute('INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (book_id,book['model'],book['title'],book['pdf'],sha,page_count,'',None,None,None,'indexed_partial','atlas-bilingual-1',time.strftime('%Y-%m-%dT%H:%M:%S')))
        kinds=Counter(p['page_kind'] for p in pages)
        stats=dict(book_id=book_id,pages=page_count,list_pages=kinds['table'],rows=len(all_rows),figures=len(figs),
            page_kinds=dict(kinds),rows_with_pn=sum(bool(r[-1]['pn']) for r in all_rows),
            flagged_rows=sum(bool(r[-1]['flags']) for r in all_rows),
            ambiguous_rows=sum(any(f.startswith('ambiguous') for f in r[-1]['flags']) for r in all_rows),flags=dict(flag_counts),
            unlinked_table_pages=unlinked,unresolved_see_page=unresolved,
            unclassified_pages=[p['pdf_page'] for p in pages if p['page_kind']=='unclassified'],
            ambiguous_header_pages=[p['pdf_page'] for p in pages if p['page_kind']=='ambiguous_header'],
            coverage_complete=False,verification_status='INDEXED_PARTIAL_ROUTING_ONLY',seconds=round(time.perf_counter()-started,2))
        stats['peak_process_working_set_mib']=peak_working_set_mib()
        for p in pages:
            con.execute('INSERT INTO book_coverages VALUES (?,?,?,?,?,?)',
                (book_id,p['pdf_page'],p['pdf_page'],p['page_kind'],'indexed_routing' if p['fig_no'] else 'not_indexed',
                 json.dumps(dict(printed_page=p['printed_page'],anomalies=p['anomalies']))))
        con.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('stats:'+book_id,json.dumps(stats)))
        for bid,digest in preserved.items():
            if pb.book_digest(con,bid)!=digest:raise ValueError('Legacy digest changed: '+bid)
        con.commit()
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Candidate integrity failed')
        con.close();tmp.replace(db_path)
        return stats
    except BaseException:
        con.close()
        tmp.unlink(missing_ok=True)
        raise
