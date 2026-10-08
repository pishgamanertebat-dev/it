"""Single SQLite authorization API; reads never create/migrate a database."""
from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / 'reports/telegram_usage/telegram_users.db'
OFFICE_SUPERVISOR = 'office_supervisor'
OVERFLOW_READ = 'reports.overflow.read'
DAILY_RECEIVE = 'reports.overflow.daily_receive'
BUSINESS_ADMIN = 'business_admin'
BUSINESS_ADMIN_PROFILE_PRIORITY = 10
FUNCTION_READ = 'function.read_all'
DRIVER_READ = 'reports.driver_daily.read'
DRIVER_RECEIVE = 'reports.driver_daily.daily_receive'
# Mechanical Phase 1 (additive). Profile = expertise, Role = responsibility, Capability = grant.
MECHANICAL_STAFF = 'mechanical_staff'
MECHANICAL_MANAGER = 'mechanical_manager'
MECHANICAL_MANAGER_DEPUTY = 'mechanical_manager_deputy'
MAINTENANCE_PROFILE = 'maintenance'
MAINTENANCE_RECORDS_READ = 'maintenance.records.read'
DRIVER_REPORT_READ = 'repairs.driver_report.read'
MECH_OVERFLOW_RECEIVE = 'reports.overflow.mechanical_daily_receive'
MECH_DRIVER_RECEIVE = 'reports.driver_daily.mechanical_daily_receive'
# Exact-file resource capabilities. The path map is owned by code, never by DB rows:
# a capability row can document a resource but cannot widen it.
FILE_RESOURCES = {
    MAINTENANCE_RECORDS_READ: 'تعمیرات 1405.xlsx',
    DRIVER_REPORT_READ: 'گزارش روزانه رانندگان2.xlsx',
}
FUNCTION_CAPABILITIES = (FUNCTION_READ, *FILE_RESOURCES)
MECHANICAL_ROLES = (
    (MECHANICAL_STAFF, 'نیروی مکانیکی / تعمیرات'),
    (MECHANICAL_MANAGER, 'مسئول مکانیکی'),
    (MECHANICAL_MANAGER_DEPUTY, 'جانشین مسئول مکانیکی'),
)
MECHANICAL_CAPABILITIES = (
    (MAINTENANCE_RECORDS_READ, 'E:\\Function\\' + FILE_RESOURCES[MAINTENANCE_RECORDS_READ]),
    (DRIVER_REPORT_READ, 'E:\\Function\\' + FILE_RESOURCES[DRIVER_REPORT_READ]),
    (MECH_OVERFLOW_RECEIVE, 'reports.overflow'),
    (MECH_DRIVER_RECEIVE, 'reports.driver_daily.mechanical'),
)
_MANAGER_CAPABILITIES = (MAINTENANCE_RECORDS_READ, DRIVER_REPORT_READ, OVERFLOW_READ,
                         MECH_OVERFLOW_RECEIVE, MECH_DRIVER_RECEIVE)
# Staff gets only the technical-records read. Managers add data/report grants on top of
# the base role; neither role maps to a profile (only mechanical_staff -> maintenance does).
MECHANICAL_ROLE_CAPABILITIES = {
    MECHANICAL_STAFF: (MAINTENANCE_RECORDS_READ,),
    MECHANICAL_MANAGER: _MANAGER_CAPABILITIES,
    MECHANICAL_MANAGER_DEPUTY: _MANAGER_CAPABILITIES,
}
MECHANICAL_PROFILE_MAP = ((MECHANICAL_STAFF, MAINTENANCE_PROFILE, 100),)
MECHANICAL_EXTENSION = 'mechanical_roles_v1'
METALWORK_STAFF = 'metalwork_staff'
METALWORK_DRIVER_RECEIVE = 'reports.driver_daily.metalwork_daily_receive'
METALWORK_EXTENSION = 'metalwork_roles_v1'
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


@dataclass(frozen=True)
class RecipientSetResolution:
    """Multi-recipient counterpart of RecipientResolution (never an exactly-one invariant)."""
    status: str
    recipients: tuple = ()
    holder_count: int = 0
    skipped_count: int = 0


