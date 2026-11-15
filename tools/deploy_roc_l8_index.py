"""Guarded Technical Docs index promotion/rollback. Never reloads the Gateway.

Default: prepare a valid Online Backup and rehearse rollback on an isolated copy.
--apply installs the already tested candidate atomically after digest/hash guards.
--rollback RECEIPT --apply restores the exact pre-install index only if unchanged.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from zoneinfo import ZoneInfo
import partbook_index as index
from partbook_atlas import document_digest

ROOT=index.ROOT
OUT=ROOT/'runtime/roc-l8-onboarding'
PRODUCTION=index.DEFAULT_DB
NEW_BOOKS={'ROC-L8-D2','ROC-L8-LF-D3'}

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def backup(source,target):
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup integrity failed')

def snapshot(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Index integrity failed')
        return {bid:dict(row=list(con.execute('select * from books where book_id=?',(bid,)).fetchone()),digest=index.book_digest(con,bid),
            atlas_digest=document_digest(con,bid) if bid in NEW_BOOKS else None,
            coverage=[list(r) for r in con.execute('select * from book_coverages where book_id=? order by rowid',(bid,))])
            for bid, in con.execute('select book_id from books')}

def atomic_copy(source,target):
    temporary=Path(target).with_name(Path(target).name+'.roc-l8-installing')
    try:
        shutil.copyfile(source,temporary)
        with temporary.open('r+b') as stream:os.fsync(stream.fileno())
        if sha(source)!=sha(temporary):raise ValueError('Staged bytes differ')
        os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--candidate',type=Path,default=OUT/'acceptance-final.sqlite')
    ap.add_argument('--apply',action='store_true');ap.add_argument('--rollback',type=Path)
    ap.add_argument('--replace-installed',action='store_true',help='Replace only the exact ROC deployment named in the current receipt; legacy books remain immutable')
    args=ap.parse_args()
    if args.rollback:
        receipt=json.loads(args.rollback.read_text(encoding='utf-8'))
        if sha(PRODUCTION)!=receipt['installed_sha256']:raise ValueError('Index changed since ROC install; rollback requires separate review')
        source=Path(receipt['backup'])
        if sha(source)!=receipt['backup_sha256']:raise ValueError('Backup changed')
        if not args.apply:print('Rollback validated; pass --apply to restore Technical Docs index');return
        atomic_copy(source,PRODUCTION);print('Restored exact pre-ROC Technical Docs index; Gateway untouched');return
    candidate=args.candidate.resolve()
    if not candidate.is_relative_to((ROOT/'runtime').resolve()) or candidate==PRODUCTION.resolve():
        raise ValueError('Select an independent candidate inside runtime')
    evidence=json.loads((OUT/'acceptance-evidence.json').read_text(encoding='utf-8'))
    if evidence['sample_count']!=64 or evidence['sample_passed']!=64 or not all(evidence['legacy_preservation'].values()):
        raise ValueError('Candidate acceptance is incomplete')
    baseline=json.loads((OUT/'legacy-baseline.json').read_text(encoding='utf-8'))
    before_sha=sha(PRODUCTION);before=snapshot(PRODUCTION);after=snapshot(candidate)
    previous=None
    if NEW_BOOKS & set(before):
        if not args.replace_installed:raise ValueError('ROC already installed; explicit --replace-installed required')
        previous=json.loads((OUT/'deployment-receipt.json').read_text(encoding='utf-8'))
        if previous.get('installed_sha256')!=before_sha or NEW_BOOKS-set(before):
            raise ValueError('Installed index differs from the prior controlled ROC receipt')
    with closing(sqlite3.connect(PRODUCTION.resolve().as_uri()+'?mode=ro',uri=True)) as con:
        if con.execute('PRAGMA journal_mode').fetchone()[0]!='delete':
            raise ValueError('Index is not in DELETE journal mode; atomic replacement needs separate review')
    if set(after)!=set(baseline)|NEW_BOOKS or set(before)-NEW_BOOKS!=set(baseline):raise ValueError('Unexpected book set')
    for bid,old in before.items():
        if bid in NEW_BOOKS:continue
        if after.get(bid)!=old or old['digest']!=baseline[bid]['digest'] or old['row']!=baseline[bid]['row']:
            raise ValueError('Legacy data changed: '+bid)
    # Evidence is bound to these exact tested logical rows, not just row counts.
    accepted=json.loads((OUT/'accepted-candidate-digests.json').read_text(encoding='utf-8'))
    if {bid:dict(index=after[bid]['digest'],atlas=after[bid]['atlas_digest']) for bid in NEW_BOOKS}!=accepted:raise ValueError('Candidate differs from accepted rows')
    manifest=json.loads((ROOT/'ROC-L8/document_manifest.json').read_text(encoding='utf-8'))
    for document in manifest['documents']:
        if sha(ROOT/document['file_path'])!=document['sha256']:raise ValueError('Original PDF changed')
    stamp=datetime.now(ZoneInfo('Asia/Tehran')).strftime('%Y%m%dT%H%M%S')
    backup_path=OUT/('production-before-ROC-'+stamp+'.sqlite')
    backup(PRODUCTION,backup_path)
    if snapshot(backup_path)!=before:raise ValueError('Online Backup differs from production baseline')
    whole_backup=Path(previous['backup']) if previous else backup_path
    if previous and sha(whole_backup)!=previous['backup_sha256']:raise ValueError('Original pre-ROC backup changed')
    expected_rollback={bid:record for bid,record in before.items() if bid not in NEW_BOOKS}
    rehearsal=OUT/('rollback-rehearsal-'+stamp+'.sqlite')
    atomic_copy(candidate,rehearsal);atomic_copy(whole_backup,rehearsal)
    if snapshot(rehearsal)!=expected_rollback:raise ValueError('Isolated full-package rollback failed')
    receipt=dict(timestamp_tehran=stamp,production=str(PRODUCTION),candidate=str(candidate),backup=str(whole_backup),
        before_sha256=before_sha,backup_sha256=sha(whole_backup),candidate_sha256=sha(candidate),
        immediate_before_backup=str(backup_path),immediate_before_backup_sha256=sha(backup_path),
        legacy_preserved=sorted(baseline),added_books=sorted(NEW_BOOKS),rollback_rehearsal='VERIFIED',gateway_reloaded=False,
        gateway_restarted=False,operational_messages_sent=False,status='PREPARED')
    if sha(PRODUCTION)!=before_sha:raise ValueError('Production changed during preparation')
    if args.apply:
        try:
            atomic_copy(candidate,PRODUCTION)
            if snapshot(PRODUCTION)!=after:raise ValueError('Installed index differs from candidate')
        except BaseException:
            if sha(PRODUCTION)==receipt['candidate_sha256']:atomic_copy(backup_path,PRODUCTION)
            raise
        receipt.update(status='INDEX_INSTALLED_ACTIVATION_PENDING',installed_sha256=sha(PRODUCTION))
    path=OUT/'deployment-receipt.json';path.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    if previous:(OUT/('deployment-receipt-before-'+stamp+'.json')).write_text(json.dumps(previous,indent=2),encoding='utf-8')
    print(json.dumps(receipt))

if __name__=='__main__':main()
