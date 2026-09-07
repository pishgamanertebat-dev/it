from __future__ import annotations

import sqlite3

from tools.fleet.work_orders.core.paths import DB_PATH


def connect_db() -> sqlite3.Connection:

    if not DB_PATH.exists():

        raise RuntimeError(
            f"Fleet DB not found: {DB_PATH}"
        )

    con = sqlite3.connect(
        str(DB_PATH)
    )

    con.row_factory = sqlite3.Row

    con.execute(
        "PRAGMA foreign_keys = ON"
    )

    return con