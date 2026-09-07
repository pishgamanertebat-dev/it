from __future__ import annotations

import sqlite3
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)


def main():
    if not DB_PATH.exists():
        raise SystemExit(
            f"ERROR: DB not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)

    rows = conn.execute(
        """
        SELECT
            machine_code_raw,
            machine_type_raw,
            COUNT(*) AS report_count,
            MIN(report_date) AS first_seen,
            MAX(report_date) AS last_seen
        FROM daily_fault_reports
        WHERE
            machine_code_raw IS NOT NULL
            AND TRIM(machine_code_raw) <> ''
        GROUP BY
            machine_code_raw,
            machine_type_raw
        ORDER BY
            machine_code_raw,
            machine_type_raw
        """
    ).fetchall()

    distinct_codes = conn.execute(
        """
        SELECT COUNT(DISTINCT machine_code_raw)
        FROM daily_fault_reports
        WHERE
            machine_code_raw IS NOT NULL
            AND TRIM(machine_code_raw) <> ''
        """
    ).fetchone()[0]

    print(f"DISTINCT MACHINE CODES: {distinct_codes}")
    print(f"CODE/TYPE COMBINATIONS: {len(rows)}")
    print()

    current_code = None

    for (
        code,
        machine_type,
        count,
        first_seen,
        last_seen,
    ) in rows:

        if code != current_code:
            if current_code is not None:
                print()

            print("=" * 70)
            print(f"CODE: {code}")
            current_code = code

        print(
            f"  TYPE: {machine_type or '(empty)'}"
        )
        print(
            f"  REPORTS: {count}"
        )
        print(
            f"  RANGE: {first_seen} -> {last_seen}"
        )

    conn.close()


if __name__ == "__main__":
    main()