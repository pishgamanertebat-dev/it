from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)

STATE_VERSION = "issue_state_v1"


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS issue_state_current (
            machine_id INTEGER NOT NULL,
            issue_family TEXT NOT NULL,

            issue_label TEXT NOT NULL,

            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,

            days_detected INTEGER NOT NULL,
            mention_count INTEGER NOT NULL,

            repeated INTEGER NOT NULL,
            present_on_latest_fleet_date INTEGER NOT NULL,

            status TEXT NOT NULL,

            fleet_latest_report_date TEXT NOT NULL,

            state_version TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            PRIMARY KEY (
                machine_id,
                issue_family
            ),

            FOREIGN KEY (machine_id)
                REFERENCES machines(id)
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_issue_state_status
        ON issue_state_current(status)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_issue_state_machine
        ON issue_state_current(machine_id)
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

    fleet_latest_date = conn.execute(
        """
        SELECT MAX(report_date)
        FROM daily_fault_reports
        """
    ).fetchone()[0]

    if not fleet_latest_date:
        raise SystemExit(
            "ERROR: fleet latest report date not found"
        )

    rows = conn.execute(
        """
        SELECT
            machine_id,
            issue_family,
            MAX(issue_label) AS issue_label,

            MIN(report_date) AS first_seen,
            MAX(report_date) AS last_seen,

            COUNT(DISTINCT report_date) AS days_detected,
            COUNT(*) AS mention_count

        FROM issue_mentions

        WHERE
            classification_status = 'CLASSIFIED'
            AND issue_family IS NOT NULL

        GROUP BY
            machine_id,
            issue_family

        ORDER BY
            machine_id,
            issue_family
        """
    ).fetchall()

    now = datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")

    inserted = 0
    updated = 0
    unchanged = 0

    status_counts = {
        "OPEN": 0,
        "NOT_REPORTED": 0,
    }

    with conn:

        for (
            machine_id,
            issue_family,
            issue_label,
            first_seen,
            last_seen,
            days_detected,
            mention_count,
        ) in rows:

            repeated = (
                1
                if days_detected >= 2
                else 0
            )

            present_latest = (
                1
                if last_seen == fleet_latest_date
                else 0
            )

            status = (
                "OPEN"
                if present_latest
                else "NOT_REPORTED"
            )

            status_counts[status] += 1

            new_values = (
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
            )

            existing = conn.execute(
                """
                SELECT
                    issue_label,
                    first_seen,
                    last_seen,
                    days_detected,
                    mention_count,
                    repeated,
                    present_on_latest_fleet_date,
                    status,
                    fleet_latest_report_date,
                    state_version

                FROM issue_state_current

                WHERE
                    machine_id = ?
                    AND issue_family = ?
                """,
                (
                    machine_id,
                    issue_family,
                ),
            ).fetchone()

            if existing is None:

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

                inserted += 1

            elif existing == new_values:

                unchanged += 1

            else:

                conn.execute(
                    """
                    UPDATE issue_state_current

                    SET
                        issue_label = ?,
                        first_seen = ?,
                        last_seen = ?,
                        days_detected = ?,
                        mention_count = ?,
                        repeated = ?,
                        present_on_latest_fleet_date = ?,
                        status = ?,
                        fleet_latest_report_date = ?,
                        state_version = ?,
                        updated_at = ?

                    WHERE
                        machine_id = ?
                        AND issue_family = ?
                    """,
                    (
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
                        machine_id,
                        issue_family,
                    ),
                )

                updated += 1

    print()
    print("ISSUE STATE V1 COMPLETE")
    print(
        f"Fleet latest date: "
        f"{fleet_latest_date}"
    )
    print(
        f"Machine/issue states: "
        f"{len(rows)}"
    )
    print(f"Inserted: {inserted}")
    print(f"Updated: {updated}")
    print(f"Unchanged: {unchanged}")

    print()
    print("STATUS COUNTS")

    for status, count in status_counts.items():
        print(
            f"  {status}: {count}"
        )

    print()
    print("HD714 CURRENT ISSUES")

    hd714 = conn.execute(
        """
        SELECT id
        FROM machines
        WHERE canonical_code = 'HD714'
        """
    ).fetchone()

    if hd714:

        states = conn.execute(
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
                    ELSE 1
                END,
                days_detected DESC
            """,
            (hd714[0],),
        ).fetchall()

        for row in states:
            print(
                f"{row[0]} | "
                f"status={row[7]} | "
                f"days={row[4]} | "
                f"mentions={row[5]} | "
                f"{row[2]} -> {row[3]} | "
                f"{row[1]}"
            )

    print()
    print("HD702 CURRENT ISSUES")

    hd702 = conn.execute(
        """
        SELECT id
        FROM machines
        WHERE canonical_code = 'HD702'
        """
    ).fetchone()

    if hd702:

        states = conn.execute(
            """
            SELECT
                issue_family,
                issue_label,
                first_seen,
                last_seen,
                days_detected,
                repeated,
                status

            FROM issue_state_current

            WHERE machine_id = ?

            ORDER BY
                CASE status
                    WHEN 'OPEN' THEN 0
                    ELSE 1
                END,
                days_detected DESC
            """,
            (hd702[0],),
        ).fetchall()

        for row in states:
            print(
                f"{row[0]} | "
                f"status={row[6]} | "
                f"days={row[4]} | "
                f"{row[2]} -> {row[3]} | "
                f"{row[1]}"
            )

    conn.close()


if __name__ == "__main__":
    main()