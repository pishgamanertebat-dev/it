"""Receive-only additive authorization extension; deliberately no profile mapping."""
from contextlib import closing
import sqlite3
from tools.authorization.store import now
ROLE='maintenance_daily_report_recipient'
CAPABILITY='reports.maintenance.daily_receive'
RESOURCE='reports.maintenance.daily'
EXTENSION='maintenance_daily_report_v1'

def migrate(store, backup_directory, *, assignments=(), actor='maintenance-daily-migration'):
    backup=store.backup(backup_directory)
    with closing(store._connect(write=True)) as c,c:
        c.execute('BEGIN IMMEDIATE');store._version(c)
        if c.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or c.execute('PRAGMA foreign_key_check').fetchall():
            raise sqlite3.DatabaseError('Authorization integrity failed')
        c.execute('INSERT OR IGNORE INTO auth_roles VALUES (?,?)',(ROLE,'گیرنده گزارش روزانه تعمیرات'))
        c.execute('INSERT OR IGNORE INTO auth_capabilities VALUES (?,?)',(CAPABILITY,RESOURCE))
        c.execute('INSERT OR IGNORE INTO auth_role_capabilities VALUES (?,?)',(ROLE,CAPABILITY))
        if (c.execute('SELECT resource FROM auth_capabilities WHERE capability=?',(CAPABILITY,)).fetchall()!=[(RESOURCE,)]
            or c.execute('SELECT capability FROM auth_role_capabilities WHERE role=?',(ROLE,)).fetchall()!=[(CAPABILITY,)]
            or c.execute('SELECT role FROM auth_role_capabilities WHERE capability=?',(CAPABILITY,)).fetchall()!=[(ROLE,)]
            or c.execute('SELECT 1 FROM auth_role_profiles WHERE role=?',(ROLE,)).fetchone()):
            raise ValueError('Conflicting receive-only policy')
        pending=[]
        for user in sorted(set(map(str,assignments))):
            if c.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?",(user,)).fetchone()!=(user,'approved'):
                raise ValueError('Approved private identity required')
            if c.execute("SELECT active FROM auth_user_roles WHERE platform='bale' AND user_id=? AND role=?",(user,ROLE)).fetchone()!=(1,):pending.append((user,ROLE))
        store._assign_roles(c,pending,actor=actor)
        c.execute('CREATE TABLE IF NOT EXISTS auth_extensions (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
        c.execute('INSERT OR IGNORE INTO auth_extensions VALUES (?,?)',(EXTENSION,now()))
        if c.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or c.execute('PRAGMA foreign_key_check').fetchall():raise sqlite3.DatabaseError('Authorization integrity failed')
    return backup
