"""NET catalog, additive operator migration and audited domain authority.

Profiles never authorize. Domain markers persist when a role is revoked.
Legacy stores remain the authority only for identities without a marker.
"""
from contextlib import closing
from dataclasses import dataclass
import logging
import re
import sqlite3

from .store import AuthorizationStore, FUNCTION_READ, now

NET_MANAGER = 'net_manager'
NET_DEPUTY = 'net_manager_deputy'
NET_PROFILE = 'net'
NET_EXTENSION = 'net_roles_v1'
WO_DOMAIN = 'work_orders'
REPAIRS_DOMAIN = 'repairs.driver_report'
WO_CAPABILITIES = (
    'work_orders.create', 'work_orders.read_own', 'work_orders.review',
    'work_orders.assign', 'work_orders.approve', 'work_orders.send',
    'work_orders.air_filter.archive_append', 'work_orders.greasing.archive_append',
)
REPAIRS_CAPABILITIES = (
    'repairs.driver_report.edit', 'repairs.driver_report.append',
    'repairs.driver_report.clear', 'repairs.driver_report.daily_sheet_create',
)
NET_CAPABILITIES = (*WO_CAPABILITIES, *REPAIRS_CAPABILITIES, FUNCTION_READ)
RESOURCES = {
    **{cap: 'work_orders.own' for cap in WO_CAPABILITIES[:6]},
    WO_CAPABILITIES[6]: r'E:\Function\لیست روزانه هواکش .xlsx',
    WO_CAPABILITIES[7]: r'E:\Function\لیست روزانه گریسکاری.xlsx',
    **{cap: r'E:\Function\گزارش روزانه رانندگان2.xlsx' for cap in REPAIRS_CAPABILITIES},
    FUNCTION_READ: r'E:\Function',
}
DOMAINS = {WO_DOMAIN: WO_CAPABILITIES, REPAIRS_DOMAIN: REPAIRS_CAPABILITIES}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DomainDecision:
    allowed: bool
    source: str
    reason: str


def domain_decision(actor, domain, capabilities=None, *, store=None):
    """One fresh transaction for marker, approval, capability and fixed resource.

    source=legacy delegates to the existing domain policy; source=central is
    authoritative, including deny. Storage failure always denies, never falls back.
    This function is not a bot tool. Its caller obtains actor from trusted Bale.
    """
    if isinstance(actor, bool) or not isinstance(actor, (str, int)) or not re.fullmatch(r'[1-9][0-9]*', str(actor)):
        result = DomainDecision(False, 'denied', 'invalid_actor')
    elif domain not in DOMAINS:
        result = DomainDecision(False, 'denied', 'invalid_domain')
    else:
        required = tuple(DOMAINS[domain] if capabilities is None else capabilities)
        if not required or not set(required) <= set(DOMAINS[domain]):
            result = DomainDecision(False, 'denied', 'invalid_capability')
        else:
            store = store or AuthorizationStore()
            try:
                with closing(store._connect()) as con, con:
                    con.execute('BEGIN')
                    store._version(con)
                    exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='auth_domain_authority'").fetchone()
                    if not exists:
                        # Pre-NET databases stay compatible; a damaged NET DB fails closed.
                        ext = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='auth_extensions'").fetchone()
                        if ext and con.execute('SELECT 1 FROM auth_extensions WHERE name=?', (NET_EXTENSION,)).fetchone():
                            raise sqlite3.DatabaseError('NET authority markers missing')
                        result = DomainDecision(False, 'legacy', 'unmigrated')
                    elif not con.execute('SELECT 1 FROM auth_domain_authority WHERE platform=? AND user_id=? AND domain=?',
                                         ('bale', str(actor), domain)).fetchone():
                        result = DomainDecision(False, 'legacy', 'unmigrated')
                    else:
                        identity = con.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?",
                                               (str(actor),)).fetchone()
                        allowed = identity == (str(actor), 'approved') and all(
                            store._has_capability(con, actor, cap, 'bale') and
                            con.execute('SELECT resource FROM auth_capabilities WHERE capability=?', (cap,)).fetchone() == (RESOURCES[cap],)
                            for cap in required)
                        result = DomainDecision(bool(allowed), 'central', 'authorized' if allowed else 'central_denied')
            except (sqlite3.Error, OSError, ValueError):
                result = DomainDecision(False, 'denied', 'store_unavailable')
    logger.info('NET authorization actor=%s domain=%s source=%s allowed=%s reason=%s',
                actor if isinstance(actor, (str, int)) else 'invalid', domain,
                result.source, result.allowed, result.reason)
    return result


