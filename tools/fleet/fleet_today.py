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


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--json",
        action="store_true",
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

    year_pattern = f"{operational_year}/%"

    latest_date = conn.execute(
        """
        SELECT MAX(report_date)
        FROM daily_fault_reports
        WHERE report_date LIKE ?
        """,
        (year_pattern,),
    ).fetchone()[0]

    maintenance_latest = conn.execute(
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

    maintenance_behind = bool(
        maintenance_latest
        and latest_date
        and maintenance_latest < latest_date
    )

    # -----------------------------------------------
    # Latest report row for every machine
    # reported on the fleet latest date
    # -----------------------------------------------

    rows = conn.execute(
        """
        SELECT
            d.id,
            i.machine_id,
            m.canonical_code,
            m.machine_type_hint,
            m.model_key,
            m.identity_status,

            d.machine_code_raw,
            d.mechanical_raw,
            d.fabrication_raw,
            d.general_raw,
            d.sheet_name,
            d.excel_row

        FROM daily_fault_reports d

        JOIN daily_fault_report_identity i
            ON i.daily_fault_report_id = d.id

        JOIN machines m
            ON m.id = i.machine_id

        WHERE d.report_date = ?

        ORDER BY
            m.canonical_code,
            d.id DESC
        """,
        (latest_date,),
    ).fetchall()

    # Prevent duplicate machine rows if source ever
    # contains more than one row for same device/day.
    latest_by_machine = {}

    for row in rows:
        machine_id = row[1]

        if machine_id not in latest_by_machine:
            latest_by_machine[machine_id] = row

    fleet = []

    for machine_id, row in latest_by_machine.items():

        report_id = row[0]

        issue_rows = conn.execute(
            """
            SELECT
                issue_family,
                issue_label,
                days_detected,
                repeated

            FROM issue_state_current

            WHERE
                machine_id = ?
                AND status = 'OPEN'

            ORDER BY
                days_detected DESC,
                issue_family
            """,
            (machine_id,),
        ).fetchall()

        open_issues = [
            {
                "family": issue[0],
                "label": issue[1],
                "days_detected": issue[2],
                "repeated": bool(issue[3]),
            }
            for issue in issue_rows
        ]

        unclassified_count = conn.execute(
            """
            SELECT COUNT(*)

            FROM issue_mentions

            WHERE
                daily_fault_report_id = ?
                AND classification_status = 'UNCLASSIFIED'
            """,
            (report_id,),
        ).fetchone()[0]

        classified_count = conn.execute(
            """
            SELECT COUNT(*)

            FROM issue_mentions

            WHERE
                daily_fault_report_id = ?
                AND classification_status = 'CLASSIFIED'
            """,
            (report_id,),
        ).fetchone()[0]

        fleet.append(
            {
                "machine": row[2],
                "type": row[3],
                "model": row[4],
                "identity_status": row[5],

                "latest_raw_report": {
                    "raw_code": row[6],
                    "mechanical": row[7],
                    "fabrication": row[8],
                    "general": row[9],
                    "source_sheet": row[10],
                    "excel_row": row[11],
                },

                "open_issue_families": open_issues,

                "classification_coverage": {
                    "classified_fragments":
                        classified_count,

                    "unclassified_fragments":
                        unclassified_count,

                    "classifier_is_partial":
                        True,
                },
            }
        )

    # -----------------------------------------------
    # How many machines had reports in 1405 but
    # were NOT reported on latest fleet date?
    # This is NOT resolution.
    # -----------------------------------------------

    not_reported_count = conn.execute(
        """
        SELECT COUNT(*)

        FROM (
            SELECT i.machine_id

            FROM daily_fault_reports d

            JOIN daily_fault_report_identity i
                ON i.daily_fault_report_id = d.id

            WHERE d.report_date LIKE ?

            GROUP BY i.machine_id

            HAVING MAX(d.report_date) < ?
        )
        """,
        (
            year_pattern,
            latest_date,
        ),
    ).fetchone()[0]

    result = {
        "context_policy": {
            "operational_year":
                operational_year,

            "older_years_loaded":
                False,

            "historical_data_policy":
                "Older years are excluded from normal operational reasoning."
        },

        "fleet_date":
            latest_date,

        "fleet_summary": {
            "machines_reported_on_latest_date":
                len(fleet),

            "machines_with_1405_history_but_not_latest_date":
                not_reported_count,
        },

        "maintenance_freshness": {
            "maintenance_source_latest_event_date":
                maintenance_latest,

            "maintenance_source_behind_fault_data":
                maintenance_behind,
        },

        "classifier_semantics": {
            "classifier_is_partial":
                True,

            "important":
                "Open issue families are supplemental. Always inspect latest_raw_report because some faults are still unclassified."
        },

        "machines":
            fleet,
    }

    if args.json:

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    else:

        print("FLEET TODAY")
        print("=" * 70)

        print(
            f"Operational year: "
            f"{operational_year}"
        )

        print(
            "Older years loaded: False"
        )

        print(
            f"Fleet date: {latest_date}"
        )

        print()

        print("SUMMARY")

        print(
            "Machines reported on latest date: "
            f"{len(fleet)}"
        )

        print(
            "Machines with 1405 history but "
            "not reported on latest date: "
            f"{not_reported_count}"
        )

        print()

        print("MAINTENANCE FRESHNESS")

        print(
            "Maintenance source latest date: "
            f"{maintenance_latest}"
        )

        print(
            "Maintenance source behind fault data: "
            f"{maintenance_behind}"
        )

        if maintenance_behind:
            print(
                "WARNING: maintenance data is older "
                "than latest fault data."
            )

        print()
        print("IMPORTANT")

        print(
            "Issue classification is PARTIAL. "
            "The raw latest report must also be read."
        )

        print()
        print("MACHINES")
        print("=" * 70)

        for item in fleet:

            print()
            print("#" * 70)

            print(
                f"{item['machine']} | "
                f"{item['model']} | "
                f"{item['type']}"
            )

            issues = item[
                "open_issue_families"
            ]

            if issues:

                print("Open classified issues:")

                for issue in issues:

                    print(
                        f"  - {issue['family']} | "
                        f"days={issue['days_detected']} | "
                        f"repeated={issue['repeated']}"
                    )

            else:
                print(
                    "Open classified issues: None"
                )

            coverage = item[
                "classification_coverage"
            ]

            print(
                "Fragments: "
                f"classified="
                f"{coverage['classified_fragments']}, "
                f"unclassified="
                f"{coverage['unclassified_fragments']}"
            )

            raw = item[
                "latest_raw_report"
            ]

            if raw["mechanical"]:
                print(
                    f"Mechanical: "
                    f"{raw['mechanical']}"
                )

            if raw["fabrication"]:
                print(
                    f"Fabrication: "
                    f"{raw['fabrication']}"
                )

            if raw["general"]:
                print(
                    f"General: "
                    f"{raw['general']}"
                )

    conn.close()


if __name__ == "__main__":
    main()