"""Manager-authorized dispatch and recipient acknowledgement; no role coupling."""
import hashlib
import json
from pathlib import Path

from tools.fleet.work_orders.core.db import connect_db
from tools.fleet.work_orders.core.permissions import require_work_order_permission
from tools.fleet.work_orders.core.assignment import assign_work_order
from tools.fleet.work_orders.core.approval import approve_work_order


def create_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS service_work_order_dispatch (
        work_order_no TEXT PRIMARY KEY REFERENCES service_work_orders(work_order_no),
        manager_chat_id TEXT NOT NULL, recipient_id TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'READY', notified_at TEXT,
        roster_id INTEGER, staff_name TEXT
    )""")
    con.execute('''CREATE TABLE IF NOT EXISTS service_staff_roster (
        id INTEGER PRIMARY KEY, display_name TEXT NOT NULL,
        staff_id INTEGER NOT NULL REFERENCES service_staff(id), active INTEGER NOT NULL DEFAULT 1
    )''')
    columns = {r[1] for r in con.execute('PRAGMA table_info(service_work_order_dispatch)')}
    for name,kind in [('roster_id','INTEGER'),('staff_name','TEXT')]:
        if name not in columns:
            con.execute(f'ALTER TABLE service_work_order_dispatch ADD COLUMN {name} {kind}')


def staff_options():
    con = connect_db()
    try:
        roster = [dict(r) for r in con.execute('''SELECT s.id,r.id AS roster_id,r.display_name,s.bale_id
            FROM service_staff_roster r JOIN service_staff s ON s.id=r.staff_id
            WHERE r.active=1 AND s.active=1 ORDER BY r.id''')]
        if roster:
            return roster
        return [dict(r) for r in con.execute("SELECT id,display_name,bale_id FROM service_staff WHERE active=1 AND notes='BALE_DISPATCH_PILOT' ORDER BY id")]
    finally:
        con.close()


def staff_display_name(staff, work_order_type):
    return staff['display_name']


def staff_menu(work_order_type=None):
    options = staff_options()
    lines = ["سرویسکار را انتخاب کنید:"]
    lines.extend(f"{i}) {staff_display_name(s, work_order_type)}" for i, s in enumerate(options, 1))
    lines.extend(f"{i}) هنوز تعریف نشده (غیرفعال)" for i in range(len(options) + 1, 4))
    return "\n".join(lines), options


def prepare_dispatch(number, actor, chat, staff_id, roster_id=None):
    require_work_order_permission(actor)
    con = connect_db()
    try:
        row = con.execute("SELECT * FROM service_work_orders WHERE work_order_no=?", (number,)).fetchone()
        if not row or row['created_by'] != f'bale:{actor}':
            raise ValueError('این حکم متعلق به شما نیست.')
        staff = next((s for s in staff_options() if s['id'] == staff_id and s.get('roster_id') == roster_id), None)
        if not staff:
            raise ValueError('این سرویسکار فعال نیست.')
        previous = con.execute('SELECT roster_id FROM service_work_order_dispatch WHERE work_order_no=?', (number,)).fetchone()
        if previous and previous['roster_id'] != roster_id:
            raise ValueError('این حکم قبلاً به فرد دیگری تخصیص یافته است.')
        if row['assigned_staff_id'] and row['assigned_staff_id'] != staff_id:
            raise ValueError('این حکم قبلاً به فرد دیگری تخصیص یافته است.')
        fingerprint = hashlib.sha256(Path(row['excel_path']).read_bytes()).hexdigest()
        reviews = []
        for line in (row['notes'] or '').splitlines():
            if line.startswith('MANAGER_DOCUMENT_REVIEW: '):
                reviews.append(json.loads(line.split(': ', 1)[1]))
        if not any(r.get('bale_id') == actor and r.get('sha256') == fingerprint for r in reviews):
            raise ValueError('ابتدا فایل فعلی را بررسی و تایید کنید.')
        if row['status'] == 'FILE_READY':
            assign_work_order(work_order_no=number, staff_id=staff_id)
        if row['status'] in {'FILE_READY', 'ASSIGNED'}:
            approve_work_order(work_order_no=number, approved_by=f'bale:{actor}')
        with con:
            con.execute('INSERT OR IGNORE INTO service_work_order_dispatch(work_order_no,manager_chat_id,recipient_id,roster_id,staff_name) VALUES (?,?,?,?,?)', (number, chat, staff['bale_id'],roster_id,staff['display_name']))
        return {**dict(row), 'staff_name': staff_display_name(staff, row['work_order_type']), 'recipient_id': staff['bale_id']}
    finally:
        con.close()


def claim_send(number):
    con = connect_db()
    try:
        with con:
            return con.execute("UPDATE service_work_order_dispatch SET state='SENDING' WHERE work_order_no=? AND state IN ('READY','FAILED')", (number,)).rowcount == 1
    finally:
        con.close()


def finish_send(number, state):
    con = connect_db()
    try:
        with con:
            con.execute('UPDATE service_work_order_dispatch SET state=? WHERE work_order_no=?', (state, number))
    finally:
        con.close()


def recipient_orders(actor):
    con = connect_db()
    try:
        return [dict(r) for r in con.execute("""SELECT w.*, d.manager_chat_id,d.notified_at,COALESCE(d.staff_name,s.display_name) AS display_name
            FROM service_work_orders w JOIN service_work_order_dispatch d USING(work_order_no)
            JOIN service_staff s ON s.id=w.assigned_staff_id
            WHERE d.recipient_id=? AND s.bale_id=? AND s.active=1 AND w.status='SENT'
            ORDER BY w.id DESC""", (actor, actor))]
    finally:
        con.close()


def is_recipient(actor):
    con = connect_db()
    try:
        return con.execute('SELECT 1 FROM service_staff WHERE bale_id=? AND active=1', (actor,)).fetchone() is not None
    finally:
        con.close()


def acknowledge(number, actor):
    con = connect_db()
    try:
        with con:
            con.execute('BEGIN IMMEDIATE')
            orders = recipient_orders(actor)
            order = next((r for r in orders if r['work_order_no'] == number), None)
            if order is None:
                raise ValueError('این حکم برای شما ارسال نشده است.')
            con.execute('UPDATE service_work_orders SET acknowledged_at=COALESCE(acknowledged_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE work_order_no=?', (number,))
            return order
    finally:
        con.close()


def mark_notified(number):
    con = connect_db()
    try:
        with con:
            con.execute('UPDATE service_work_order_dispatch SET notified_at=CURRENT_TIMESTAMP WHERE work_order_no=?', (number,))
    finally:
        con.close()
