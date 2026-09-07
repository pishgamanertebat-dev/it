from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)


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

    if len(rows) > 1:
        raise SystemExit(
            f"ERROR: ambiguous verified alias: {code}"
        )

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

    raise SystemExit(
        f"ERROR: machine not found: {code}"
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("machine")

    parser.add_argument(
        "--json",
        action="store_true",
        help="Return compact JSON for agent/tool use",
    )

    args = parser.parse_args()

    if not DB_PATH.exists():
        raise SystemExit(
            f"ERROR: DB not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)

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

    fleet_latest_date = conn.execute(
        """
        SELECT MAX(report_date)
        FROM daily_fault_reports
        """
    ).fetchone()[0]

    history = conn.execute(
        """
        SELECT
            COUNT(*),
            MIN(d.report_date),
            MAX(d.report_date)
        FROM daily_fault_reports d
        JOIN daily_fault_report_identity i
            ON i.daily_fault_report_id = d.id
        WHERE i.machine_id = ?
        """,
        (machine_id,),
    ).fetchone()

    total_reports = history[0]
    first_seen = history[1]
    last_seen = history[2]

    latest = conn.execute(
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
        LIMIT 1
        """,
        (machine_id,),
    ).fetchone()

    recent = conn.execute(
        """
        SELECT
            d.report_date,
            d.mechanical_raw,
            d.fabrication_raw,
            d.general_raw
        FROM daily_fault_reports d
        JOIN daily_fault_report_identity i
            ON i.daily_fault_report_id = d.id
        WHERE i.machine_id = ?
        ORDER BY
            d.report_date DESC,
            d.id DESC
        LIMIT 7
        """,
        (machine_id,),
    ).fetchall()

    reported_on_latest_fleet_date = (
        last_seen == fleet_latest_date
    )

    context = {
        "machine": {
            "canonical_code": canonical_code,
            "machine_type": machine_type,
            "model_key": model_key,
            "identity_status": identity_status,
            "resolved_via": resolved_via,
        },

        "fleet_freshness": {
            "fleet_latest_report_date": fleet_latest_date,
            "machine_latest_report_date": last_seen,
            "reported_on_latest_fleet_date":
                reported_on_latest_fleet_date,
        },

        "history": {
            "report_count": total_reports,
            "first_seen": first_seen,
            "last_seen": last_seen,
        },

        "latest_report": None,

        "recent_reports": [],
    }

    if latest:
        context["latest_report"] = {
            "date": latest[0],
            "raw_code": latest[1],
            "mechanical": latest[2],
            "fabrication": latest[3],
            "general": latest[4],
            "source_sheet": latest[5],
            "excel_row": latest[6],
        }

    for row in recent:
        context["recent_reports"].append(
            {
                "date": row[0],
                "mechanical": row[1],
                "fabrication": row[2],
                "general": row[3],
            }
        )

    if args.json:
        print(
            json.dumps(
                context,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    else:
        print("MACHINE CONTEXT")
        print("=" * 70)

        print(
            f"Machine: {canonical_code}"
        )
        print(
            f"Type: {machine_type}"
        )
        print(
            f"Model: {model_key}"
        )
        print(
            f"Identity: {identity_status}"
        )

        print()
        print("FLEET FRESHNESS")

        print(
            f"Fleet latest date: {fleet_latest_date}"
        )
        print(
            f"Machine latest date: {last_seen}"
        )
        print(
            "Reported on latest fleet date: "
            f"{reported_on_latest_fleet_date}"
        )

        print()
        print("HISTORY")

        print(
            f"Reports: {total_reports}"
        )
        print(
            f"First seen: {first_seen}"
        )
        print(
            f"Last seen: {last_seen}"
        )

        print()
        print("LATEST REPORT")

        if latest:
            print(
                f"Date: {latest[0]}"
            )

            if latest[2]:
                print(
                    f"Mechanical: {latest[2]}"
                )

            if latest[3]:
                print(
                    f"Fabrication: {latest[3]}"
                )

            if latest[4]:
                print(
                    f"General: {latest[4]}"
                )

        else:
            print("None")

    conn.close()


if __name__ == "__main__":
    main()