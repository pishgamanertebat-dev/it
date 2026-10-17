"""Shared work-order version and delivery invariants in the existing database.

Audit events append to work-order notes, as document reviews already do. No
historical order is selected as a winner or rewritten by schema activation.
"""
import hashlib
import json
import time


LEASE_SECONDS = 600
RELIABLE_TYPES = ('OIL_CHANGE', 'GREASING', 'AIR_FILTER')


def migrate(con):
    columns = {r[1] for r in con.execute('PRAGMA table_info(service_work_orders)')}
    for name in ('operation_key', 'oil_service_key', 'revision_of', 'superseded_by', 'reissue_reason'):
        if name not in columns:
            con.execute(f'ALTER TABLE service_work_orders ADD COLUMN {name} TEXT')
    con.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_work_order_operation ON service_work_orders(operation_key) WHERE operation_key IS NOT NULL')
    con.execute('CREATE INDEX IF NOT EXISTS idx_oil_service_key ON service_work_orders(oil_service_key)')
    # Extend the dispatch infrastructure rather than introduce another queue.
    from .staff_dispatch import create_schema
    create_schema(con)
    columns = {r[1] for r in con.execute('PRAGMA table_info(service_work_order_dispatch)')}
    for name, kind in (('attempt_id', 'INTEGER'), ('claimed_at', 'REAL'), ('transfer_started_at', 'REAL'), ('updated_at', 'REAL')):
        if name not in columns:
            con.execute(f'ALTER TABLE service_work_order_dispatch ADD COLUMN {name} {kind}')
    con.execute('''CREATE TABLE IF NOT EXISTS service_work_order_delivery_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        work_order_no TEXT NOT NULL REFERENCES service_work_orders(work_order_no),
        state TEXT NOT NULL, claimed_at REAL NOT NULL, transfer_started_at REAL,
        finished_at REAL, error TEXT, decision TEXT, decided_by TEXT,
        decided_at REAL, decision_reason TEXT, artifact_path TEXT)''')


def audit(con, number, event, **details):
    record = json.dumps({'event': event, 'at': time.time(), **details}, ensure_ascii=False)
    con.execute("""UPDATE service_work_orders SET notes=COALESCE(notes||char(10),'')||?,
        updated_at=CURRENT_TIMESTAMP WHERE work_order_no=?""", ('OIL_WORK_ORDER_AUDIT: ' + record, number))


def current(con, number):
    row = con.execute('SELECT * FROM service_work_orders WHERE work_order_no=?', (number,)).fetchone()
    if not row:
        raise ValueError('حکم پیدا نشد.')
    if row['work_order_type'] in RELIABLE_TYPES and 'superseded_by' in row.keys() and row['superseded_by']:
        raise ValueError('این نسخه منسوخ است؛ نسخهٔ معتبر: ' + row['superseded_by'])
    return row


