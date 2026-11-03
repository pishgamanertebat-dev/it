"""Narrow performance-file access and independent PDF/reminder subscriptions."""
from contextlib import closing
import sqlite3

from .store import now, MECHANICAL_STAFF

READ = 'reports.machinery_performance.read'
CAPABILITY = 'reports.machinery_performance.daily_receive'
REMINDER_CAPABILITY = 'reports.machinery_performance.completion_remind'
REPORT_USERS = ('397185913', '641220453')  # Emami, Hedayati; identities rechecked at dispatch.
REMINDER_USER = '641220453'
FILE = 'عملکرد ماشین آلات.xlsx'
POLICIES = (
    ('machinery_performance_daily_recipient', CAPABILITY, REPORT_USERS, 'گیرنده عملکرد ماشین آلات'),
    ('machinery_performance_completion_recipient', REMINDER_CAPABILITY, (REMINDER_USER,), 'مسئول تکمیل عملکرد ماشین آلات'),
)


def migrate(store, backup_directory):
    backup = store.backup(backup_directory)
    with closing(store._connect(write=True)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        store._version(conn)
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise sqlite3.DatabaseError('Authorization integrity failed')
        pending = []
        for role, capability, users, title in POLICIES:
            conn.execute('INSERT OR IGNORE INTO auth_roles VALUES (?,?)', (role, title))
            conn.execute('INSERT OR IGNORE INTO auth_capabilities VALUES (?,?)', (capability, 'reports.machinery_performance.daily'))
            conn.execute('INSERT OR IGNORE INTO auth_role_capabilities VALUES (?,?)', (role, capability))
            holders = {r[0] for r in conn.execute("SELECT user_id FROM auth_user_roles WHERE platform='bale' AND role=? AND active=1", (role,))}
            if (conn.execute('SELECT capability FROM auth_role_capabilities WHERE role=?', (role,)).fetchall() != [(capability,)]
                    or conn.execute('SELECT role FROM auth_role_capabilities WHERE capability=?', (capability,)).fetchall() != [(role,)]
                    or conn.execute('SELECT resource FROM auth_capabilities WHERE capability=?', (capability,)).fetchall() != [('reports.machinery_performance.daily',)]
                    or not holders.issubset(users)
                    or conn.execute('SELECT 1 FROM auth_role_profiles WHERE role=?', (role,)).fetchone()):
                raise ValueError('Conflicting performance subscription policy')
            for user in users:
                if conn.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?", (user,)).fetchone() != (user, 'approved'):
                    raise ValueError('Approved private identity required')
                if user not in holders:
                    pending.append((user, role))
        if not conn.execute('SELECT 1 FROM auth_roles WHERE role=?', (MECHANICAL_STAFF,)).fetchone():
            raise ValueError('Existing maintenance role required')
        resource = 'E:\\Function\\' + FILE
        conn.execute('INSERT OR IGNORE INTO auth_capabilities VALUES (?,?)', (READ, resource))
        if conn.execute('SELECT resource FROM auth_capabilities WHERE capability=?', (READ,)).fetchall() != [(resource,)]:
            raise ValueError('Conflicting performance file resource')
        for existing_role in (MECHANICAL_STAFF, 'net_staff'):
            if conn.execute('SELECT 1 FROM auth_roles WHERE role=?', (existing_role,)).fetchone():
                conn.execute('INSERT OR IGNORE INTO auth_role_capabilities VALUES (?,?)', (existing_role, READ))
        store._assign_roles(conn, pending, actor='owner-machinery-performance-20261103')
        conn.execute('CREATE TABLE IF NOT EXISTS auth_extensions (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
        conn.execute('INSERT OR IGNORE INTO auth_extensions VALUES (?,?)', ('machinery_performance_v1', now()))
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise sqlite3.DatabaseError('Authorization integrity failed')
    return backup
