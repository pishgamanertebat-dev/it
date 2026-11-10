"""Durable, deterministic Tehran 17:30 management snapshot; never sends reports."""
from datetime import timedelta
import hashlib
import json
import sqlite3

from .delivery_alert import (TEHRAN, STALE_CLAIM_SECONDS, _default_sender, _iso,
    _parse, _operators, _expected, _delivery_summary, _problems, _target, _title,
    _label, _message_id, occurrence_recipients)
from .misfire import _occurrences, due_storage_key


def ensure_schema(conn):
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS report_correction_review (
            schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
            evidence TEXT NOT NULL, updated TEXT NOT NULL,
            PRIMARY KEY(schedule_id,due,recipient_id,artifact_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS manager_daily_snapshot (
            day TEXT PRIMARY KEY, captured TEXT NOT NULL, sha256 TEXT NOT NULL,
            payload TEXT NOT NULL, messages TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS manager_daily_receipt (
            day TEXT NOT NULL, part INTEGER NOT NULL, operator_id TEXT NOT NULL,
            status TEXT NOT NULL, message_id TEXT, error TEXT, updated TEXT NOT NULL,
            PRIMARY KEY(day,part,operator_id))''')


def snapshot(conn, jobs, now, store):
    """Seven days of completed work plus EVERY unresolved historical obligation."""
    counts = dict(complete=0,waiting=0,error=0,closed=0,uncertain=0,review=0,unverified=0)
    details=[];correction_status=[]
    floor=now-timedelta(days=7)
    for job in jobs:
        if not job.get('enabled',True):continue
        runs={r['due']:dict(r) for r in conn.execute('SELECT * FROM runs WHERE schedule_id=?',(job['id'],))}
        for h in conn.execute("SELECT * FROM report_recovery_hold WHERE schedule_id=? AND status='held'",(job['id'],)):
            runs.setdefault(h['due'],dict(schedule_id=job['id'],due=h['due'],status='not_recorded',error='occurrence_not_recorded',started=None,finished=None))
        trigger=job.get('trigger')
        if trigger and hasattr(trigger,'get_next_fire_time'):
            dues,flooded=_occurrences(trigger,floor,now,10)
            if not flooded:
                by_day={_parse(key).astimezone(trigger.timezone).date():key for key in sorted(runs,reverse=True)}
                for due in dues:
                    key=by_day.get(due.date(),due_storage_key(due))
                    runs.setdefault(key,dict(schedule_id=job['id'],due=key,status='not_recorded',error='occurrence_not_recorded',started=None,finished=None))
        for run in sorted(runs.values(),key=lambda r:r['due']):
            if _parse(run['due'])>now:continue
            expected=occurrence_recipients(conn,job,run,store,now)
            receipts=_delivery_summary(conn,job,run,expected or (),store)
            corrections=[dict(r) for r in conn.execute('SELECT * FROM report_correction_review WHERE schedule_id=? AND due=?', (job['id'],run['due']))]
            open_corrections=[r for r in corrections if r['status'] not in {'sent','resolved'}]
            correction_status.extend(dict(c,title=_title(job),target=_target(run['due'])) for c in corrections
                                     if c['status'] not in {'sent','resolved'} or _parse(c['updated'])>=floor)
            complete=bool(expected) and set(expected)<={r['recipient'] for r in receipts if r['status']=='sent'}
            if complete and not open_corrections:
                if _parse(run['due'])>=floor:counts['complete']+=1
                continue
            problems=_problems(conn,job,run,expected or (),store)
            hold=conn.execute("SELECT * FROM report_recovery_hold WHERE schedule_id=? AND due=? AND status='held'",(job['id'],run['due'])).fetchone()
            if any(r['status'] in {'uncertain','sending'} for r in receipts) or run['status']=='uncertain':state='uncertain'
            elif run['status']=='no_data_final':state='closed'
            elif hold or open_corrections:state='review'
            elif run['status'] in {'succeeded','not_recorded'}:state='unverified'
            elif run.get('error') in {'date_missing','no_target_date_records'} and run['status'] in {'waiting_for_data','retry_wait','skipped'}:state='waiting'
            else:state='error'
            counts[state]+=1
            undelivered=[r for r in receipts if r['status'] not in {'sent','revoked'}]
            details.append(dict(schedule_id=job['id'],title=_title(job),target=_target(run['due']),
                original_due=run['due'],last_attempt=run.get('finished') or run.get('started'),
                state=state,error=run.get('error'),error_detail=run.get('error_detail'),
                recipients=undelivered,problems=problems,corrections=corrections,
                resolver_available=expected is not None))
            details[-1]['recovery_hold']=dict(hold) if hold else None
    return dict(captured_at=_iso(now),counts=counts,unresolved=details,correction_status=correction_status,
                completed_window_days=7,unresolved_history='all')


def format_messages(data):
    from .report_errors import public_reason
    c=data['counts']
    header=('خلاصه وضعیت ارسال گزارش‌ها — '+_parse(data['captured_at']).astimezone(TEHRAN).strftime('%Y-%m-%d %H:%M')+
        '\nکامل در ۷ روز: '+str(c['complete'])+' | منتظر: '+str(c['waiting'])+
        ' | خطادار: '+str(c['error'])+' | بسته بدون ارسال: '+str(c['closed'])+' | نامطمئن: '+str(c['uncertain'])+
        '\nنیازمند تصمیم انسانی: '+str(c.get('review',0))+' | سوابق تحویل ناکافی: '+str(c.get('unverified',0))+
        '\nهمه موارد حل‌نشده قدیمی در این Snapshot باقی می‌مانند.')
    blocks=[header]
    for r in data['unresolved']:
        due=_parse(r['original_due']).astimezone(TEHRAN).strftime('%Y-%m-%d %H:%M')
        last=_parse(r['last_attempt']).astimezone(TEHRAN).strftime('%Y-%m-%d %H:%M') if r['last_attempt'] else 'ثبت نشده'
        names='، '.join(x['label'] for x in r['recipients']) or ('گیرنده تاریخی در رسیدها ثبت نشده؛ بررسی تاریخچه لازم است' if not r['corrections'] else 'اصلاحیه')
        if r.get('recovery_hold'):
            reason='توقف مستقل نوبت تاریخی برای تصمیم مالک؛ علت قبلی: '+str(r['error'] or 'رسید کامل نیست')
            action='بررسی منبع و رسید؛ ارسال فقط پس از تأیید مستقل مالک'
        elif not r['recipients'] and any(c['status'] not in {'sent','resolved'} for c in r['corrections']):
            reason='رسید ارسال اولیه موجود است؛ صحت محتوای نسخه اولیه یا نیاز به اصلاحیه هنوز تعیین تکلیف نشده است'
            action='بررسی فایل دریافت‌شده و تصمیم مالک درباره اصلاحیه'
        elif r['state']=='unverified':
            reason='سوابق گیرنده یا رسید فایل برای تأیید تحویل کافی نیست؛ نبود ارسال نیز اثبات نشده است'
            action='بررسی تاریخچه؛ از ایجاد تعهد برای گیرنده جدید یا بازفرستادن کور پرهیز شود'
        elif r['state']=='waiting':reason='داده همان تاریخ کامل نیست';action='تکمیل منبع تا مهلت ۳۶ ساعت'
        elif r['state']=='closed':reason='بسته بدون ارسال؛ '+str(r['error'] or 'علت ثبت نشده');action='بررسی بازیابی و تأیید مالک؛ بازگشایی خودکار ندارد'
        elif r['state']=='uncertain':reason='نتیجه ارسال نامطمئن';action='بررسی رسید؛ ارسال مجدد خودکار ممنوع'
        else:reason=public_reason(r['error'],r['error_detail']) or str(r['error'] or 'رسید معتبر کامل نیست');action='بررسی خطا و رسید هر فایل'
        text=(f"{r['title']} | داده {r['target']} | {names}\nموعد {due}؛ آخرین تلاش {last}\nعلت: {reason}؛ اقدام: {action}")
        for item in r['corrections']:
            text+=f"\nاصلاحیه {_label_from(item)}: {item['status']}؛ {item['reason']}"
        blocks.append(text)
    if len(blocks)==1:blocks.append('مورد حل‌نشده ثبت نشده است.')
    for item in data.get('correction_status',[]):
        if item['status'] in {'sent','resolved'}:
            blocks.append(f"اصلاحیه {item['title']} | {item['target']} | {_label_from(item)}: {item['status']}؛ {item['reason']}")
    # Bale text limits: every part has a separate durable receipt, same snapshot.
    parts=[];current=''
    for block in blocks:
        for offset in range(0,len(block),3300):
            piece=block[offset:offset+3300]
            if current and len(current)+len(piece)+2>3400:parts.append(current);current=''
            current+=('\n\n' if current else '')+piece
    if current:parts.append(current)
    return [f'خلاصه روزانه ({i+1}/{len(parts)})\n'+s for i,s in enumerate(parts)]


def _label_from(item):
    return item['recipient_id']+'/'+item['artifact_id']


def scan_daily_summary(conn,jobs,now,store,*,sender_factory=None):
    local=now.astimezone(TEHRAN)
    if (local.hour,local.minute)<(17,30):return
    ensure_schema(conn)
    day=local.date().isoformat()
    conn.row_factory=sqlite3.Row
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        saved=conn.execute('SELECT * FROM manager_daily_snapshot WHERE day=?',(day,)).fetchone()
        if not saved:
            data=snapshot(conn,jobs,now,store)
            payload=json.dumps(data,ensure_ascii=False,sort_keys=True)
            messages=json.dumps(format_messages(data),ensure_ascii=False)
            conn.execute('INSERT INTO manager_daily_snapshot VALUES (?,?,?,?,?)',
                (day,_iso(now),hashlib.sha256(payload.encode()).hexdigest(),payload,messages))
            saved=conn.execute('SELECT * FROM manager_daily_snapshot WHERE day=?',(day,)).fetchone()
    messages=json.loads(saved['messages'])
    if (hashlib.sha256(saved['payload'].encode()).hexdigest()!=saved['sha256']
            or messages!=format_messages(json.loads(saved['payload']))):
        raise RuntimeError('Manager summary snapshot integrity failure')
    from .tasks import TransportUnavailable,TransportRejected
    for part,message in enumerate(messages):
        for operator in _operators(store):
            key=(day,part,operator)
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                row=conn.execute('SELECT * FROM manager_daily_receipt WHERE day=? AND part=? AND operator_id=?',key).fetchone()
                if row and row['status'] in {'sent','uncertain'}:continue
                if row and row['status']=='sending':
                    if (now-_parse(row['updated'])).total_seconds()>=STALE_CLAIM_SECONDS:
                        conn.execute("UPDATE manager_daily_receipt SET status='uncertain',error='InterruptedSend',updated=? WHERE day=? AND part=? AND operator_id=?",(_iso(now),*key))
                    continue
                conn.execute('''INSERT INTO manager_daily_receipt VALUES (?,?,?,'sending',NULL,NULL,?)
                    ON CONFLICT(day,part,operator_id) DO UPDATE SET status='sending',error=NULL,updated=excluded.updated''',(*key,_iso(now)))
            sender=None;attempted=False;mid=None;error=None
            try:
                if operator not in _operators(store):
                    state='revoked'
                else:
                    sender=(sender_factory or _default_sender)();attempted=True
                    mid=_message_id(sender.message(operator,message));state='sent' if mid else 'uncertain'
            except (TransportUnavailable,TransportRejected) as exc:state='failed';error=type(exc).__name__
            except Exception as exc:state='uncertain' if attempted else 'failed';error=type(exc).__name__
            finally:
                if sender:
                    try:sender.close()
                    except Exception:pass
            with conn:
                conn.execute('''UPDATE manager_daily_receipt SET status=?,message_id=?,error=?,updated=?
                    WHERE day=? AND part=? AND operator_id=?''',(state,mid,error,_iso(now),*key))
