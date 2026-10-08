"""Receive-only subscription for report delivery failure alerts.

The authorization model has no direct user-capability grant. This role exists
only to carry ``reports.delivery_failure_receive``. It has no profile, so it
does not change routing, and its resource is not a file or read scope.
"""
from contextlib import closing
import sqlite3

from .store import now

ROLE = 'report_delivery_exception_recipient'
CAPABILITY = 'reports.delivery_failure_receive'
RESOURCE = 'reports.delivery_failure'
EXTENSION = 'delivery_failure_alert_v1'


def migrate(store, backup_directory, *, assignments=(), actor='delivery-failure-alert'):
    """Additive capability subscription. Does not alter existing grants or profiles."""
    backup = store.backup(backup_directory)
    with closing(store._connect(write=True)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        store._version(conn)
        if (conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]
                or conn.execute('PRAGMA foreign_key_check').fetchall()):
            raise sqlite3.DatabaseError('Authorization integrity failed')
        conn.execute('INSERT OR IGNORE INTO auth_roles VALUES (?,?)',
                     (ROLE, 'گیرنده هشدار خطای ارسال گزارش'))
        conn.execute('INSERT OR IGNORE INTO auth_capabilities VALUES (?,?)', (CAPABILITY, RESOURCE))
        conn.execute('INSERT OR IGNORE INTO auth_role_capabilities VALUES (?,?)', (ROLE, CAPABILITY))
        if (conn.execute('SELECT resource FROM auth_capabilities WHERE capability=?', (CAPABILITY,)).fetchall()
                != [(RESOURCE,)]
                or conn.execute('SELECT capability FROM auth_role_capabilities WHERE role=?', (ROLE,)).fetchall()
                != [(CAPABILITY,)]
                or conn.execute('SELECT role FROM auth_role_capabilities WHERE capability=?', (CAPABILITY,)).fetchall()
                != [(ROLE,)]):
            raise ValueError('Conflicting delivery-alert capability')
        profile_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='auth_role_profiles'").fetchone()
        if profile_table and conn.execute(
                'SELECT 1 FROM auth_role_profiles WHERE role=?', (ROLE,)).fetchone():
            raise ValueError('Delivery-alert role must not map to a profile')
        pending = []
        for user in sorted(set(map(str, assignments))):
            if conn.execute(
                    "SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?",
                    (user,)).fetchone() != (user, 'approved'):
                raise ValueError('Approved private identity required')
            if conn.execute(
                    "SELECT active FROM auth_user_roles WHERE platform='bale' AND user_id=? AND role=?",
                    (user, ROLE)).fetchone() != (1,):
                pending.append((user, ROLE))
        store._assign_roles(conn, pending, actor=actor)
        conn.execute('CREATE TABLE IF NOT EXISTS auth_extensions (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
        conn.execute('INSERT OR IGNORE INTO auth_extensions VALUES (?,?)', (EXTENSION, now()))
        if (conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]
                or conn.execute('PRAGMA foreign_key_check').fetchall()):
            raise sqlite3.DatabaseError('Authorization integrity failed')
    return backup
