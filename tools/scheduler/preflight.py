"""Generate reports one hour early without uploading or warning about missing-day data."""
from datetime import timedelta, timezone
import logging
from pathlib import Path
import sqlite3
import tempfile

from .report_errors import ReportFailure, classify, public_reason

logger=logging.getLogger(__name__)


def validate_config(raw):
    if raw is None:return ()
    if not isinstance(raw,dict) or set(raw)-{'enabled','hours'}:
        raise ValueError('Invalid preflight configuration')
    if type(raw.get('enabled',True)) is not bool:raise ValueError('preflight.enabled must be boolean')
    hours=raw.get('hours',[8,11])
    if (not isinstance(hours,list) or not hours
            or any(type(h)is not int or not 0<=h<=23 for h in hours) or len(set(hours))!=len(hours)):
        raise ValueError('preflight.hours must be distinct hours')
    return tuple(hours) if raw.get('enabled',True) else ()


def probe(job,target,directory):
    """An older sample may exercise layout; its age alone is never an error."""
    task=job['task']
    if task=='maintenance_daily_report':
        from tools.fleet.maintenance_daily import report as m
        try:
            with m.source_snapshot() as (data,_):
                report=m.scan(data,target)
                if not report['devices']:
                    latest=max(report['available_dates'],default=None)
                    if not latest:return {'status':'no_data'}
                    report=m.scan(data,latest)
        except Exception as exc:raise classify(exc,'source') from exc
        m.render(report,Path(directory)/'maintenance-preflight.pdf')
        return {'status':'passed','sample_date':report['date']}
    if task in {'driver_daily','mechanical_driver_daily','metalwork_driver_daily'}:
        from integrations.hermes.function_domain.driver_report import load_driver,build_driver_pdf
        try:
            report=load_driver(target)
            sample=target if report.get('sheet') else report.get('latest_date')
            if not sample:return {'status':'no_data'}
        except Exception as exc:raise classify(exc,'source') from exc
        try:build_driver_pdf(sample,directory,missing_result=True)
        except ReportFailure:raise
        except Exception as exc:
            if isinstance(exc,OSError):raise classify(exc,'source') from exc
            raise ReportFailure('pdf_export',stage='export') from exc
        return {'status':'passed','sample_date':sample}
    from tools.fleet.overflow.report import load_report,render_report,ReportError
    try:
        try:report=load_report(target)
        except ReportError as exc:
            if exc.reason!='date_missing':raise
            try:report=load_report()
            except ReportError as fallback:
                if fallback.reason=='date_missing':return {'status':'no_data'}
                raise
    except Exception as exc:raise classify(exc,'source') from exc
    try:render_report(report,directory)
    except Exception as exc:raise ReportFailure('pdf_layout',stage='layout') from exc
    return {'status':'passed','sample_date':report['date']}


def _connection_probe():
    from .tasks import BaleSender
    sender=BaleSender()
    try:sender.check_connection()
    finally:sender.close()


def _claim(conn,key,now):
    from .delivery_alert import _iso,_parse
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM report_preflight_runs WHERE schedule_id=? AND due=?',key).fetchone()
        if row:
            if row['status'] in {'passed','no_data'}:return False
            if row['status']=='running' and now.astimezone(timezone.utc)<_parse(row['checked'])+timedelta(minutes=15):return False
            if row['next_attempt_at'] and now.astimezone(timezone.utc)<_parse(row['next_attempt_at']):return False
        conn.execute('''INSERT INTO report_preflight_runs(schedule_id,due,status,checked)
            VALUES (?,?,'running',?) ON CONFLICT(schedule_id,due) DO UPDATE SET status='running',checked=excluded.checked''',
            (*key,_iso(now)))
    return True


def _alert(conn,job,due,code,detail,now,store,sender_factory):
    from .delivery_alert import (_operators,_open_case,_insert_case,_dispatch,_title,_target,_clock)
    key='preflight_'+job['id']
    text=(f'⚠️ بررسی پیش از موعد — {_title(job)}\n'
          f'تاریخ گزارش: {_target(due)} | موعد ارسال: {_clock(due)}\n'
          f'علت: {public_reason(code,detail) or "خطای تولید گزارش"}\n'
          'این هشدار مربوط به آماده نبودن داده‌های روز هدف نیست.')
    case=_open_case(conn,key,due)
    fingerprint=code+'|'+(detail or '')
    if case is None or case['fingerprint']!=fingerprint:
        case=_insert_case(conn,key,due,fingerprint,text,now)
    _dispatch(conn,case,'exception',case['exception_text'],_operators(store),now,sender_factory)


def run_preflights(conn,jobs,now,store,*,checker=None,sender_factory=None,connection_probe=None):
    from .delivery_alert import ensure_schema,_default_sender,_iso
    from .misfire import due_storage_key
    from .tasks import ROOT,overflow_report_date,TransportUnavailable,TransportRejected
    from .no_data import current_recipients
    conn.row_factory=sqlite3.Row
    selected=[]
    for job in jobs:
        if not job.get('enabled',True) or now.hour not in job.get('preflight_hours',()):continue
        due=job['trigger'].get_next_fire_time(None,now.replace(hour=0,minute=0,second=0,microsecond=0))
        if due and due-timedelta(hours=1)<=now<due and due.date()==now.date():selected.append((job,due))
    if not selected:return
    ensure_schema(conn)
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS report_preflight_runs (
            schedule_id TEXT NOT NULL,due TEXT NOT NULL,status TEXT NOT NULL,checked TEXT NOT NULL,
            error_code TEXT,error_detail TEXT,next_attempt_at TEXT,sample_date TEXT,
            PRIMARY KEY(schedule_id,due))''')
    checker=checker or probe
    factory=sender_factory or _default_sender
    connection_probe=connection_probe or _connection_probe
    cache={}
    for job,due in selected:
        if not current_recipients(job['task'],store):continue
        key=(job['id'],due_storage_key(due))
        previous=conn.execute('SELECT * FROM report_preflight_runs WHERE schedule_id=? AND due=?',key).fetchone()
        # Retry a failed operator notification even between generation retries.
        if previous and previous['error_code']:
            _alert(conn,job,key[1],previous['error_code'],previous['error_detail'],now,store,factory)
        if not _claim(conn,key,now):continue
        task=job['task'];family=('driver' if 'driver_daily' in task else 'overflow' if 'overflow' in task else task)
        target=overflow_report_date(due)
        cachekey=(family,target)
        if cachekey not in cache:
            try:
                if 'connection' not in cache:
                    connection_probe();cache['connection']=True
                runtime=ROOT/'runtime/scheduler';runtime.mkdir(parents=True,exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='preflight-',dir=runtime) as directory:
                    result=checker(job,target,directory)
                cache[cachekey]=(result,None,None)
            except (TransportUnavailable,TransportRejected) as exc:
                cache[cachekey]=({'status':'failed'},type(exc).__name__,None)
            except Exception as exc:
                failure=classify(exc)
                cache[cachekey]=({'status':'failed'},failure.code,failure.serialized())
        result,code,detail=cache[cachekey]
        with conn:
            conn.execute('''UPDATE report_preflight_runs SET status=?,checked=?,error_code=?,error_detail=?,
                next_attempt_at=?,sample_date=? WHERE schedule_id=? AND due=?''',
                (result['status'],_iso(now),code,detail,_iso(now+timedelta(minutes=5)) if code else None,
                 result.get('sample_date'),*key))
        logger.info('Report preflight schedule=%s due=%s status=%s error=%s',*key,result['status'],code)
        if code:_alert(conn,job,key[1],code,detail,now,store,factory)
