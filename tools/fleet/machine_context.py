from __future__ import annotations

import argparse
import json
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
        "machine"
    )

    parser.add_argument(
        "--json",
        action="store_true"
    )

    args = parser.parse_args()

    if not DB_PATH.exists():
        raise SystemExit(
            f"ERROR: DB not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)

    operational_year = get_operational_year(
        conn
    )

    year_pattern = (
        f"{operational_year}/%"
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

    # ==================================================
    # DAILY FAULT DATA — operational year only
    # ==================================================

    fleet_latest_date = conn.execute(
        """
        SELECT MAX(report_date)

        FROM daily_fault_reports

        WHERE report_date LIKE ?
        """,
        (year_pattern,),
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

        WHERE
            i.machine_id = ?
            AND d.report_date LIKE ?
        """,
        (
            machine_id,
            year_pattern,
        ),
    ).fetchone()

    total_reports = history[0]
    first_seen = history[1]
    last_seen = history[2]

    reported_on_latest_fleet_date = (
        last_seen == fleet_latest_date
    )

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

        WHERE
            i.machine_id = ?
            AND d.report_date LIKE ?

        ORDER BY
            d.report_date DESC,
            d.id DESC

        LIMIT 1
        """,
        (
            machine_id,
            year_pattern,
        ),
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

        WHERE
            i.machine_id = ?
            AND d.report_date LIKE ?

        ORDER BY
            d.report_date DESC,
            d.id DESC

        LIMIT 7
        """,
        (
            machine_id,
            year_pattern,
        ),
    ).fetchall()

    # ==================================================
    # CURRENT ISSUE STATE
    # Already built using operational-year policy
    # ==================================================

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
            days_detected DESC
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

    # ==================================================
    # MAINTENANCE DATA — operational year only
    # ==================================================

    maintenance_source_latest = conn.execute(
        """
        SELECT MAX(e.event_date_raw)

        FROM maintenance_event_identity i

        JOIN maintenance_events e
            ON e.id = i.maintenance_event_id

        WHERE
            i.resolution_status = 'LINKED'
            AND e.event_date_raw LIKE ?
        """,
        (year_pattern,),
    ).fetchone()[0]

    machine_maintenance_stats = conn.execute(
        """
        SELECT
            COUNT(*),
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

    maintenance_count = (
        machine_maintenance_stats[0]
    )

    machine_maintenance_first = (
        machine_maintenance_stats[1]
    )

    machine_maintenance_latest = (
        machine_maintenance_stats[2]
    )

    maintenance_is_behind_fault_data = False

    if (
        maintenance_source_latest
        and fleet_latest_date
    ):
        maintenance_is_behind_fault_data = (
            maintenance_source_latest
            < fleet_latest_date
        )

    recent_maintenance = conn.execute(
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

        LIMIT 5
        """,
        (
            machine_id,
            year_pattern,
        ),
    ).fetchall()

    # ==================================================
    # AGENT CONTEXT
    # ==================================================

    context = {
        "context_policy": {
            "operational_year":
                operational_year,

            "older_years_loaded":
                False,

            "historical_data_policy":
                "Older years are excluded from normal operational reasoning and must be requested explicitly."
        },

        "machine": {
            "canonical_code":
                canonical_code,

            "machine_type":
                machine_type,

            "model_key":
                model_key,

            "identity_status":
                identity_status,

            "resolved_via":
                resolved_via,
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
            "year":
                operational_year,

            "report_count":
                total_reports,

            "first_seen":
                first_seen,

            "last_seen":
                last_seen,
        },

        "issue_status_semantics": {
            "OPEN":
                "Issue family was detected on the latest fleet report date.",

            "NOT_REPORTED":
                "Issue family was seen previously in the operational year but not detected on the latest fleet report date. This does NOT confirm resolution.",
        },

        "open_issues":
            open_issues,

        "not_reported_issues":
            not_reported_issues,

        "maintenance_freshness": {
            "maintenance_source_latest_event_date":
                maintenance_source_latest,

            "machine_latest_maintenance_date":
                machine_maintenance_latest,

            "maintenance_source_behind_fault_data":
                maintenance_is_behind_fault_data,
        },

        "maintenance_history": {
            "year":
                operational_year,

            "event_count":
                maintenance_count,

            "first_event":
                machine_maintenance_first,

            "last_event":
                machine_maintenance_latest,
        },

        "maintenance_semantics": {
            "action_is_evidence_of_work":
                True,

            "action_proves_root_cause":
                False,

            "action_proves_resolution":
                False,

            "absence_of_maintenance_record_proves_no_repair":
                False,
        },

        "latest_report":
            None,

        "recent_reports":
            [],

        "recent_maintenance_events":
            [],
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
        context[
            "recent_reports"
        ].append(
            {
                "date": row[0],
                "mechanical": row[1],
                "fabrication": row[2],
                "general": row[3],
            }
        )

    for row in recent_maintenance:
        context[
            "recent_maintenance_events"
        ].append(
            {
                "date": row[0],
                "mechanic": row[1],
                "action": row[2],
                "parts": row[3],
                "source_sheet": row[4],
                "excel_row": row[5],
            }
        )

    # ==================================================
    # JSON
    # ==================================================

    if args.json:

        print(
            json.dumps(
                context,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    # ==================================================
    # HUMAN OUTPUT
    # ==================================================

    else:

        print("MACHINE CONTEXT")
        print("=" * 70)

        print(
            f"Operational year: "
            f"{operational_year}"
        )

        print(
            "Older years loaded: False"
        )

        print()

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
        print(
            f"HISTORY — {operational_year}"
        )

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
        print(
            "NOT REPORTED ISSUE FAMILIES"
        )
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
        print("MAINTENANCE FRESHNESS")
        print("-" * 70)

        print(
            "Maintenance source latest date: "
            f"{maintenance_source_latest}"
        )

        print(
            "Machine latest maintenance date: "
            f"{machine_maintenance_latest}"
        )

        print(
            "Maintenance source behind fault data: "
            f"{maintenance_is_behind_fault_data}"
        )

        if maintenance_is_behind_fault_data:
            print(
                "WARNING: maintenance data is older than "
                "the latest fault-report data. "
                "Do not infer that a repair did not occur "
                "after the maintenance source's latest date."
            )

        print()
        print(
            f"RECENT MAINTENANCE — {operational_year}"
        )
        print("-" * 70)

        if recent_maintenance:

            for row in recent_maintenance:

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

                print()

        else:
            print("None")

        print(
            "NOTE: A maintenance action proves work was recorded; "
            "it does NOT by itself prove root cause or resolution."
        )

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