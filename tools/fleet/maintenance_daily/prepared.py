"""Pinned maintenance artifacts: source access, missing data and delivery are independent."""
from datetime import datetime, timedelta, timezone
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from . import report as m
from tools.scheduler.report_errors import ReportFailure, classify

ROOT=Path(__file__).resolve().parents[3]
CACHE=ROOT/'runtime/scheduler/maintenance-ready'
SYNC_STATE=Path(os.environ.get('HERMES_HOME',r'C:\Users\win-10\AppData\Local\hermes'))/'state/mine-file-sync.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True).encode('utf8')


def retained(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=data:raise ReportFailure('source_changed',stage='validation')
        return
    with path.open('xb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def source_proof(digest,*,runtime=None,source=None):
    """Verify accepted local version using the existing immutable baseline/journal.

    Read-only: do not repair prepared edits or infer an unjournalled local change.
    """
    from tools.fleet.repairs.maintenance_service import RUNTIME,CONFIG
    from tools.fleet.repairs.maintenance_baseline import baseline,snapshot,check_journal,KIND
    runtime=Path(runtime or RUNTIME)
    source=Path(source or json.loads(CONFIG.read_text('utf8'))['source'])
    try:
        with closing(sqlite3.connect((runtime/'audit.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            db.execute('BEGIN')
            state=baseline(db,source,runtime)
            if state is None:raise ValueError('No approved baseline')
            check_journal(db)
            latest=state['initial_hash'];version=state['event_id']
            for row in db.execute('SELECT rowid,* FROM edits WHERE source=? COLLATE NOCASE AND rowid>? ORDER BY rowid',
                                  (str(source),state['cutoff'])):
                if row['status']=='not_applied':continue
                if row['status']!='succeeded' or row['before_hash']!=latest:
                    raise ValueError('Unverified journal transition')
                request=json.loads(row['request']);result=json.loads(row['result'])
                if (not re.fullmatch('[0-9a-f]{32}',row['operation']) or request.get('operation')!=row['operation'] or
                    sha(encoded([str(row['actor']),str(source),request]))!=row['fingerprint']):
                    # Writer uses json.dumps default separators, identical to encoded().
                    raise ValueError('Invalid journal fingerprint')
                snapshot(runtime,row['before_hash']);snapshot(runtime,row['after_hash'])
                if request.get('_kind')==KIND:
                    snapshot(runtime,result['mine_hash'])
                elif request.get('_kind')=='maintenance_report_batch_v1':
                    from tools.fleet.repairs.maintenance_report import verify_operation
                    verify_operation(request,result)
                elif (result.get('sheet')!=request.get('sheet') or
                      not all(field in request for field in ('canonical','mechanic','description','parts'))):
                    raise ValueError('Unverified writer identity')
                latest=row['after_hash'];version=row['operation']
            if latest!=digest:raise ValueError('Local bytes do not match accepted journal head')
            return dict(source_sha256=digest,journal_version=version,baseline=state['event_id'])
    except Exception as exc:
        raise ReportFailure('source_stale',stage='source') from exc


def confirm_missing_source(digest,*,state_path=SYNC_STATE,now=None):
    """Missing data is assertable only after a recent successful exact-file mine check."""
    now=now or datetime.now(timezone.utc)
    try:
        state=json.loads(Path(state_path).read_text('utf8'))
        run=state['last_run'];guard=run['maintenance_guard']
        checked=datetime.fromisoformat(run['finished_at'])
        if (state.get('outage') or not run.get('source_enumerated')
            or guard.get('status') not in {'unchanged','imported','merged'}
            or guard.get('hash')!=digest or not timedelta(0)<=now-checked<=timedelta(minutes=5)):
            raise ValueError('Exact source freshness unverified')
        return dict(checked_at=checked.isoformat(),source_sha256=digest,state='DATA_NOT_READY')
    except Exception as exc:
        raise ReportFailure('source_stale',stage='source') from exc


def load(target,*,cache=CACHE):
    target=m.exact_date(target);folder=Path(cache)/target.replace('/','-')
    pointer=folder/'ready.json'
    if not pointer.exists():return None
    try:
        record=json.loads(pointer.read_text('utf8'));version=record['manifest_sha256']
        if len(version)!=64 or any(c not in '0123456789abcdef' for c in version):raise ValueError('Invalid version')
        manifest_bytes=(folder/(version+'.json')).read_bytes()
        if sha(manifest_bytes)!=version:raise ValueError('Manifest changed')
        meta=json.loads(manifest_bytes)
        paths={key:folder/(meta[key] + suffix) for key,suffix in
               (('pdf_sha256','.pdf'),('source_sha256','.xlsx'),('report_sha256','.report.json'))}
        for key,path in paths.items():
            if path.parent!=folder or sha(path.read_bytes())!=meta[key]:raise ValueError('Pinned bytes changed')
        report=json.loads(paths['report_sha256'].read_text('utf8'))
        if meta['target_date']!=target or report['date']!=target or report['source_sha256']!=meta['source_sha256']:
            raise ValueError('Pinned target/source mismatch')
        validation=m.validate_pdf(paths['pdf_sha256'],report)
        return dict(status='ready',report=report,pdf=str(paths['pdf_sha256']),validation=validation,manifest=meta)
    except Exception as exc:
        raise ReportFailure('pdf_validation',stage='validation') from exc


def pin(data,result,proof,*,cache=CACHE):
    report=result['report'];target=report['date'];folder=Path(cache)/target.replace('/','-')
    source_hash=sha(data);pdf=Path(result['pdf']).read_bytes();report_bytes=encoded(report)
    if source_hash!=report['source_sha256']:raise ReportFailure('source_changed',stage='source')
    meta=dict(state='ARTIFACT_READY',target_date=target,prepared_at=datetime.now(timezone.utc).isoformat(),
              source_sha256=source_hash,pdf_sha256=sha(pdf),report_sha256=sha(report_bytes),source_version=proof,
              source_change_policy='frozen_publication_version; later versions require explicit correction review')
    retained(folder/(source_hash+'.xlsx'),data)
    retained(folder/(meta['pdf_sha256']+'.pdf'),pdf)
    retained(folder/(meta['report_sha256']+'.report.json'),report_bytes)
    manifest=encoded(meta);version=sha(manifest)
    retained(folder/(version+'.json'),manifest)
    # First complete preparation wins. Never silently replace a publication version.
    retained(folder/'ready.json',encoded(dict(manifest_sha256=version)))
    return load(target,cache=cache)


def prepare(target,directory,*,cache=CACHE,proof_reader=None,freshness_reader=None,reader=None):
    ready=load(target,cache=cache)
    if ready:return ready
    try:
        with m.source_snapshot(reader) as (data,digest):
            proof=(proof_reader or source_proof)(digest)
            report=m.scan(data,target)
    except Exception as exc:raise classify(exc,'source') from exc
    if not report['devices']:
        freshness=(freshness_reader or confirm_missing_source)(digest)
        return dict(status='skipped',reason='no_target_date_records',report=report,source_evidence=freshness)
    path=Path(directory)/('maintenance-daily-'+target.replace('/','-')+'.pdf')
    validation=m.render(report,path)
    return pin(data,dict(status='ready',report=report,pdf=str(path),validation=validation),proof,cache=cache)
