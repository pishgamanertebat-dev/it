from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)


def resolve_machine(conn, code: str):
    code = code.strip()

    # اول Verified Alias
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

    if len(rows) > 1:
        raise SystemExit(
            f"ERROR: ambiguous verified alias: {code}"
        )

    # بعد Canonical Code
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

    if len(rows) > 1:
        raise SystemExit(
            f"ERROR: ambiguous canonical code: {code}"
        )

    # بعد هر Alias دیگری
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

        WHERE UPPER(a.alias_code) = UPPER(?)
        """,
        (code,),
    ).fetchall()

    if len(rows) == 1:
        return rows[0], "SOURCE_ALIAS"

    if len(rows) > 1:
        raise SystemExit(
            f"ERROR: ambiguous source alias: {code}"
        )

    raise SystemExit(
        f"ERROR: machine not found: {code}"
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "machine",
        help="Canonical code or known alias",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of latest reports",
    )

    args = parser.parse_args()

    if args.limit < 1 or args.limit > 100:
        raise SystemExit(
            "ERROR: --limit must be between 1 and 100"
        )

    if not DB_PATH.exists():
        raise SystemExit(
            f"ERROR: DB not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)

    machine, via = resolve_machine(
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

    print("MACHINE")
    print(f"Canonical: {canonical_code}")
    print(f"Type: {machine_type}")
    print(f"Model: {model_key}")
    print(f"Identity: {identity_status}")
    print(f"Resolved via: {via}")
    print()

    total_reports = conn.execute(
        """
        SELECT COUNT(*)

        FROM daily_fault_report_identity i

        WHERE i.machine_id = ?
        """,
        (machine_id,),
    ).fetchone()[0]

    first_last = conn.execute(
        """
        SELECT
            MIN(d.report_date),
            MAX(d.report_date)

        FROM daily_fault_reports d

        JOIN daily_fault_report_identity i
            ON i.daily_fault_report_id = d.id

        WHERE i.machine_id = ?
        """,
        (machine_id,),
    ).fetchone()

    print("HISTORY")
    print(f"Reports: {total_reports}")
    print(f"First seen: {first_last[0]}")
    print(f"Last seen: {first_last[1]}")
    print()

    rows = conn.execute(
        """
        SELECT
            d.report_date,
            d.machine_code_raw,
            d.mechanical_raw,
            d.fabrication_raw,
            d.general_raw,
            d.sheet_name,
            d.excel_row

        FROM daily_fault_reports d

        JOIN daily_fault_report_identity i
            ON i.daily_fault_report_id = d.id

        WHERE i.machine_id = ?

        ORDER BY
            d.report_date DESC,
            d.id DESC

        LIMIT ?
        """,
        (
            machine_id,
            args.limit,
        ),
    ).fetchall()

    print(
        f"LATEST {len(rows)} REPORTS"
    )

    for row in rows:
        (
            report_date,
            raw_code,
            mechanical,
            fabrication,
            general,
            sheet_name,
            excel_row,
        ) = row

        print("=" * 70)
        print(f"Date: {report_date}")
        print(f"Raw code: {raw_code}")

        if mechanical:
            print(
                f"Mechanical: {mechanical}"
            )

        if fabrication:
            print(
                f"Fabrication: {fabrication}"
            )

        if general:
            print(
                f"General: {general}"
            )

        print(
            f"Source: {sheet_name} / row {excel_row}"
        )

    conn.close()


if __name__ == "__main__":
    main()