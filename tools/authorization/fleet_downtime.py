"""Additive authorization extension for Fleet Downtime monthly report delivery & admin controls.

Read-only Q&A access to Fleet Downtime is open to ALL approved KomatsoAI users across all profiles
via the independent `komatso-fleet-downtime` plugin without widening file/repair/admin access.
This extension defines ONLY the administrative & monthly-delivery receive capabilities, ready
for controlled candidate verification and owner-approved activation.
"""
from __future__ import annotations

from contextlib import closing
import re
import sqlite3

from .store import now

EXTENSION = "fleet_downtime_v1"
RECEIVE_ROLE = "fleet_downtime_monthly_recipient"
RECEIVE_CAPABILITY = "reports.fleet_downtime.monthly_receive"
RECEIVE_RESOURCE = "reports.fleet_downtime.monthly"

ADMIN_ROLE = "fleet_downtime_admin"
ADMIN_CAPABILITY = "reports.fleet_downtime.admin_manage"
ADMIN_RESOURCE = "reports.fleet_downtime.admin"


def migrate(
    store,
    backup_directory,
    *,
    recipient_user_ids: tuple[str, ...] = (),
    admin_user_ids: tuple[str, ...] = (),
    actor: str = "fleet-downtime-migration-v1",
):
    """Idempotent, additive schema-v2 extension. Never alters existing roles, profiles, or capabilities."""
    backup = store.backup(backup_directory)
    with closing(store._connect(write=True)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        store._version(conn)
        if (
            conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
            or conn.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise sqlite3.DatabaseError("Authorization integrity failed")

        policies = (
            (RECEIVE_ROLE, RECEIVE_CAPABILITY, RECEIVE_RESOURCE, "گیرنده گزارش ماهانه توقفات ماشین‌آلات", recipient_user_ids),
            (ADMIN_ROLE, ADMIN_CAPABILITY, ADMIN_RESOURCE, "مدیر ماژول توقفات ماشین‌آلات", admin_user_ids),
        )
        pending = []
        for role, cap, resource, title, users in policies:
            conn.execute("INSERT OR IGNORE INTO auth_roles VALUES (?,?)", (role, title))
            conn.execute("INSERT OR IGNORE INTO auth_capabilities VALUES (?,?)", (cap, resource))
            conn.execute("INSERT OR IGNORE INTO auth_role_capabilities VALUES (?,?)", (role, cap))
            if (
                conn.execute("SELECT capability FROM auth_role_capabilities WHERE role=?", (role,)).fetchall() != [(cap,)]
                or conn.execute("SELECT role FROM auth_role_capabilities WHERE capability=?", (cap,)).fetchall() != [(role,)]
                or conn.execute("SELECT resource FROM auth_capabilities WHERE capability=?", (cap,)).fetchall() != [(resource,)]
                or conn.execute("SELECT 1 FROM auth_role_profiles WHERE role=?", (role,)).fetchone()
            ):
                raise ValueError(f"Conflicting fleet downtime policy for {role}")
            for uid in sorted(set(map(str, users))):
                if (
                    not re.fullmatch(r"[1-9][0-9]*", uid)
                    or conn.execute(
                        "SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?",
                        (uid,),
                    ).fetchone()
                    != (uid, "approved")
                ):
                    raise ValueError(f"Approved private Bale identity required: {uid}")
                if (
                    conn.execute(
                        "SELECT active FROM auth_user_roles WHERE platform='bale' AND user_id=? AND role=?",
                        (uid, role),
                    ).fetchone()
                    != (1,)
                ):
                    pending.append((uid, role))

        store._assign_roles(conn, pending, actor=actor)
        conn.execute("CREATE TABLE IF NOT EXISTS auth_extensions (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT OR IGNORE INTO auth_extensions VALUES (?,?)", (EXTENSION, now()))
        if (
            conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
            or conn.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise sqlite3.DatabaseError("Authorization integrity failed after fleet_downtime migration")
    return backup
