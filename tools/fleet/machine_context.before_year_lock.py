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

    # 1) Verified manual alias
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

    # 2) Canonical code
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


def issue_row_to_dict(row):
    return {
        "issue_family": row[0],
        "label": row[1],
        "first_seen": row[2],
        "last_seen": row[3],
        "days_detected": row[4],
        "mention_count": row[5],
        "repeated": bool(row[6]),
        "status": row[7],
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "machine",
        help="Canonical machine code or verified alias",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Compact JSON output for agent/tool use",
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

    # --------------------------------------------------
    # Fleet freshness
    # --------------------------------------------------

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

    reported_on_latest_fleet_date = (
        last_seen == fleet_latest_date
    )

    # --------------------------------------------------
    # Latest report
    # --------------------------------------------------

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

    # --------------------------------------------------
    # Small recent history
    # --------------------------------------------------

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

    # --------------------------------------------------
    # Current issue states
    # --------------------------------------------------

    issue_rows = conn.execute(
        """
        SELECT
            issue_family,
            issue_label,
            first_seen,
            last_seen,
            days_detected,
            mention_count,
            repeated,
            status

        FROM issue_state_current

        WHERE machine_id = ?

        ORDER BY
            CASE status
                WHEN 'OPEN' THEN 0
                WHEN 'NOT_REPORTED' THEN 1
                ELSE 2
            END,
            days_detected DESC,
            issue_family
        """,
        (machine_id,),
    ).fetchall()

    open_issues = []
    not_reported_issues = []

    for row in issue_rows:
        item = issue_row_to_dict(row)

        if row[7] == "OPEN":
            open_issues.append(item)

        elif row[7] == "NOT_REPORTED":
            not_reported_issues.append(item)

    # --------------------------------------------------
    # Agent context
    # --------------------------------------------------

    context = {
        "machine": {
            "canonical_code": canonical_code,
            "machine_type": machine_type,
            "model_key": model_key,
            "identity_status": identity_status,
            "resolved_via": resolved_via,
        },

        "fleet_freshness": {
            "fleet_latest_report_date":
                fleet_latest_date,

            "machine_latest_report_date":
                last_seen,

            "reported_on_latest_fleet_date":
                reported_on_latest_fleet_date,
        },

        "history": {
            "report_count": total_reports,
            "first_seen": first_seen,
            "last_seen": last_seen,
        },

        "issue_status_semantics": {
            "OPEN":
                "Issue family was detected on the latest fleet report date.",

            "NOT_REPORTED":
                "Issue family was seen previously but was not detected on the latest fleet report date. This does NOT confirm resolution.",
        },

        "open_issues": open_issues,

        "not_reported_issues":
            not_reported_issues,

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

    # --------------------------------------------------
    # JSON output
    # --------------------------------------------------

    if args.json:

        print(
            json.dumps(
                context,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    # --------------------------------------------------
    # Human-readable output
    # --------------------------------------------------

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
            f"Fleet latest date: "
            f"{fleet_latest_date}"
        )

        print(
            f"Machine latest date: "
            f"{last_seen}"
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

        print("OPEN ISSUE FAMILIES")
        print("-" * 70)

        if open_issues:

            for issue in open_issues:

                print(
                    f"{issue['issue_family']} | "
                    f"days={issue['days_detected']} | "
                    f"last={issue['last_seen']} | "
                    f"repeated={issue['repeated']}"
                )

                print(
                    f"  {issue['label']}"
                )

        else:
            print("None")

        print()

        print("NOT REPORTED ISSUE FAMILIES")
        print("-" * 70)

        if not_reported_issues:

            print(
                "NOTE: NOT_REPORTED does NOT mean RESOLVED."
            )

            for issue in not_reported_issues:

                print(
                    f"{issue['issue_family']} | "
                    f"days={issue['days_detected']} | "
                    f"last={issue['last_seen']} | "
                    f"repeated={issue['repeated']}"
                )

                print(
                    f"  {issue['label']}"
                )

        else:
            print("None")

        print()

        print("LATEST RAW REPORT")
        print("-" * 70)

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