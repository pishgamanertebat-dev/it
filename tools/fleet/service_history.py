import argparse
import json
import sqlite3
from collections import defaultdict


DB = r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"


def get_operational_year(cur):
    row = cur.execute("""
        SELECT setting_value
        FROM fleet_settings
        WHERE setting_key='operational_jalali_year'
    """).fetchone()

    return int(row[0]) if row else 1405


def normalize_code(value):
    return str(value).strip().upper()


def main():

    parser = argparse.ArgumentParser(
        description="Fleet service history"
    )

    parser.add_argument(
        "machine_code",
        help="Canonical machine code, e.g. HD714"
    )

    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Jalali year. Default = operational year."
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=15,
        help="Number of recent shift/service records."
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Return JSON output."
    )

    args = parser.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    operational_year = get_operational_year(cur)

    year = (
        args.year
        if args.year is not None
        else operational_year
    )

    requested_code = normalize_code(
        args.machine_code
    )

    machine = cur.execute("""
        SELECT
            id,
            canonical_code,
            machine_type_hint,
            model_key,
            identity_status
        FROM machines
        WHERE UPPER(canonical_code)=?
        LIMIT 1
    """, (
        requested_code,
    )).fetchone()

    if not machine:

        result = {
            "status": "NOT_FOUND",
            "requested_code": requested_code,
        }

        if args.json:
            print(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2
                )
            )
        else:
            print(
                f"MACHINE NOT FOUND: "
                f"{requested_code}"
            )

        conn.close()
        return


    canonical_code = machine[
        "canonical_code"
    ]


    # ========================================================
    # Service snapshot
    # ========================================================

    snapshot = cur.execute("""
        SELECT
            jalali_asof_date,
            next_service_hour,
            current_meter_hour,
            remaining_hours,
            pm_issue_raw,
            source_sheet,
            source_row
        FROM service_pm_snapshots
        WHERE UPPER(canonical_code)=?
          AND (
              jalali_asof_date LIKE ?
              OR jalali_asof_date IS NULL
          )
        ORDER BY
            CASE
                WHEN jalali_asof_date IS NULL
                THEN 1
                ELSE 0
            END,
            jalali_asof_date DESC,
            id DESC
        LIMIT 1
    """, (
        canonical_code.upper(),
        f"{year}/%",
    )).fetchone()


    # ========================================================
    # All numeric work hours for year
    # ========================================================

    summary = cur.execute("""
        SELECT
            COUNT(*) AS record_count,
            COUNT(work_hours) AS numeric_count,
            SUM(work_hours) AS total_hours,
            MIN(jalali_date) AS first_date,
            MAX(jalali_date) AS last_date
        FROM service_shift_hours
        WHERE UPPER(canonical_code)=?
          AND jalali_date LIKE ?
    """, (
        canonical_code.upper(),
        f"{year}/%",
    )).fetchone()


    # ========================================================
    # Recent service/hour records
    # ========================================================

    recent_rows = cur.execute("""
        SELECT
            jalali_date,
            shift,
            work_hours,
            note,
            raw_value
        FROM service_shift_hours
        WHERE UPPER(canonical_code)=?
          AND jalali_date LIKE ?
        ORDER BY
            jalali_date DESC,
            CASE
                WHEN shift='شب' THEN 0
                WHEN shift='روز' THEN 1
                ELSE 2
            END,
            id DESC
        LIMIT ?
    """, (
        canonical_code.upper(),
        f"{year}/%",
        args.limit,
    )).fetchall()


    # ========================================================
    # Daily totals
    # ========================================================

    daily_rows = cur.execute("""
        SELECT
            jalali_date,
            SUM(work_hours) AS total_hours
        FROM service_shift_hours
        WHERE UPPER(canonical_code)=?
          AND jalali_date LIKE ?
          AND work_hours IS NOT NULL
        GROUP BY jalali_date
        ORDER BY jalali_date DESC
        LIMIT 10
    """, (
        canonical_code.upper(),
        f"{year}/%",
    )).fetchall()


    # ========================================================
    # Notes only
    # ========================================================

    notes = cur.execute("""
        SELECT
            jalali_date,
            shift,
            note
        FROM service_shift_hours
        WHERE UPPER(canonical_code)=?
          AND jalali_date LIKE ?
          AND note IS NOT NULL
          AND TRIM(note) != ''
          AND TRIM(note) != '-'
        ORDER BY
            jalali_date DESC,
            id DESC
        LIMIT 10
    """, (
        canonical_code.upper(),
        f"{year}/%",
    )).fetchall()


    result = {
        "status": "OK",

        "machine": {
            "canonical_code":
                canonical_code,

            "machine_type_hint":
                machine[
                    "machine_type_hint"
                ],

            "model_key":
                machine[
                    "model_key"
                ],

            "identity_status":
                machine[
                    "identity_status"
                ],
        },

        "service_policy": {
            "operational_year":
                operational_year,

            "requested_year":
                year,

            "older_year_explicit":
                year != operational_year,
        },

        "service_status": (
            {
                "as_of":
                    snapshot[
                        "jalali_asof_date"
                    ],

                "next_service_hour":
                    snapshot[
                        "next_service_hour"
                    ],

                "current_meter_hour":
                    snapshot[
                        "current_meter_hour"
                    ],

                "remaining_hours":
                    snapshot[
                        "remaining_hours"
                    ],

                "pm_issue":
                    snapshot[
                        "pm_issue_raw"
                    ],
            }
            if snapshot
            else None
        ),

        "year_summary": {
            "record_count":
                summary[
                    "record_count"
                ],

            "numeric_records":
                summary[
                    "numeric_count"
                ],

            "total_work_hours":
                summary[
                    "total_hours"
                ],

            "first_date":
                summary[
                    "first_date"
                ],

            "last_date":
                summary[
                    "last_date"
                ],
        },

        "recent_records": [
            {
                "date":
                    row[
                        "jalali_date"
                    ],

                "shift":
                    row[
                        "shift"
                    ],

                "work_hours":
                    row[
                        "work_hours"
                    ],

                "note":
                    row[
                        "note"
                    ],

                "raw_value":
                    row[
                        "raw_value"
                    ],
            }
            for row in recent_rows
        ],

        "recent_daily_totals": [
            {
                "date":
                    row[
                        "jalali_date"
                    ],

                "hours":
                    row[
                        "total_hours"
                    ],
            }
            for row in daily_rows
        ],

        "recent_notes": [
            {
                "date":
                    row[
                        "jalali_date"
                    ],

                "shift":
                    row[
                        "shift"
                    ],

                "note":
                    row[
                        "note"
                    ],
            }
            for row in notes
        ],
    }


    if args.json:

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2
            )
        )

    else:

        print(
            "SERVICE HISTORY"
        )
        print(
            "=" * 72
        )

        print(
            f"Machine: "
            f"{canonical_code}"
        )

        print(
            f"Type: "
            f"{machine['machine_type_hint']}"
        )

        print(
            f"Year: {year}"
        )

        print()

        print(
            "SERVICE STATUS"
        )
        print(
            "-" * 72
        )

        if snapshot:

            print(
                f"As of: "
                f"{snapshot['jalali_asof_date']}"
            )

            print(
                f"Next service hour: "
                f"{snapshot['next_service_hour']}"
            )

            print(
                f"Current meter: "
                f"{snapshot['current_meter_hour']}"
            )

            print(
                f"Remaining hours: "
                f"{snapshot['remaining_hours']}"
            )

            print(
                f"PM status: "
                f"{snapshot['pm_issue_raw']}"
            )

        else:

            print(
                "No service snapshot "
                "for requested year."
            )


        print()
        print(
            "YEAR WORK-HOUR SUMMARY"
        )
        print(
            "-" * 72
        )

        print(
            f"Date range: "
            f"{summary['first_date']} "
            f"-> "
            f"{summary['last_date']}"
        )

        print(
            f"Records: "
            f"{summary['record_count']}"
        )

        total = summary[
            "total_hours"
        ]

        print(
            f"Total numeric work hours: "
            f"{total}"
        )


        print()
        print(
            f"RECENT RECORDS "
            f"(limit={args.limit})"
        )
        print(
            "-" * 72
        )

        for row in recent_rows:

            if row[
                "work_hours"
            ] is not None:

                value = (
                    f"{row['work_hours']} h"
                )

            else:

                value = (
                    row["note"]
                    or row["raw_value"]
                )

            print(
                f"{row['jalali_date']} | "
                f"{row['shift']} | "
                f"{value}"
            )


        print()
        print(
            "RECENT DAILY TOTALS"
        )
        print(
            "-" * 72
        )

        for row in daily_rows:

            print(
                f"{row['jalali_date']} | "
                f"{row['total_hours']} h"
            )


        if notes:

            print()
            print(
                "SERVICE-SOURCE NOTES"
            )
            print(
                "-" * 72
            )

            for row in notes:

                print(
                    f"{row['jalali_date']} | "
                    f"{row['shift']} | "
                    f"{row['note']}"
                )

            print()
            print(
                "NOTE: These are raw notes "
                "from the service/hour source. "
                "They are not verified repair "
                "events."
            )


    conn.close()


if __name__ == "__main__":
    main()