def migrate_net(store, backup_directory=None, *, assignments=(), actor='net-operator'):
    """Online backup, transactional/idempotent extension. Empty assignments = NET-1.

    NET-2 assignment and permanent per-domain markers commit atomically.
    Caller must enforce the tested NET-1 gate before production assignments.
    No legacy/staff/schedule or registration records are modified.
    """
    backup = store.backup(backup_directory)
    with closing(store._connect(write=True)) as con, con:
        con.execute('BEGIN IMMEDIATE')
        store._version(con)
        if con.execute('SELECT MAX(version) FROM auth_migrations').fetchone() != (2,):
            raise sqlite3.DatabaseError('Authorization v2 required')
        if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or con.execute('PRAGMA foreign_key_check').fetchall():
            raise sqlite3.DatabaseError('Authorization integrity failed')
        con.execute('CREATE TABLE IF NOT EXISTS auth_extensions (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
        con.execute("""CREATE TABLE IF NOT EXISTS auth_domain_authority (
            platform TEXT NOT NULL, user_id TEXT NOT NULL,
            domain TEXT NOT NULL CHECK(domain IN ('work_orders','repairs.driver_report')),
            migrated_at TEXT NOT NULL, migrated_by TEXT NOT NULL,
            PRIMARY KEY(platform,user_id,domain),
            FOREIGN KEY(platform,user_id) REFERENCES channel_users(platform,user_id))""")
        for role, label in [(NET_MANAGER, 'مسئول NET'), (NET_DEPUTY, 'جانشین مسئول NET')]:
            con.execute('INSERT OR IGNORE INTO auth_roles VALUES (?,?)', (role, label))
            if con.execute('SELECT display_name FROM auth_roles WHERE role=?', (role,)).fetchone() != (label,):
                raise sqlite3.DatabaseError('Conflicting NET role')
            con.execute('INSERT OR IGNORE INTO auth_role_profiles VALUES (?,?,100,1)', (role, NET_PROFILE))
            if con.execute('SELECT profile,priority,active FROM auth_role_profiles WHERE role=?', (role,)).fetchone() != (NET_PROFILE,100,1):
                raise sqlite3.DatabaseError('Conflicting NET profile mapping')
            for capability, resource in RESOURCES.items():
                con.execute('INSERT OR IGNORE INTO auth_capabilities VALUES (?,?)', (capability, resource))
                if con.execute('SELECT resource FROM auth_capabilities WHERE capability=?', (capability,)).fetchone() != (resource,):
                    raise sqlite3.DatabaseError('Conflicting NET resource')
                con.execute('INSERT OR IGNORE INTO auth_role_capabilities VALUES (?,?)', (role,capability))
            if {r[0] for r in con.execute('SELECT capability FROM auth_role_capabilities WHERE role=?', (role,))} != set(NET_CAPABILITIES):
                raise sqlite3.DatabaseError('Conflicting NET capability catalog')
        for user, role in dict.fromkeys(assignments):
            user = str(user)
            if role not in (NET_MANAGER, NET_DEPUTY):
                raise ValueError('Only NET roles allowed')
            if (not re.fullmatch(r'[1-9][0-9]*', user) or
                    con.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?", (user,)).fetchone() != (user,'approved')):
                raise ValueError('Approved private Bale identity required')
            rows = con.execute("""SELECT profile,priority FROM auth_role_profiles WHERE active=1 AND
                (role=? OR role IN (SELECT role FROM auth_user_roles WHERE platform='bale' AND user_id=? AND active=1))
                ORDER BY priority DESC""", (role,user)).fetchall()
            if {p for p, priority in rows if priority == rows[0][1]} != {NET_PROFILE}:
                raise ValueError('Ambiguous or conflicting profile')
            if con.execute("SELECT active FROM auth_user_roles WHERE platform='bale' AND user_id=? AND role=?", (user,role)).fetchone() != (1,):
                store._assign_roles(con, [(user,role)], actor=actor)
            for domain in DOMAINS:
                if not con.execute("SELECT 1 FROM auth_domain_authority WHERE platform='bale' AND user_id=? AND domain=?", (user,domain)).fetchone():
                    con.execute("INSERT INTO auth_domain_authority VALUES ('bale',?,?,?,?)", (user,domain,now(),actor))
                    con.execute("""INSERT INTO auth_events(platform,user_id,role,event_type,actor,created_at)
                        VALUES ('bale',?,?,?, ?,?)""", (user,role,'central_authority:'+domain,actor,now()))
        con.execute('INSERT OR IGNORE INTO auth_extensions VALUES (?,?)', (NET_EXTENSION,now()))
        if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or con.execute('PRAGMA foreign_key_check').fetchall():
            raise sqlite3.DatabaseError('Authorization integrity failed after NET migration')
    return backup
