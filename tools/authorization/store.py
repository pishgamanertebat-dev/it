"""Single SQLite authorization API; reads never create/migrate a database."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / 'reports/telegram_usage/telegram_users.db'
OFFICE_SUPERVISOR = 'office_supervisor'
OVERFLOW_READ = 'reports.overflow.read'
DAILY_RECEIVE = 'reports.overflow.daily_receive'
BUSINESS_ADMIN = 'business_admin'
FUNCTION_READ = 'function.read_all'
DRIVER_READ = 'reports.driver_daily.read'
DRIVER_RECEIVE = 'reports.driver_daily.daily_receive'
logger = logging.getLogger(__name__)

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS auth_migrations (
        version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS auth_roles (
        role TEXT PRIMARY KEY, display_name TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS auth_capabilities (
        capability TEXT PRIMARY KEY, resource TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS auth_role_capabilities (
        role TEXT NOT NULL REFERENCES auth_roles(role),
        capability TEXT NOT NULL REFERENCES auth_capabilities(capability),
        PRIMARY KEY(role, capability))''',
    '''CREATE TABLE IF NOT EXISTS auth_user_roles (
        platform TEXT NOT NULL, user_id TEXT NOT NULL,
        role TEXT NOT NULL REFERENCES auth_roles(role),
        active INTEGER NOT NULL CHECK(active IN (0,1)),
        assigned_at TEXT NOT NULL, assigned_by TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(platform,user_id,role),
        FOREIGN KEY(platform,user_id) REFERENCES channel_users(platform,user_id))''',
    '''CREATE TABLE IF NOT EXISTS auth_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
        user_id TEXT NOT NULL, role TEXT NOT NULL, event_type TEXT NOT NULL,
        actor TEXT NOT NULL, created_at TEXT NOT NULL)''',
)


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RecipientResolution:
    status: str
    recipient: str | None = None
    holder_count: int = 0


class AuthorizationStore:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)

    def _connect(self, *, write=False):
        conn = sqlite3.connect(self.path.resolve().as_uri() + ('?mode=rw' if write else '?mode=ro'),
                               uri=True, timeout=5)
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            if not write:
                conn.execute('PRAGMA query_only=ON')
            return conn
        except Exception:
            conn.close()
            raise

    @staticmethod
    def _version(conn):
        if conn.execute('SELECT MAX(version) FROM auth_migrations').fetchone()[0] not in (1, 2):
            raise sqlite3.DatabaseError('Unsupported authorization schema')

    def backup(self, directory=None):
        """Consistent online backup; must succeed before any production mutation."""
        directory = Path(directory or ROOT / 'backup/authorization')
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        target = directory / f'{self.path.stem}-{stamp}-{uuid4().hex}.sqlite3'
        with closing(self._connect()) as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise sqlite3.DatabaseError('Backup integrity check failed')
        return target

    def migrate(self, backup_directory=None):
        backup = self.backup(backup_directory)
        with closing(self._connect(write=True)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('SELECT platform,user_id,registration_status FROM channel_users LIMIT 0')
            for statement in SCHEMA:
                conn.execute(statement)
            version = conn.execute('SELECT MAX(version) FROM auth_migrations').fetchone()[0]
            if version not in (None, 1, 2):
                raise sqlite3.DatabaseError('Unsupported authorization schema')
            if version is None:
                conn.execute('INSERT INTO auth_roles VALUES (?,?)', (OFFICE_SUPERVISOR, 'سرپرست دفتر'))
                for capability in (OVERFLOW_READ, DAILY_RECEIVE):
                    conn.execute('INSERT INTO auth_capabilities VALUES (?,?)', (capability, 'reports.overflow'))
                    conn.execute('INSERT INTO auth_role_capabilities VALUES (?,?)', (OFFICE_SUPERVISOR, capability))
                conn.execute('INSERT INTO auth_migrations VALUES (1,?)', (now(),))
        return backup

    def roles(self, user_id, platform='bale'):
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute('BEGIN')
                self._version(conn)
                return tuple(row[0] for row in conn.execute('''
                    SELECT ur.role FROM auth_user_roles ur
                    JOIN channel_users u USING(platform,user_id)
                    WHERE ur.platform=? AND ur.user_id=? AND ur.active=1
                      AND u.registration_status='approved' ORDER BY ur.role''',
                    (platform, str(user_id))))
        except (sqlite3.Error, OSError):
            logger.error('Authorization roles unavailable; fail closed')
            return ()

    def capabilities_for_role(self, role):
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute('BEGIN')
                self._version(conn)
                return tuple(row[0] for row in conn.execute('''SELECT c.capability
                    FROM auth_roles r JOIN auth_role_capabilities rc ON rc.role=r.role
                    JOIN auth_capabilities c ON c.capability=rc.capability
                    WHERE r.role=? ORDER BY c.capability''', (role,)))
        except (sqlite3.Error, OSError):
            logger.error('Authorization role capabilities unavailable; fail closed')
            return ()

    @staticmethod
    def _has_capability(conn, user_id, capability, platform):
        return conn.execute('''SELECT 1 FROM channel_users u
            JOIN auth_user_roles ur USING(platform,user_id)
            JOIN auth_roles r ON r.role=ur.role
            JOIN auth_role_capabilities rc ON rc.role=r.role
            JOIN auth_capabilities c ON c.capability=rc.capability
            WHERE u.platform=? AND u.user_id=? AND u.registration_status='approved'
              AND ur.active=1 AND c.capability=? LIMIT 1''',
            (platform, str(user_id), capability)).fetchone() is not None

    def has_capability(self, user_id, capability, platform='bale'):
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute('BEGIN')
                self._version(conn)
                return self._has_capability(conn, user_id, capability, platform)
        except (sqlite3.Error, OSError):
            logger.error('Authorization capability unavailable; fail closed')
            return False

    def can_read_overflow(self, user_id, chat_id, platform='bale', chat_type='dm'):
        if platform != 'bale' or chat_type != 'dm' or not user_id or str(chat_id) != str(user_id):
            return False
        return self.has_capability(user_id, OVERFLOW_READ, platform)

    def resolve_daily_recipient(self, *, role=OFFICE_SUPERVISOR, capability=DAILY_RECEIVE):
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute('BEGIN')
                self._version(conn)
                holders = conn.execute('''SELECT user_id FROM auth_user_roles
                    WHERE platform='bale' AND role=? AND active=1''', (role,)).fetchall()
                count = len(holders)
                if count != 1:
                    return RecipientResolution('no_active_holder' if count == 0 else 'ambiguous_holders',
                                               holder_count=count)
                user_id = holders[0][0]
                identity = conn.execute('''SELECT chat_id,registration_status FROM channel_users
                    WHERE platform='bale' AND user_id=?''', (user_id,)).fetchone()
                if (not identity or identity[1] != 'approved' or identity[0] != user_id
                        or not re.fullmatch(r'[1-9][0-9]*', user_id)):
                    return RecipientResolution('recipient_not_approved', holder_count=1)
                if not self._has_capability(conn, user_id, capability, 'bale'):
                    return RecipientResolution('capability_missing', holder_count=1)
                return RecipientResolution('ready', user_id, 1)
        except (sqlite3.Error, OSError):
            logger.error('Authorization recipient store unavailable; fail closed')
            return RecipientResolution('store_unavailable')

    def assign_role(self, user_id, role, *, actor, replace=False):
        """Local operator API; caller backs up production first. Never a bot tool.

        Replacement deactivates old holders and assigns the new one atomically.
        Registration/approval and profile routing remain independent.
        """
        user_id = str(user_id)
        with closing(self._connect(write=True)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            self._version(conn)
            identity = conn.execute('''SELECT chat_id,registration_status FROM channel_users
                WHERE platform='bale' AND user_id=?''', (user_id,)).fetchone()
            if (not identity or identity != (user_id, 'approved')
                    or not re.fullmatch(r'[1-9][0-9]*', user_id)):
                raise ValueError('Registered, approved private Bale identity required')
            timestamp = now()
            if role == OFFICE_SUPERVISOR:
                others = conn.execute('''SELECT user_id FROM auth_user_roles
                    WHERE platform='bale' AND role=? AND active=1 AND user_id<>?''', (role, user_id)).fetchall()
                if others and not replace:
                    raise ValueError('Active role holder already exists; explicit replacement required')
                for (old_user,) in others:
                    conn.execute('''UPDATE auth_user_roles SET active=0,updated_at=?
                        WHERE platform='bale' AND user_id=? AND role=?''', (timestamp, old_user, role))
                    conn.execute('''INSERT INTO auth_events(platform,user_id,role,event_type,actor,created_at)
                        VALUES ('bale',?,?,'deactivated',?,?)''', (old_user, role, actor, timestamp))
            conn.execute('''INSERT INTO auth_user_roles VALUES ('bale',?,?,1,?,?,?)
                ON CONFLICT(platform,user_id,role) DO UPDATE SET active=1,
                  assigned_at=excluded.assigned_at,assigned_by=excluded.assigned_by,updated_at=excluded.updated_at''',
                (user_id, role, timestamp, actor, timestamp))
            conn.execute('''INSERT INTO auth_events(platform,user_id,role,event_type,actor,created_at)
                VALUES ('bale',?,?,'assigned',?,?)''', (user_id, role, actor, timestamp))


    def migrate_admin(self, backup_directory=None):
        """ADMIN-1 additive extension; no registration or overflow grant changes."""
        backup = self.backup(backup_directory)
        with closing(self._connect(write=True)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            self._version(conn)
            conn.execute("""CREATE TABLE IF NOT EXISTS auth_role_profiles (
                role TEXT PRIMARY KEY REFERENCES auth_roles(role),
                profile TEXT NOT NULL, priority INTEGER NOT NULL CHECK(typeof(priority)='integer'),
                active INTEGER NOT NULL CHECK(active IN (0,1)))""")
            if conn.execute('SELECT 1 FROM auth_migrations WHERE version=2').fetchone():
                return backup
            conn.execute('INSERT INTO auth_roles VALUES (?,?)', (BUSINESS_ADMIN, 'مدیر / مسئول اداری'))
            for cap, resource, role in [(FUNCTION_READ, r'E:\Function', BUSINESS_ADMIN),
                    (DRIVER_READ, 'reports.driver_daily', OFFICE_SUPERVISOR),
                    (DRIVER_RECEIVE, 'reports.driver_daily', OFFICE_SUPERVISOR)]:
                conn.execute('INSERT INTO auth_capabilities VALUES (?,?)', (cap,resource))
                conn.execute('INSERT INTO auth_role_capabilities VALUES (?,?)', (role,cap))
            conn.execute('INSERT INTO auth_role_profiles VALUES (?,?,?,1)', (OFFICE_SUPERVISOR,'admin',100))
            conn.execute('INSERT INTO auth_migrations VALUES (2,?)', (now(),))
        return backup

    def resolve_profile(self, user_id, chat_id, platform='bale'):
        """Fresh role mapping per identity resolution; no profile-based permissions."""
        if not user_id or str(user_id) != str(chat_id):
            return None
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute('BEGIN')
                self._version(conn)
                rows = conn.execute("""SELECT rp.profile,rp.priority FROM auth_role_profiles rp
                    JOIN auth_roles r ON r.role=rp.role
                    JOIN auth_user_roles ur ON ur.role=r.role
                    JOIN channel_users u USING(platform,user_id)
                    WHERE ur.platform=? AND ur.user_id=? AND ur.active=1 AND rp.active=1
                      AND u.registration_status='approved' AND u.chat_id=u.user_id
                    ORDER BY rp.priority DESC""", (platform,str(user_id))).fetchall()
                if not rows:
                    return None
                if any(not isinstance(p,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',p)
                       or type(priority) is not int for p,priority in rows):
                    return None
                highest = {p for p,priority in rows if priority == rows[0][1]}
                if len(highest) != 1:
                    return None
                return highest.pop()
        except (sqlite3.Error, OSError, ValueError):
            logger.error('Authorization profile mapping unavailable; fail closed')
            return None


    def assign_roles(self, assignments, *, actor):
        """Atomic additive operator assignments; never modifies registration or other roles."""
        with closing(self._connect(write=True)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            self._version(conn)
            timestamp = now()
            for user_id, role in assignments:
                user_id = str(user_id)
                identity = conn.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?", (user_id,)).fetchone()
                if identity != (user_id, 'approved') or not re.fullmatch(r'[1-9][0-9]*',user_id):
                    raise ValueError('Registered approved private Bale identity required')
                if role == OFFICE_SUPERVISOR:
                    raise ValueError('Supervisor assignment requires the existing explicit replacement API')
                conn.execute("""INSERT INTO auth_user_roles VALUES ('bale',?,?,1,?,?,?)
                    ON CONFLICT(platform,user_id,role) DO UPDATE SET active=1,
                    assigned_at=excluded.assigned_at,assigned_by=excluded.assigned_by,updated_at=excluded.updated_at""",
                    (user_id,role,timestamp,actor,timestamp))
                conn.execute("""INSERT INTO auth_events(platform,user_id,role,event_type,actor,created_at)
                    VALUES ('bale',?,?,'assigned',?,?)""", (user_id,role,actor,timestamp))
