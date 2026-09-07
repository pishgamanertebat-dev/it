from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)

OPERATIONAL_YEAR = "1405"
STATE_VERSION = "issue_state_v1_year_filtered"


def main():
    if not DB_PATH.exists():
        raise SystemExit(
            f"ERROR: DB not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    now = datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")

    with conn:
        conn.execute(
            """
            INSERT INTO fleet_settings (
                setting_key,
                setting_value,
                updated_at
            )
            VALUES (
                'operational_jalali_year',
                ?,
                ?
            )

            ON CONFLICT(setting_key)
            DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = excluded.updated_at
            """,
            (
                OPERATIONAL_YEAR,
                now,
            ),
        )

    fleet_latest_date = conn.execute(
        """
        SELECT MAX(report_date)
        FROM daily_fault_reports
        WHERE report_date LIKE ?
        """,
        (f"{OPERATIONAL_YEAR}/%",),
    ).fetchone()[0]

    rows = conn.execute(
        """
        SELECT
            machine_id,
            issue_family,
            MAX(issue_label),
            MIN(report_date),
            MAX(report_date),
            COUNT(DISTINCT report_date),
            COUNT(*)

        FROM issue_mentions

        WHERE
            classification_status = 'CLASSIFIED'
            AND issue_family IS NOT NULL
            AND report_date LIKE ?

        GROUP BY
            machine_id,
            issue_family
        """,
        (f"{OPERATIONAL_YEAR}/%",),
    ).fetchall()

    # Derived table: safe to rebuild.
    with conn:
        conn.execute(
            "DELETE FROM issue_state_current"
        )

        for (
            machine_id,
            issue_family,
            issue_label,
            first_seen,
            last_seen,
            days_detected,
            mention_count,
        ) in rows:

            repeated = int(
                days_detected >= 2
            )

            present_latest = int(
                last_seen == fleet_latest_date
            )

            status = (
                "OPEN"
                if present_latest
                else "NOT_REPORTED"
            )

            conn.execute(
                """
                INSERT INTO issue_state_current (
                    machine_id,
                    issue_family,
                    issue_label,
                    first_seen,
                    last_seen,
                    days_detected,
                    mention_count,
                    repeated,
                    present_on_latest_fleet_date,
                    status,
                    fleet_latest_report_date,
                    state_version,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    machine_id,
                    issue_family,
                    issue_label,
                    first_seen,
                    last_seen,
                    days_detected,
                    mention_count,
                    repeated,
                    present_latest,
                    status,
                    fleet_latest_date,
                    STATE_VERSION,
                    now,
                ),
            )

    print("OPERATIONAL YEAR POLICY APPLIED")
    print(f"Operational year: {OPERATIONAL_YEAR}")
    print(f"Fleet latest date: {fleet_latest_date}")
    print(f"Operational issue states: {len(rows)}")

    print()
    print("HD714")

    result = conn.execute(
        """
        SELECT
            s.issue_family,
            s.first_seen,
            s.last_seen,
            s.days_detected,
            s.status

        FROM issue_state_current s

        JOIN machines m
            ON m.id = s.machine_id

        WHERE m.canonical_code = 'HD714'

        ORDER BY s.days_detected DESC
        """
    ).fetchall()

    for row in result:
        print(
            f"{row[0]} | "
            f"{row[1]} -> {row[2]} | "
            f"days={row[3]} | "
            f"{row[4]}"
        )

    print()
    print("HD702")

    result = conn.execute(
        """
        SELECT
            s.issue_family,
            s.first_seen,
            s.last_seen,
            s.days_detected,
            s.status

        FROM issue_state_current s

        JOIN machines m
            ON m.id = s.machine_id

        WHERE m.canonical_code = 'HD702'

        ORDER BY s.days_detected DESC
        """
    ).fetchall()

    for row in result:
        print(
            f"{row[0]} | "
            f"{row[1]} -> {row[2]} | "
            f"days={row[3]} | "
            f"{row[4]}"
        )

    conn.close()


if __name__ == "__main__":
    main()