@dataclass(frozen=True)
class FunctionScope:
    """What a verified identity may read below E:\\Function. Empty scope = nothing."""
    all: bool = False
    files: tuple = ()

    def __bool__(self):
        return self.all or bool(self.files)


def valid_registry_name(value, user_id):
    """A registry label may be shown to an operator. Anything else stays an id."""
    if not isinstance(value, str):
        return None
    if any(ord(ch) < 32 or ch in '\\/' for ch in value):
        return None
    text = ' '.join(value.split())
    if not text or len(text) > 80 or text == str(user_id) or text.isdigit():
        return None
    if not any(ch.isalpha() for ch in text):
        return None
    return text


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

    def identity_label(self, user_id, platform='bale'):
        """Approved private registry name. Falls back to the id; never invents one."""
        user_id = str(user_id)
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute('BEGIN')
                self._version(conn)
                columns = {row[1] for row in conn.execute('PRAGMA table_info(channel_users)')}
                selected = [name for name in ('verified_name', 'display_name') if name in columns]
                if not selected:
                    return user_id
                row = conn.execute(
                    f'''SELECT {", ".join(selected)} FROM channel_users
                        WHERE platform=? AND user_id=? AND registration_status='approved'
                          AND chat_id=user_id''',
                    (platform, user_id)).fetchone()
        except (sqlite3.Error, OSError):
            logger.error('Authorization identity label unavailable; using id')
            return user_id
        if not row:
            return user_id
        for value in row:
            label = valid_registry_name(value, user_id)
            if label:
                return label
        return user_id

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
            self._assign_roles(conn, assignments, actor=actor)

    @staticmethod
    def _assign_roles(conn, assignments, *, actor):
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

    def migrate_business_admin_profile(self, backup_directory=None, *, assignments=(), actor='business-admin-profile'):
        """Add generic Admin routing and explicit assignments in one guarded transaction.

        Schema v2 is retained for already-running readers. No role, resource,
        capability, registration or scheduled grant is created. Specialized
        mappings must already have strictly higher priority.
        """
        backup = self.backup(backup_directory)
        with closing(self._connect(write=True)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            self._version(conn)
            if conn.execute('SELECT MAX(version) FROM auth_migrations').fetchone()[0] != 2:
                raise sqlite3.DatabaseError('ADMIN-1 authorization schema is required first')
            caps = conn.execute('SELECT capability FROM auth_role_capabilities WHERE role=?', (BUSINESS_ADMIN,)).fetchall()
            if caps != [(FUNCTION_READ,)]:
                raise sqlite3.DatabaseError('Business Admin must carry only function.read_all')
            if conn.execute('SELECT resource FROM auth_capabilities WHERE capability=?', (FUNCTION_READ,)).fetchone() != (r'E:\Function',):
                raise sqlite3.DatabaseError('Conflicting Function resource root')
            for role, profile in [(OFFICE_SUPERVISOR, 'admin'), (MECHANICAL_STAFF, MAINTENANCE_PROFILE)]:
                row = conn.execute('SELECT profile,priority,active FROM auth_role_profiles WHERE role=?', (role,)).fetchone()
                if not row or row[0] != profile or row[2] != 1 or type(row[1]) is not int or row[1] <= BUSINESS_ADMIN_PROFILE_PRIORITY:
                    raise sqlite3.DatabaseError('Specialized profile must have higher priority')
            conn.execute('INSERT OR IGNORE INTO auth_role_profiles VALUES (?,?,?,1)',
                         (BUSINESS_ADMIN, 'admin', BUSINESS_ADMIN_PROFILE_PRIORITY))
            if conn.execute('SELECT profile,priority,active FROM auth_role_profiles WHERE role=?', (BUSINESS_ADMIN,)).fetchone() != ('admin', BUSINESS_ADMIN_PROFILE_PRIORITY, 1):
                raise sqlite3.DatabaseError('Conflicting Business Admin profile mapping')
            pending = []
            for user, role in assignments:
                if role not in (BUSINESS_ADMIN, MECHANICAL_STAFF):
                    raise ValueError('Only Business Admin or explicitly requested Mechanical Staff assignments allowed')
                user = str(user)
                if conn.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?", (user,)).fetchone() != (user, 'approved'):
                    raise ValueError('Registered approved private Bale identity required')
                if conn.execute("SELECT active FROM auth_user_roles WHERE platform='bale' AND user_id=? AND role=?", (user,role)).fetchone() != (1,):
                    pending.append((user,role))
            self._assign_roles(conn, pending, actor=actor)
            conn.execute('CREATE TABLE IF NOT EXISTS auth_extensions (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
            conn.execute('INSERT OR IGNORE INTO auth_extensions VALUES (?,?)', ('business_admin_profile_v1', now()))
            if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or conn.execute('PRAGMA foreign_key_check').fetchall():
                raise sqlite3.DatabaseError('Authorization integrity failed')
        return backup

    def migrate_mechanical(self, backup_directory=None, *, assignments=(), actor='mechanical-migration'):
        """Mechanical Phase 1: additive roles, capabilities and the single Role->Profile row.

        Idempotent. It deliberately records an `auth_extensions` marker instead of a new
        `auth_migrations` version: already-running readers accept only schema versions 1/2
        and would fail closed for every identity if MAX(version) changed under them.
        Existing roles, capabilities, assignments and registrations are never touched.
        """
        backup = self.backup(backup_directory)
        with closing(self._connect(write=True)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            self._version(conn)
            if conn.execute('SELECT MAX(version) FROM auth_migrations').fetchone()[0] != 2:
                raise sqlite3.DatabaseError('ADMIN-1 authorization schema is required first')
            conn.execute('''CREATE TABLE IF NOT EXISTS auth_extensions (
                name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)''')
            for role, display in MECHANICAL_ROLES:
                conn.execute('INSERT OR IGNORE INTO auth_roles VALUES (?,?)', (role, display))
            for capability, resource in MECHANICAL_CAPABILITIES:
                conn.execute('INSERT OR IGNORE INTO auth_capabilities VALUES (?,?)', (capability, resource))
            for role, capabilities in MECHANICAL_ROLE_CAPABILITIES.items():
                for capability in capabilities:
                    conn.execute('INSERT OR IGNORE INTO auth_role_capabilities VALUES (?,?)', (role, capability))
            for role, profile, priority in MECHANICAL_PROFILE_MAP:
                conn.execute('INSERT OR IGNORE INTO auth_role_profiles VALUES (?,?,?,1)', (role, profile, priority))
                if conn.execute('SELECT profile,priority,active FROM auth_role_profiles WHERE role=?',
                                (role,)).fetchone() != (profile, priority, 1):
                    raise sqlite3.DatabaseError('Conflicting existing Role->Profile mapping; refusing to override')
            # No manager/deputy profile rows or unexpected grants may sneak into an existing seed.
            for role, expected in MECHANICAL_ROLE_CAPABILITIES.items():
                actual = {r[0] for r in conn.execute('SELECT capability FROM auth_role_capabilities WHERE role=?', (role,))}
                if actual != set(expected):
                    raise sqlite3.DatabaseError('Conflicting mechanical role capabilities')
            if conn.execute('SELECT 1 FROM auth_role_profiles WHERE role IN (?,?)',
                            (MECHANICAL_MANAGER, MECHANICAL_MANAGER_DEPUTY)).fetchone():
                raise sqlite3.DatabaseError('Manager roles must not map to a profile')
            for capability, resource in MECHANICAL_CAPABILITIES:
                if conn.execute('SELECT resource FROM auth_capabilities WHERE capability=?',
                                (capability,)).fetchone() != (resource,):
                    raise sqlite3.DatabaseError('Conflicting fixed resource mapping')
            timestamp = now()
            for user_id, role in assignments:
                user_id = str(user_id)
                if role not in MECHANICAL_ROLE_CAPABILITIES:
                    raise ValueError('Only mechanical roles may be assigned in this migration')
                identity = conn.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?",
                                        (user_id,)).fetchone()
                if identity != (user_id, 'approved') or not re.fullmatch(r'[1-9][0-9]*',user_id):
                    raise ValueError('Registered approved private Bale identity required')
                conn.execute("""INSERT INTO auth_user_roles VALUES ('bale',?,?,1,?,?,?)
                    ON CONFLICT(platform,user_id,role) DO UPDATE SET active=1,
                    assigned_at=excluded.assigned_at,assigned_by=excluded.assigned_by,updated_at=excluded.updated_at""",
                    (user_id,role,timestamp,actor,timestamp))
                conn.execute("""INSERT INTO auth_events(platform,user_id,role,event_type,actor,created_at)
                    VALUES ('bale',?,?,'assigned',?,?)""", (user_id,role,actor,timestamp))
            conn.execute('INSERT OR IGNORE INTO auth_extensions VALUES (?,?)', (MECHANICAL_EXTENSION, timestamp))
        return backup

    def migrate_metalwork(self, backup_directory=None, *, assignments=(), actor='metalwork-migration'):
        """Minimal scheduled-delivery role; additive, atomic, idempotent schema-v2 extension.

        No Function scope or on-demand data access. Existing seeds or effective
        profile conflicts are rejected rather than overriding authorization.
        """
        backup = self.backup(backup_directory)
        with closing(self._connect(write=True)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            self._version(conn)
            if conn.execute('SELECT MAX(version) FROM auth_migrations').fetchone() != (2,):
                raise sqlite3.DatabaseError('ADMIN-1 authorization schema is required first')
            if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or conn.execute('PRAGMA foreign_key_check').fetchall():
                raise sqlite3.DatabaseError('Authorization integrity failed before migration')
            conn.execute('CREATE TABLE IF NOT EXISTS auth_extensions (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
            conn.execute('INSERT OR IGNORE INTO auth_roles VALUES (?,?)', (METALWORK_STAFF, 'نیروی آهنگری'))
            conn.execute('INSERT OR IGNORE INTO auth_capabilities VALUES (?,?)',
                         (METALWORK_DRIVER_RECEIVE, 'reports.driver_daily.metalwork'))
            conn.execute('INSERT OR IGNORE INTO auth_role_capabilities VALUES (?,?)',
                         (METALWORK_STAFF, METALWORK_DRIVER_RECEIVE))
            conn.execute('INSERT OR IGNORE INTO auth_role_profiles VALUES (?,?,?,1)',
                         (METALWORK_STAFF, MAINTENANCE_PROFILE, 100))
            if conn.execute('SELECT profile,priority,active FROM auth_role_profiles WHERE role=?',
                            (METALWORK_STAFF,)).fetchone() != (MAINTENANCE_PROFILE, 100, 1):
                raise sqlite3.DatabaseError('Conflicting metalwork profile mapping')
            if conn.execute('SELECT capability FROM auth_role_capabilities WHERE role=?',
                            (METALWORK_STAFF,)).fetchall() != [(METALWORK_DRIVER_RECEIVE,)]:
                raise sqlite3.DatabaseError('Metalwork Staff must carry only its scheduled receive capability')
            if conn.execute('SELECT resource FROM auth_capabilities WHERE capability=?',
                            (METALWORK_DRIVER_RECEIVE,)).fetchone() != ('reports.driver_daily.metalwork',):
                raise sqlite3.DatabaseError('Conflicting metalwork report resource')
            pending = []
            for user_id in dict.fromkeys(map(str, assignments)):
                if (not re.fullmatch(r'[1-9][0-9]*', user_id) or
                        conn.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?",
                                     (user_id,)).fetchone() != (user_id, 'approved')):
                    raise ValueError('Registered approved private Bale identity required')
                # Calculate the proposed highest-priority profile before assignment.
                rows = conn.execute("""SELECT rp.profile,rp.priority FROM auth_role_profiles rp
                    WHERE rp.active=1 AND (rp.role=? OR rp.role IN (
                        SELECT role FROM auth_user_roles WHERE platform='bale' AND user_id=? AND active=1))
                    ORDER BY rp.priority DESC""", (METALWORK_STAFF, user_id)).fetchall()
                if (any(not isinstance(profile, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', profile)
                        or type(priority) is not int for profile, priority in rows)
                        or {profile for profile, priority in rows if priority == rows[0][1]} != {MAINTENANCE_PROFILE}):
                    raise ValueError('Conflicting effective profile; metalwork assignment refused')
                existing = {r[0] for r in conn.execute("""SELECT rc.capability FROM auth_user_roles ur
                    JOIN auth_role_capabilities rc ON rc.role=ur.role
                    WHERE ur.platform='bale' AND ur.user_id=? AND ur.active=1""", (user_id,))}
                if existing - {METALWORK_DRIVER_RECEIVE}:
                    raise ValueError('Existing operational grants require review before minimal metalwork assignment')
                if conn.execute("SELECT active FROM auth_user_roles WHERE platform='bale' AND user_id=? AND role=?",
                                (user_id, METALWORK_STAFF)).fetchone() != (1,):
                    pending.append((user_id, METALWORK_STAFF))
            self._assign_roles(conn, pending, actor=actor)
            conn.execute('INSERT OR IGNORE INTO auth_extensions VALUES (?,?)', (METALWORK_EXTENSION, now()))
            if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or conn.execute('PRAGMA foreign_key_check').fetchall():
                raise sqlite3.DatabaseError('Authorization integrity failed after migration')
        return backup

    def resolve_active_recipients(self, capability):
        """Generic multi-recipient resolution for any push capability.

        0 eligible -> not ready (caller skips + logs); N eligible -> all N, deduplicated.
        A recipient needs an active role carrying the capability, approved registration
        and a private Bale identity (chat_id == user_id). Ineligible holders are skipped
        individually and never block the others. Fail closed on storage problems.
        """
        if not isinstance(capability, str) or not capability:
            return RecipientSetResolution('invalid_capability')
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute('BEGIN')
                self._version(conn)
                holders = [row[0] for row in conn.execute('''SELECT DISTINCT ur.user_id
                    FROM auth_user_roles ur JOIN auth_role_capabilities rc ON rc.role=ur.role
                    WHERE ur.platform='bale' AND ur.active=1 AND rc.capability=?
                    ORDER BY ur.user_id''', (capability,))]
                if not holders:
                    return RecipientSetResolution('no_active_holder')
                ready = []
                for user_id in holders:
                    identity = conn.execute('''SELECT chat_id,registration_status FROM channel_users
                        WHERE platform='bale' AND user_id=?''', (user_id,)).fetchone()
                    if (identity and identity[1] == 'approved' and identity[0] == user_id
                            and re.fullmatch(r'[1-9][0-9]*', user_id)
                            and self._has_capability(conn, user_id, capability, 'bale')):
                        ready.append(user_id)
                if not ready:
                    return RecipientSetResolution('no_eligible_recipient', holder_count=len(holders),
                                                  skipped_count=len(holders))
                return RecipientSetResolution('ready', tuple(ready), len(holders), len(holders) - len(ready))
        except (sqlite3.Error, OSError):
            logger.error('Authorization recipient set unavailable; fail closed')
            return RecipientSetResolution('store_unavailable')

    def function_scope(self, user_id, platform='bale'):
        """Resource scope of an approved identity below E:\\Function.

        function.read_all -> everything. Exact-file capabilities -> only their own file.
        Paths come from code (FILE_RESOURCES); a DB resource that disagrees voids the grant.
        """
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute('BEGIN')
                self._version(conn)
                rows = conn.execute('''SELECT DISTINCT c.capability,c.resource FROM channel_users u
                    JOIN auth_user_roles ur USING(platform,user_id)
                    JOIN auth_roles r ON r.role=ur.role
                    JOIN auth_role_capabilities rc ON rc.role=r.role
                    JOIN auth_capabilities c ON c.capability=rc.capability
                    WHERE u.platform=? AND u.user_id=? AND u.registration_status='approved'
                      AND u.chat_id=u.user_id AND ur.active=1''', (platform, str(user_id))).fetchall()
        except (sqlite3.Error, OSError):
            logger.error('Authorization function scope unavailable; fail closed')
            return FunctionScope()
        if any(capability == FUNCTION_READ for capability, _ in rows):
            return FunctionScope(all=True)
        files = sorted({FILE_RESOURCES[capability] for capability, resource in rows
                        if capability in FILE_RESOURCES
                        and resource == 'E:\\Function\\' + FILE_RESOURCES[capability]})
        return FunctionScope(False, tuple(files))
