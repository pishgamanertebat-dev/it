from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)


def get_operational_year(conn):
    row = conn.execute(
        """
        SELECT setting_value
        FROM fleet_settings
        WHERE setting_key = 'operational_jalali_year'
        """
    ).fetchone()

    if not row:
        raise SystemExit(
            "ERROR: operational_jalali_year not configured"
        )

    return row[0]


def resolve_machine(conn, code):
    code = code.strip()

    rows = conn.execute(
        """
        SELECT DISTINCT
            m.id,
            m.canonical_code,
            m.machine_type_hint,
            m.model_key,
            m.identity_status

        FROM machine_aliases a

        JOIN machines m
            ON m.id = a.machine_id

        WHERE
            UPPER(a.alias_code) = UPPER(?)
            AND a.verified = 1
        """,
        (code,),
    ).fetchall()

    if len(rows) == 1:
        return rows[0], "VERIFIED_ALIAS"

    rows = conn.execute(
        """
        SELECT
            id,
            canonical_code,
            machine_type_hint,
            model_key,
            identity_status

        FROM machines

        WHERE UPPER(canonical_code) = UPPER(?)
        """,
        (code,),
    ).fetchall()

    if len(rows) == 1:
        return rows[0], "CANONICAL_CODE"

    raise SystemExit(
        f"ERROR: machine not found or ambiguous: {code}"
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "machine"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--year",
        help=(
            "Explicit Jalali year. "
            "If omitted, operational year is used."
        ),
    )

    args = parser.parse_args()

    if args.limit < 1 or args.limit > 100:
        raise SystemExit(
            "ERROR: --limit must be between 1 and 100"
        )

    conn = sqlite3.connect(DB_PATH)

    operational_year = get_operational_year(
        conn
    )

    selected_year = (
        args.year
        if args.year
        else operational_year
    )

    explicit_historical_request = (
        args.year is not None
        and selected_year != operational_year
    )

    year_pattern = (
        f"{selected_year}/%"
    )

    machine, resolved_via = resolve_machine(
        conn,
        args.machine,
    )

    (
        machine_id,
        canonical_code,
        machine_type,
        model_key,
        identity_status,
    ) = machine

    print("MAINTENANCE HISTORY")
    print("=" * 70)

    print(
        f"Machine: {canonical_code}"
    )

    print(
        f"Model: {model_key}"
    )

    print(
        f"Resolved via: {resolved_via}"
    )

    print()
    print("CONTEXT POLICY")

    print(
        f"Operational year: {operational_year}"
    )

    print(
        f"Selected year: {selected_year}"
    )

    print(
        "Explicit historical request: "
        f"{explicit_historical_request}"
    )

    count = conn.execute(
        """
        SELECT COUNT(*)

        FROM maintenance_event_identity i

        JOIN maintenance_events e
            ON e.id = i.maintenance_event_id

        WHERE
            i.machine_id = ?
            AND i.resolution_status = 'LINKED'
            AND e.event_date_raw LIKE ?
        """,
        (
            machine_id,
            year_pattern,
        ),
    ).fetchone()[0]

    first_last = conn.execute(
        """
        SELECT
            MIN(e.event_date_raw),
            MAX(e.event_date_raw)

        FROM maintenance_event_identity i

        JOIN maintenance_events e
            ON e.id = i.maintenance_event_id

        WHERE
            i.machine_id = ?
            AND i.resolution_status = 'LINKED'
            AND e.event_date_raw LIKE ?
        """,
        (
            machine_id,
            year_pattern,
        ),
    ).fetchone()

    print()
    print(f"HISTORY — {selected_year}")

    print(
        f"Maintenance events: {count}"
    )

    print(
        f"First event: {first_last[0]}"
    )

    print(
        f"Last event: {first_last[1]}"
    )

    rows = conn.execute(
        """
        SELECT
            e.event_date_raw,
            e.mechanic_raw,
            e.action_raw,
            e.parts_raw,
            e.sheet_name,
            e.excel_row

        FROM maintenance_event_identity i

        JOIN maintenance_events e
            ON e.id = i.maintenance_event_id

        WHERE
            i.machine_id = ?
            AND i.resolution_status = 'LINKED'
            AND e.event_date_raw LIKE ?

        ORDER BY
            e.event_date_raw DESC,
            e.id DESC

        LIMIT ?
        """,
        (
            machine_id,
            year_pattern,
            args.limit,
        ),
    ).fetchall()

    print()
    print(
        f"LATEST {len(rows)} MAINTENANCE EVENTS "
        f"IN {selected_year}"
    )

    if not rows:
        print("None")

    for row in rows:

        print("=" * 70)

        print(
            f"Date: {row[0]}"
        )

        if row[1]:
            print(
                f"Mechanic: {row[1]}"
            )

        if row[2]:
            print(
                f"Action: {row[2]}"
            )

        if row[3]:
            print(
                f"Parts: {row[3]}"
            )

        print(
            f"Source: {row[4]} / row {row[5]}"
        )

    print()
    print("IMPORTANT")

    print(
        "A maintenance action is evidence of work performed. "
        "It does NOT by itself prove root cause or issue resolution."
    )

    conn.close()


if __name__ == "__main__":
    main()