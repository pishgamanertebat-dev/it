from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)

CODE_RE = re.compile(r"^[A-Za-z]+[0-9]+$")


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS machines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            canonical_code TEXT NOT NULL UNIQUE,

            machine_type_hint TEXT,
            model_key TEXT,

            identity_status TEXT NOT NULL DEFAULT 'PROVISIONAL',

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS machine_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            machine_id INTEGER NOT NULL,

            alias_code TEXT NOT NULL,
            alias_type_raw TEXT NOT NULL DEFAULT '',

            source_type TEXT NOT NULL,

            verified INTEGER NOT NULL DEFAULT 0,

            created_at TEXT NOT NULL,

            UNIQUE (
                alias_code,
                alias_type_raw,
                source_type
            ),

            FOREIGN KEY (machine_id)
                REFERENCES machines(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS machine_identity_observations (
            raw_code TEXT NOT NULL,
            raw_type TEXT NOT NULL,

            report_count INTEGER NOT NULL,
            first_seen TEXT,
            last_seen TEXT,

            resolution_status TEXT NOT NULL,

            PRIMARY KEY (
                raw_code,
                raw_type
            )
        )
        """
    )

    conn.commit()


def main():
    if not DB_PATH.exists():
        raise SystemExit(
            f"ERROR: DB not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    init_db(conn)

    now = datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")

    observations = conn.execute(
        """
        SELECT
            machine_code_raw,
            COALESCE(machine_type_raw, ''),
            COUNT(*) AS report_count,
            MIN(report_date),
            MAX(report_date)
        FROM daily_fault_reports
        WHERE
            machine_code_raw IS NOT NULL
            AND TRIM(machine_code_raw) <> ''
        GROUP BY
            machine_code_raw,
            machine_type_raw
        ORDER BY
            machine_code_raw,
            report_count DESC
        """
    ).fetchall()

    inserted_machines = 0
    inserted_aliases = 0

    unresolved_numeric = set()

    # برای انتخاب رایج‌ترین type هر code
    best_type = {}

    for (
        raw_code,
        raw_type,
        report_count,
        first_seen,
        last_seen,
    ) in observations:

        code = raw_code.strip()
        type_text = raw_type.strip()

        is_structured = bool(
            CODE_RE.fullmatch(code)
        )

        status = (
            "PROVISIONAL"
            if is_structured
            else "UNRESOLVED"
        )

        conn.execute(
            """
            INSERT INTO machine_identity_observations (
                raw_code,
                raw_type,
                report_count,
                first_seen,
                last_seen,
                resolution_status
            )
            VALUES (?, ?, ?, ?, ?, ?)

            ON CONFLICT(raw_code, raw_type)
            DO UPDATE SET
                report_count = excluded.report_count,
                first_seen = excluded.first_seen,
                last_seen = excluded.last_seen,
                resolution_status = excluded.resolution_status
            """,
            (
                code,
                type_text,
                report_count,
                first_seen,
                last_seen,
                status,
            ),
        )

        if not is_structured:
            unresolved_numeric.add(code)
            continue

        canonical = code.upper()

        if canonical not in best_type:
            best_type[canonical] = type_text

        existing = conn.execute(
            """
            SELECT id
            FROM machines
            WHERE canonical_code = ?
            """,
            (canonical,),
        ).fetchone()

        if existing:
            machine_id = existing[0]

        else:
            cur = conn.execute(
                """
                INSERT INTO machines (
                    canonical_code,
                    machine_type_hint,
                    model_key,
                    identity_status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, NULL, 'PROVISIONAL', ?, ?)
                """,
                (
                    canonical,
                    type_text,
                    now,
                    now,
                ),
            )

            machine_id = cur.lastrowid
            inserted_machines += 1

        cur = conn.execute(
            """
            INSERT OR IGNORE INTO machine_aliases (
                machine_id,
                alias_code,
                alias_type_raw,
                source_type,
                verified,
                created_at
            )
            VALUES (?, ?, ?, 'daily_fault_reports', 0, ?)
            """,
            (
                machine_id,
                code,
                type_text,
                now,
            ),
        )

        if cur.rowcount == 1:
            inserted_aliases += 1

    conn.commit()

    total_machines = conn.execute(
        """
        SELECT COUNT(*)
        FROM machines
        """
    ).fetchone()[0]

    total_aliases = conn.execute(
        """
        SELECT COUNT(*)
        FROM machine_aliases
        """
    ).fetchone()[0]

    conflicts = conn.execute(
        """
        SELECT
            raw_code,
            COUNT(DISTINCT raw_type)
        FROM machine_identity_observations
        GROUP BY raw_code
        HAVING COUNT(DISTINCT raw_type) > 1
        ORDER BY raw_code
        """
    ).fetchall()

    print()
    print("MACHINE IDENTITY V1 COMPLETE")
    print(f"Machines: {total_machines}")
    print(f"Aliases: {total_aliases}")
    print(f"Inserted machines: {inserted_machines}")
    print(f"Inserted aliases: {inserted_aliases}")

    print()
    print("UNRESOLVED CODES")

    if unresolved_numeric:
        for code in sorted(unresolved_numeric):
            print(f"  {code}")
    else:
        print("  None")

    print()
    print("TYPE CONFLICTS")

    if conflicts:
        for code, count in conflicts:
            print(
                f"  {code}: "
                f"{count} different type labels"
            )
    else:
        print("  None")

    print()
    print("SAMPLE MACHINES")

    rows = conn.execute(
        """
        SELECT
            canonical_code,
            machine_type_hint,
            model_key,
            identity_status
        FROM machines
        ORDER BY canonical_code
        LIMIT 20
        """
    ).fetchall()

    for row in rows:
        print(
            f"{row[0]} | "
            f"{row[1]} | "
            f"model={row[2]} | "
            f"{row[3]}"
        )

    conn.close()


if __name__ == "__main__":
    main()