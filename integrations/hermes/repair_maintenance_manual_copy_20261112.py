"""Pinned repair of the owner's 2026-11-12 Share copy; default is preparation only.

Retain the copied source and exact prior Writer history. Reconstruct only the
three known UUID batches, never infer report identity from matching prose.
"""
from contextlib import closing
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import uuid

from tools.fleet.repairs import maintenance_service as service
from tools.fleet.repairs.entry_service import open_audit, publish_candidate, source_reader, writer_lock
from tools.fleet.repairs.maintenance_baseline import KIND, combine_report_batches, snapshot
from tools.fleet.maintenance_daily.prepared import source_proof
from tools.fleet.maintenance_daily.report import scan

ROOT = service.ROOT
OUT = ROOT/'runtime/sync-repair-20261112'
COPIED = 'e60243ddab1cd7f16408a3615fbf5a7b8993b0ae39216601200b39d4989e9b05'
HEAD = '74101bd3916b9b3bb046d0260d78480dfc6b00320cbc0946b01721fe9a6ce318'
BASE = '3936d913824e18b0d64c300522e38316f873dedabdfab81d7c5617badd2f5520'
OPERATIONS = ['bb9adf4f871659ce9043dace1f49c2b1', '72fc231498da5a34aa1dad9e294b5856',
              '1643333071a655248008413804373396']


def digest(data): return hashlib.sha256(data).hexdigest()


def prepare():
    settings = json.loads(service.CONFIG.read_text('utf8'))
    source = Path(settings['source']).resolve(strict=True)
    source_proof(HEAD, runtime=service.RUNTIME, source=source)
    incoming = (OUT/(COPIED+'.xlsx')).read_bytes()
    if digest(incoming) != COPIED: raise ValueError('Copied snapshot changed')
    with closing(open_audit(service.RUNTIME)) as db:
        rows = [db.execute('SELECT * FROM edits WHERE operation=? AND status=\'succeeded\'',(op,)).fetchone()
                for op in OPERATIONS]
        batches = [(row['operation'],json.loads(row['result'])['records']) for row in rows]
        candidate, changed, positions = combine_report_batches(
            snapshot(service.RUNTIME,BASE),snapshot(service.RUNTIME,HEAD),incoming,batches,upstream_copy=True)
    target = OUT/'recovered-candidate.xlsx'
    target.write_bytes(candidate)
    counts = {date:scan(candidate,date)['rows_matched']
              for date in ('1405/08/17','1405/08/18','1405/08/19','1405/08/20')}
    expected = {'1405/08/17':23,'1405/08/18':25,'1405/08/19':19,'1405/08/20':17}
    if counts != expected: raise ValueError(('Unexpected complete report counts', counts))
    evidence = dict(copied_sha256=COPIED,prior_head_sha256=HEAD,upstream_base_sha256=BASE,
                    candidate_sha256=digest(candidate),candidate=str(target),
                    protected_operations=OPERATIONS,changed_sheets=changed,report_positions=positions,
                    restored_rows=sum(len(records) for _,records in batches),counts=counts)
    (OUT/'prepared.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),'utf8')
    return evidence


def activate():
    evidence = json.loads((OUT/'prepared.json').read_text('utf8'))
    native = json.loads((OUT/'native-excel.json').read_text('utf-8-sig'))
    if (native['read_only'] or native['sheets'] != 63 or native['repair_mode']
            or native['sha256'].lower() != evidence['candidate_sha256']):
        raise ValueError('Native read/write Excel acceptance required')
    candidate = Path(evidence['candidate']).read_bytes()
    if digest(candidate) != evidence['candidate_sha256']: raise ValueError('Candidate changed')
    source = Path(json.loads(service.CONFIG.read_text('utf8'))['source']).resolve(strict=True)
    # Same lock as the installed two-minute job; do not race an older worker.
    spec = importlib.util.spec_from_file_location('mine_sync',ROOT/'integrations/hermes/mine_sync/mine_file_sync.py')
    sync = importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)
    lock = sync.acquire_lock(sync.HERMES_HOME/'state/mine-file-sync.lock')
    if lock is None: raise ValueError('Sync currently running; retry activation after it finishes')
    try:
        with sync.readonly_source(sync.SOURCE/source.name) as reader:
            if digest(reader.read()) != COPIED: raise ValueError('Share changed; prepare current source first')
        with writer_lock(service.RUNTIME), closing(open_audit(service.RUNTIME)) as db, source_reader(source) as reader:
            original = reader.read()
            head = db.execute('SELECT after_hash FROM edits ORDER BY rowid DESC LIMIT 1').fetchone()[0]
            if digest(original) != COPIED or head != HEAD: raise ValueError('Production changed; stop')
            backup = sqlite3.connect(OUT/'maintenance-activation-before.sqlite3')
            db.backup(backup);backup.close()
            # Record the already-completed owner copy as a separate historical
            # transition. Insert it atomically with the prepared recovery below.
            operation = uuid.uuid4().hex
            request = dict(_kind=KIND,operation=operation,external_copy=True,
                           decision='Owner copied Share on 2026-11-12; retain all three UUID report batches')
            result = dict(status='imported',mine_hash=COPIED,hash=COPIED,
                          protected_operations=OPERATIONS,external_copy=True)
            fingerprint = digest(json.dumps(['project-owner',str(source),request],sort_keys=True,ensure_ascii=False).encode())
            db.execute('INSERT INTO edits VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (operation,fingerprint,'project-owner',str(source),json.dumps(request,ensure_ascii=False),
                 HEAD,COPIED,str(service.RUNTIME/'backups'/(HEAD+'.xlsx')),'succeeded',
                 json.dumps(result,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
            result = dict(status='merged',mine_hash=COPIED,hash=digest(candidate),
                          protected_operations=OPERATIONS,report_positions=evidence['report_positions'],
                          restored_rows=18,owner_copy_evidence=str(OUT/'before.json'))
            published = publish_candidate(source,original,candidate,db,'mine-file-sync',
                dict(_kind=KIND,operation=uuid.uuid4().hex),result,runtime=service.RUNTIME)
        # Enable the reviewed copy policy only after the complete source exists.
        settings = json.loads(service.CONFIG.read_text('utf8'))
        settings['sync_policy'] = 'upstream_copy'
        staged_config = service.CONFIG.with_suffix('.sync-repair.tmp')
        staged_config.write_text(json.dumps(settings,ensure_ascii=False,indent=2)+'\n','utf8')
        os.replace(staged_config,service.CONFIG)
        installed = sync.HERMES_HOME/'scripts/mine_file_sync.py'
        (OUT/'mine_file_sync.before.py').write_bytes(installed.read_bytes())
        staged_script = installed.with_suffix('.sync-repair.tmp')
        staged_script.write_bytes((ROOT/'integrations/hermes/mine_sync/mine_file_sync.py').read_bytes())
        os.replace(staged_script,installed)
        source_proof(digest(source.read_bytes()))
        (OUT/'activated.json').write_text(json.dumps(published,ensure_ascii=False,indent=2),'utf8')
        return published
    finally:
        sync.release_lock(lock)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate',action='store_true')
    args=parser.parse_args()
    print(json.dumps(activate() if args.activate else prepare(),ensure_ascii=False))
