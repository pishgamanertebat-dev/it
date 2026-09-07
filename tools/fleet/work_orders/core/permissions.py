from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from tools.fleet.work_orders.core.paths import DB_PATH


MAINTENANCE_MANAGER = "MAINTENANCE_MANAGER"


@dataclass(frozen=True)
class PermissionResult:
    status: str
    reason: str
    bale_id: str | None = None
    role: str | None = None

    @property
    def allowed(self) -> bool:
        return self.status == "ALLOWED"


class WorkOrderPermissionDenied(PermissionError):
    def __init__(self, result: PermissionResult):
        self.result = result
        super().__init__("شما اجازهٔ مدیریت حکم کار را ندارید.")


def normalize_bale_id(value: str | int) -> str | None:
    """Accept a platform user ID, never a display name or message-supplied role."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    value = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]*", value):
        return None
    return value


def check_work_order_permission(
    bale_id: str | int,
    *,
    db_path: Path | str | None = None,
) -> PermissionResult:
    """Read authorization on every call; missing or unavailable data denies access.

    The caller must use the authenticated Bale sender's user_id. This policy is
    independent of registration approval, bot admins and service_staff routing.
    No database, schema, user or role is created by a permission check.
    """
    user_id = normalize_bale_id(bale_id)
    if user_id is None:
        return PermissionResult("DENIED", "INVALID_BALE_ID")

    path = Path(db_path) if db_path is not None else DB_PATH
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            con.execute("PRAGMA query_only = ON")
            row = con.execute(
                """
                SELECT role, active
                FROM service_work_order_users
                WHERE bale_id = ?
                """,
                (user_id,),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return PermissionResult("DENIED", "PERMISSION_STORE_UNAVAILABLE", user_id)

    if row is None:
        return PermissionResult("DENIED", "UNKNOWN_USER", user_id)

    role, active = row
    if active != 1:
        return PermissionResult("DENIED", "INACTIVE_USER", user_id, role)
    if role != MAINTENANCE_MANAGER:
        return PermissionResult("DENIED", "ROLE_NOT_ALLOWED", user_id, role)
    return PermissionResult("ALLOWED", "AUTHORIZED", user_id, role)


def require_work_order_permission(
    bale_id: str | int,
    *,
    db_path: Path | str | None = None,
) -> PermissionResult:
    result = check_work_order_permission(bale_id, db_path=db_path)
    if not result.allowed:
        raise WorkOrderPermissionDenied(result)
    return result