def creation(con, date, items, owner, revision_of=None, reissue_reason=None, operation_id=None,
             work_order_type='OIL_CHANGE', shift=None):
    greasing = work_order_type == 'GREASING'
    air_filter = work_order_type == 'AIR_FILTER'
    multiple = greasing or air_filter
    if not multiple and len(items) != 1:
        raise ValueError('هر حکم تعویض روغن باید فقط یک دستگاه داشته باشد.')
    code = items[0]['machine_code'].upper()
    codes = sorted(i['machine_code'] for i in items)
    service_key = (f'AIR_FILTER:{date}:{shift}:' + json.dumps(codes) if air_filter else
                   f'GREASING:{date}:' + json.dumps(codes) if greasing else f'OIL_CHANGE:{date}:{code}')
    reason = str(reissue_reason or '').strip()
    if reason and (not revision_of or not operation_id):
        raise ValueError('صدور مجدد به شمارهٔ قبلی، علت و هویت پایدار درخواست نیاز دارد.')
    actions = sorted((i['machine_code'],i['action_code']) for i in items) if multiple else items[0]['action_code']
    payload = [service_key, actions, revision_of, operation_id if reason else None]
    operation_key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    existing = con.execute('SELECT * FROM service_work_orders WHERE operation_key=?', (operation_key,)).fetchone()
    if existing:
        if existing['created_by'] != owner:
            raise ValueError('حکم این سرویس توسط حساب دیگری ساخته شده است.')
        return operation_key, service_key, existing['work_order_no']
    if multiple:
        candidates = con.execute('''SELECT * FROM service_work_orders WHERE work_order_type=?
            AND jalali_date=? AND superseded_by IS NULL AND status NOT IN ('CANCELLED','VOID')''', (work_order_type,date)).fetchall()
        active = []
        for candidate in candidates:
            if air_filter and candidate['shift'] != shift:
                continue
            old_codes = {r[0] for r in con.execute('SELECT machine_code FROM service_work_order_items WHERE work_order_id=?', (candidate['id'],))}
            if old_codes.intersection(codes) or candidate['work_order_no'] == revision_of:
                active.append(candidate)
    else:
        active = con.execute('''SELECT DISTINCT w.* FROM service_work_orders w
        JOIN service_work_order_items i ON i.work_order_id=w.id
        WHERE w.work_order_type='OIL_CHANGE' AND w.jalali_date=? AND UPPER(i.machine_code)=?
        AND w.superseded_by IS NULL AND w.status NOT IN ('CANCELLED','VOID')''', (date, code)).fetchall()
    if revision_of:
        parent = current(con, revision_of)
        if parent['created_by'] != owner or parent['work_order_type'] != work_order_type:
            raise ValueError('اصلاح یا صدور مجدد فقط برای حکم همین نوع و متعلق به خودتان مجاز است.')
        parent_items = con.execute('SELECT machine_code FROM service_work_order_items WHERE work_order_id=?', (parent['id'],)).fetchall()
        if (parent['jalali_date'] != date or (air_filter and parent['shift'] != shift) or
                (not multiple and (len(parent_items) != 1 or parent_items[0][0].upper() != code))):
            raise ValueError('نسخهٔ جدید باید متعلق به همان سرویس و دستگاه باشد.')
        if len(active) != 1 or active[0]['work_order_no'] != revision_of:
            raise ValueError('چند حکم قبلی برای این سرویس وجود دارد؛ ابتدا تصمیم مسئول NET لازم است.')
        dispatch = con.execute('SELECT state FROM service_work_order_dispatch WHERE work_order_no=?', (revision_of,)).fetchone()
        if dispatch and dispatch['state'] in {'SENDING', 'UNCERTAIN'}:
            raise ValueError('ابتدا نتیجهٔ ارسال نسخهٔ قبلی را تعیین تکلیف کنید.')
        if parent['status'] == 'SENT' and not reason:
            raise ValueError('حکم ارسال‌شده فقط با صدور مجدد عمدی و ثبت علت قابل جایگزینی است.')
    elif active:
        if len(active) == 1 and active[0]['created_by'] == owner:
            old_items = con.execute('SELECT machine_code,action_code FROM service_work_order_items WHERE work_order_id=?', (active[0]['id'],)).fetchall()
            if (multiple and sorted(tuple(r) for r in old_items) == actions) or (not multiple and len(old_items) == 1 and old_items[0][1] == items[0]['action_code']):
                return operation_key, service_key, active[0]['work_order_no']
        numbers = '، '.join(r['work_order_no'] for r in active)
        raise ValueError('برای این سرویس حکم موجود است: ' + numbers + '؛ از اصلاح همان حکم یا صدور مجدد با علت استفاده کنید.')
    return operation_key, service_key, None


