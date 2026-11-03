"""Receive-only oil balance grants; no workbook access or profile changes."""
from contextlib import closing
import sqlite3

from .store import now

CAPABILITY = 'reports.oil_balance.daily_receive'
REMINDER_CAPABILITY = 'reports.oil_balance.completion_remind'
REPORT_USER = '397185913'
REMINDER_USER = '641220453'
POLICIES = (
    ('oil_balance_daily_recipient', CAPABILITY, REPORT_USER, 'گیرنده گزارش موازنه روغن'),
    ('oil_balance_completion_recipient', REMINDER_CAPABILITY, REMINDER_USER, 'مسئول تکمیل موازنه روغن'),
)


def migrate(store, backup_directory):
    backup = store.backup(backup_directory)
    with closing(store._connect(write=True)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        store._version(conn)
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise sqlite3.DatabaseError('Authorization integrity failed')
        pending = []
        for role, capability, user, title in POLICIES:
            conn.execute('INSERT OR IGNORE INTO auth_roles VALUES (?,?)', (role, title))
            conn.execute('INSERT OR IGNORE INTO auth_capabilities VALUES (?,?)', (capability, 'reports.oil_balance.daily'))
            conn.execute('INSERT OR IGNORE INTO auth_role_capabilities VALUES (?,?)', (role, capability))
            if (conn.execute('SELECT capability FROM auth_role_capabilities WHERE role=?', (role,)).fetchall() != [(capability,)]
                    or conn.execute('SELECT role FROM auth_role_capabilities WHERE capability=?', (capability,)).fetchall() != [(role,)]
                    or conn.execute('SELECT resource FROM auth_capabilities WHERE capability=?', (capability,)).fetchall() != [('reports.oil_balance.daily',)]
                    or conn.execute("SELECT 1 FROM auth_user_roles WHERE platform='bale' AND role=? AND active=1 AND user_id<>?", (role, user)).fetchone()
                    or conn.execute('SELECT 1 FROM auth_role_profiles WHERE role=?', (role,)).fetchone()):
                raise ValueError('Conflicting oil balance receive-only policy')
            if conn.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?", (user,)).fetchone() != (user, 'approved'):
                raise ValueError('Approved private identity required')
            if conn.execute("SELECT active FROM auth_user_roles WHERE platform='bale' AND user_id=? AND role=?", (user, role)).fetchone() != (1,):
                pending.append((user, role))
        store._assign_roles(conn, pending, actor='owner-oil-balance-20261103')
        conn.execute('CREATE TABLE IF NOT EXISTS auth_extensions (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
        conn.execute('INSERT OR IGNORE INTO auth_extensions VALUES (?,?)', ('oil_balance_daily_v1', now()))
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise sqlite3.DatabaseError('Authorization integrity failed')
    return backup
