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
            sheet_name,
            COALESCE(machine_code_raw, ''),
            COUNT(*) AS event_count,
            MIN(event_date_raw),
            MAX(event_date_raw)

        FROM maintenance_events

        GROUP BY
            sheet_name,
            machine_code_raw

        ORDER BY
            sheet_name,
            event_count DESC
        """
    ).fetchall()

    print("MAINTENANCE IDENTITY INSPECTION")
    print("=" * 70)

    current_sheet = None

    for (
        sheet_name,
        raw_code,
        count,
        first_date,
        last_date,
    ) in rows:

        if sheet_name != current_sheet:

            if current_sheet is not None:
                print()

            print("=" * 70)
            print(f"SHEET: {sheet_name}")

            current_sheet = sheet_name

        print(
            f"  RAW CODE: "
            f"{raw_code or '(empty)'}"
        )

        print(
            f"  EVENTS: {count}"
        )

        print(
            f"  RANGE: "
            f"{first_date} -> {last_date}"
        )

    print()
    print("=" * 70)
    print("SPECIAL 601 CHECK")

    rows = conn.execute(
        """
        SELECT
            sheet_name,
            machine_code_raw,
            COUNT(*),
            MIN(event_date_raw),
            MAX(event_date_raw)

        FROM maintenance_events

        WHERE sheet_name IN ('601', '601.')

        GROUP BY
            sheet_name,
            machine_code_raw

        ORDER BY sheet_name
        """
    ).fetchall()

    for row in rows:
        print(
            f"{row[0]} | "
            f"raw={row[1]} | "
            f"events={row[2]} | "
            f"{row[3]} -> {row[4]}"
        )

    print()
    print("=" * 70)
    print("POTENTIAL ODD CODES")

    rows = conn.execute(
        """
        SELECT
            sheet_name,
            machine_code_raw,
            COUNT(*) AS count

        FROM maintenance_events

        WHERE
            machine_code_raw IS NULL
            OR TRIM(machine_code_raw) = ''
            OR machine_code_raw LIKE '% %'
            OR machine_code_raw GLOB '*[^0-9A-Za-z.-]*'

        GROUP BY
            sheet_name,
            machine_code_raw

        ORDER BY
            sheet_name,
            count DESC
        """
    ).fetchall()

    if not rows:
        print("None")

    for row in rows:
        print(
            f"{row[0]} | "
            f"raw={row[1]!r} | "
            f"events={row[2]}"
        )

    conn.close()


if __name__ == "__main__":
    main()