def recover_stale(con, now=None):
    now = time.time() if now is None else now
    rows = con.execute('''SELECT d.* FROM service_work_order_dispatch d
        JOIN service_work_orders w USING(work_order_no)
        WHERE w.work_order_type IN ('OIL_CHANGE','GREASING','AIR_FILTER') AND d.state='SENDING'
        AND COALESCE(d.transfer_started_at,d.claimed_at,CAST(strftime('%s',w.updated_at) AS REAL),0) <= ?''', (now - LEASE_SECONDS,)).fetchall()
    for row in rows:
        attempt = row['attempt_id']
        if attempt is None:
            attempt = con.execute('''INSERT INTO service_work_order_delivery_attempts
                (work_order_no,state,claimed_at,error) VALUES (?,'UNCERTAIN',?,'legacy_sending')''',
                (row['work_order_no'], now)).lastrowid
        con.execute("UPDATE service_work_order_dispatch SET state='UNCERTAIN',attempt_id=?,updated_at=? WHERE work_order_no=?", (attempt, now, row['work_order_no']))
        con.execute("UPDATE service_work_order_delivery_attempts SET state='UNCERTAIN',finished_at=?,error='abandoned_sending' WHERE id=?", (now, attempt))
        audit(con, row['work_order_no'], 'ABANDONED_SENDING', attempt_id=attempt)
    return len(rows)


def sweep():
    """Called by the existing minute scheduler; never sends or retries a file."""
    from .db import connect_db
    con = connect_db()
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='service_work_order_delivery_attempts'").fetchone():
            return 0
        with con:
            con.execute('BEGIN IMMEDIATE')
            return recover_stale(con)
    finally:
        con.close()


def claim(con, number):
    row = current(con, number)
    recover_stale(con)
    if row['status'] != 'APPROVED':
        return None
    if not con.execute('SELECT 1 FROM service_work_order_dispatch WHERE work_order_no=?', (number,)).fetchone():
        staff = con.execute('SELECT bale_id FROM service_staff WHERE id=?', (row['assigned_staff_id'],)).fetchone()
        if not staff or not staff[0]:
            raise ValueError('گیرندهٔ معتبر برای حکم ثبت نشده است.')
        con.execute('INSERT INTO service_work_order_dispatch(work_order_no,manager_chat_id,recipient_id) VALUES (?,?,?)', (number, (row['created_by'] or '').removeprefix('bale:'), staff[0]))
    dispatch = con.execute('SELECT * FROM service_work_order_dispatch WHERE work_order_no=?', (number,)).fetchone()
    if dispatch['state'] not in {'READY','FAILED'}:
        return None
    now = time.time()
    attempt = con.execute("INSERT INTO service_work_order_delivery_attempts(work_order_no,state,claimed_at) VALUES (?,'CLAIMED',?)", (number, now)).lastrowid
    con.execute("UPDATE service_work_order_dispatch SET state='SENDING',attempt_id=?,claimed_at=?,transfer_started_at=NULL,updated_at=? WHERE work_order_no=?", (attempt, now, now, number))
    con.execute('UPDATE service_work_orders SET send_attempts=send_attempts+1 WHERE work_order_no=?', (number,))
    audit(con, number, 'SEND_CLAIMED', attempt_id=attempt)
    return attempt


def start_transfer(con, number, attempt, artifact_path):
    if type(attempt) is not int or attempt <= 0:
        raise ValueError('این تلاش ارسال معتبر نیست؛ وضعیت ارسال را بررسی کنید.')
    current(con, number)
    now = time.time()
    updated = con.execute("""UPDATE service_work_order_dispatch SET transfer_started_at=?,updated_at=?
        WHERE work_order_no=? AND state='SENDING' AND attempt_id=? AND transfer_started_at IS NULL""", (now, now, number, attempt)).rowcount
    if updated != 1:
        raise ValueError('این تلاش ارسال دیگر معتبر نیست؛ وضعیت ارسال را بررسی کنید.')
    con.execute("UPDATE service_work_order_delivery_attempts SET state='TRANSFERRING',transfer_started_at=?,artifact_path=? WHERE id=?", (now, str(artifact_path), attempt))
    audit(con, number, 'TRANSFER_STARTED', attempt_id=attempt)


