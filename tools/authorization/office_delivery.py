"""Receive-only office pushes and final NET copies; no profile or domain authority."""
from contextlib import closing
import sqlite3
from .store import DAILY_RECEIVE, DRIVER_RECEIVE, now

OFFICE_ROLE = 'office_daily_report_recipient'
COPY_ROLE = 'work_order_final_copy_recipient'
COPY_CAPABILITY = 'work_orders.final_copy.receive'
COPY_RESOURCE = 'work_orders.final_approved_copy'
OFFICE_TASKS = {'overflow': DAILY_RECEIVE, 'driver_daily': DRIVER_RECEIVE}
ROLE_CAPS = {OFFICE_ROLE: (DAILY_RECEIVE, DRIVER_RECEIVE), COPY_ROLE: (COPY_CAPABILITY,)}


def migrate(store, backup_directory, *, assignments=(), actor='office-delivery-operator'):
    backup = store.backup(backup_directory)
    with closing(store._connect(write=True)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        store._version(c)
        if c.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or c.execute('PRAGMA foreign_key_check').fetchall():
            raise sqlite3.DatabaseError('Authorization integrity failed')
        c.execute('INSERT OR IGNORE INTO auth_capabilities VALUES (?,?)', (COPY_CAPABILITY, COPY_RESOURCE))
        if c.execute('SELECT resource FROM auth_capabilities WHERE capability=?', (COPY_CAPABILITY,)).fetchone() != (COPY_RESOURCE,):
            raise ValueError('Conflicting copy resource')
        for role, caps in ROLE_CAPS.items():
            c.execute('INSERT OR IGNORE INTO auth_roles VALUES (?,?)', (role, role))
            for cap in caps:
                c.execute('INSERT OR IGNORE INTO auth_role_capabilities VALUES (?,?)', (role, cap))
            if ({r[0] for r in c.execute('SELECT capability FROM auth_role_capabilities WHERE role=?', (role,))} != set(caps)
                    or c.execute('SELECT 1 FROM auth_role_profiles WHERE role=?', (role,)).fetchone()):
                raise ValueError('Conflicting receive-only policy')
        pending = []
        for user, role in dict.fromkeys(assignments):
            user = str(user)
            if role not in ROLE_CAPS:
                raise ValueError('Only receive-only roles allowed')
            if c.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?", (user,)).fetchone() != (user, 'approved'):
                raise ValueError('Approved private identity required')
            if c.execute("SELECT active FROM auth_user_roles WHERE platform='bale' AND user_id=? AND role=?", (user, role)).fetchone() != (1,):
                pending.append((user, role))
        store._assign_roles(c, pending, actor=actor)
        c.execute('CREATE TABLE IF NOT EXISTS auth_extensions (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
        c.execute('INSERT OR IGNORE INTO auth_extensions VALUES (?,?)', ('office_final_copy_v1', now()))
        if c.execute('PRAGMA foreign_key_check').fetchall():
            raise sqlite3.DatabaseError('Authorization FK failed')
    return backup