def complete(con, number, attempt, *, error=None):
    if type(attempt) is not int or attempt <= 0:
        return False
    dispatch = con.execute('SELECT * FROM service_work_order_dispatch WHERE work_order_no=?', (number,)).fetchone()
    if not dispatch or dispatch['attempt_id'] != attempt or dispatch['state'] != 'SENDING':
        return False  # A late completion cannot undo an operator decision or a new attempt.
    state = 'SENT' if error is None else ('UNCERTAIN' if dispatch['transfer_started_at'] else 'FAILED')
    now = time.time()
    con.execute('UPDATE service_work_order_dispatch SET state=?,updated_at=? WHERE work_order_no=?', (state, now, number))
    con.execute('UPDATE service_work_order_delivery_attempts SET state=?,finished_at=?,error=? WHERE id=?', (state, now, type(error).__name__ if error else None, attempt))
    audit(con, number, state, attempt_id=attempt)
    return state


def authorize_manager(con, actor, chat):
    from . import db
    from .permissions import require_work_order_permission
    require_work_order_permission(actor, db_path=db.DB_PATH, capabilities=('work_orders.read_own','work_orders.send','work_orders.approve'))
    if str(actor) != str(chat):
        raise PermissionError('Private actor/chat mismatch')


def pending(con, actor, chat):
    authorize_manager(con, actor, chat)
    recover_stale(con)
    return [dict(r) for r in con.execute('''SELECT w.work_order_no,d.staff_name,d.recipient_id,d.attempt_id,
        a.claimed_at,a.transfer_started_at,a.error FROM service_work_order_dispatch d
        JOIN service_work_orders w USING(work_order_no)
        JOIN service_work_order_delivery_attempts a ON a.id=d.attempt_id
        WHERE w.work_order_type IN ('OIL_CHANGE','GREASING','AIR_FILTER') AND w.created_by=? AND d.state='UNCERTAIN'
        ORDER BY a.claimed_at''', (f'bale:{actor}',))]


def resolve(con, number, attempt, actor, chat, decision, reason):
    authorize_manager(con, actor, chat)
    if decision not in {'RECEIVED','RESEND_ALLOWED'} or not str(reason).strip():
        raise ValueError('تصمیم صریح و علت یا شاهد بررسی لازم است.')
    row = current(con, number)
    if row['created_by'] != f'bale:{actor}' or row['work_order_type'] not in RELIABLE_TYPES:
        raise ValueError('این حکم متعلق به شما نیست.')
    old = con.execute('SELECT * FROM service_work_order_delivery_attempts WHERE id=? AND work_order_no=?', (attempt, number)).fetchone()
    if old and old['decision'] == decision:
        return 'ALREADY_RESOLVED'
    dispatch = con.execute('SELECT * FROM service_work_order_dispatch WHERE work_order_no=?', (number,)).fetchone()
    if not old or not dispatch or dispatch['attempt_id'] != attempt or dispatch['state'] != 'UNCERTAIN':
        raise ValueError('این تلاش دیگر منتظر تصمیم نیست؛ فهرست بررسی ارسال را دوباره باز کنید.')
    state = 'SENT' if decision == 'RECEIVED' else 'READY'
    now = time.time()
    con.execute('UPDATE service_work_order_dispatch SET state=?,updated_at=? WHERE work_order_no=?', (state, now, number))
    con.execute('''UPDATE service_work_order_delivery_attempts SET state=?,decision=?,decided_by=?,decided_at=?,decision_reason=? WHERE id=?''', ('RESOLVED_' + decision, decision, str(actor), now, reason.strip(), attempt))
    if decision == 'RECEIVED':
        con.execute("""UPDATE service_work_orders SET status='SENT',sent_at=COALESCE(sent_at,CURRENT_TIMESTAMP),
            pdf_path=COALESCE(?,pdf_path),last_send_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE work_order_no=?""", (old['artifact_path'], number))
    audit(con, number, 'DELIVERY_DECISION', actor=str(actor), attempt_id=attempt, decision=decision, reason=reason.strip())
    return state
