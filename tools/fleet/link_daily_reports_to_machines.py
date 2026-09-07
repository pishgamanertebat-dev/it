from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_fault_report_identity (
            daily_fault_report_id INTEGER PRIMARY KEY,

            machine_id INTEGER NOT NULL,

            resolution_method TEXT NOT NULL,
            resolved_at TEXT NOT NULL,

            FOREIGN KEY (daily_fault_report_id)
                REFERENCES daily_fault_reports(id),

            FOREIGN KEY (machine_id)
                REFERENCES machines(id)
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_daily_fault_report_identity_machine
        ON daily_fault_report_identity(machine_id)
        """
    )

    conn.commit()


def unique_result(rows):
    unique = {}

    for row in rows:
        unique[row[0]] = row

    values = list(unique.values())

    if len(values) == 1:
        return values[0]

    if len(values) > 1:
        return "AMBIGUOUS"

    return None


def resolve_machine(conn, raw_code):
    code = (raw_code or "").strip()

    if not code:
        return None, "EMPTY_CODE"

    # 1) بالاترین اولویت:
    # Aliasهایی که دستی تأیید شده‌اند.
    rows = conn.execute(
        """
        SELECT DISTINCT
            m.id,
            m.canonical_code,
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

    result = unique_result(rows)

    if result == "AMBIGUOUS":
        return None, "AMBIGUOUS_VERIFIED_ALIAS"

    if result:
        return result, "VERIFIED_ALIAS"

    # 2) خود canonical_code
    rows = conn.execute(
        """
        SELECT
            id,
            canonical_code,
            model_key,
            identity_status
        FROM machines
        WHERE UPPER(canonical_code) = UPPER(?)
        """,
        (code,),
    ).fetchall()

    result = unique_result(rows)

    if result == "AMBIGUOUS":
        return None, "AMBIGUOUS_CANONICAL"

    if result:
        return result, "CANONICAL_CODE"

    # 3) Aliasهای استخراج‌شده از خود گزارش‌ها
    rows = conn.execute(
        """
        SELECT DISTINCT
            m.id,
            m.canonical_code,
            m.model_key,
            m.identity_status
        FROM machine_aliases a
        JOIN machines m
            ON m.id = a.machine_id
        WHERE UPPER(a.alias_code) = UPPER(?)
        """,
        (code,),
    ).fetchall()

    result = unique_result(rows)

    if result == "AMBIGUOUS":
        return None, "AMBIGUOUS_SOURCE_ALIAS"

    if result:
        return result, "SOURCE_ALIAS"

    return None, "NOT_FOUND"


def main():
    if not DB_PATH.exists():
        raise SystemExit(
            f"ERROR: DB not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    init_db(conn)

    reports = conn.execute(
        """
        SELECT
            id,
            machine_code_raw
        FROM daily_fault_reports
        ORDER BY id
        """
    ).fetchall()

    resolved_at = datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")

    inserted = 0
    updated = 0
    unchanged = 0
    unresolved = []

    method_counts = {}

    with conn:
        for report_id, raw_code in reports:

            machine, method = resolve_machine(
                conn,
                raw_code,
            )

            if machine is None:
                unresolved.append(
                    (
                        report_id,
                        raw_code,
                        method,
                    )
                )
                continue

            machine_id = machine[0]

            existing = conn.execute(
                """
                SELECT
                    machine_id,
                    resolution_method
                FROM daily_fault_report_identity
                WHERE daily_fault_report_id = ?
                """,
                (report_id,),
            ).fetchone()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO daily_fault_report_identity (
                        daily_fault_report_id,
                        machine_id,
                        resolution_method,
                        resolved_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        report_id,
                        machine_id,
                        method,
                        resolved_at,
                    ),
                )

                inserted += 1

            elif (
                existing[0] == machine_id
                and existing[1] == method
            ):
                unchanged += 1

            else:
                # این جدول Derived است و با اصلاح Identity
                # اجازه Re-resolution دارد.
                conn.execute(
                    """
                    UPDATE daily_fault_report_identity
                    SET
                        machine_id = ?,
                        resolution_method = ?,
                        resolved_at = ?
                    WHERE daily_fault_report_id = ?
                    """,
                    (
                        machine_id,
                        method,
                        resolved_at,
                        report_id,
                    ),
                )

                updated += 1

            method_counts[method] = (
                method_counts.get(method, 0) + 1
            )

    total_links = conn.execute(
        """
        SELECT COUNT(*)
        FROM daily_fault_report_identity
        """
    ).fetchone()[0]

    print()
    print("REPORT -> MACHINE LINK COMPLETE")
    print(f"Reports: {len(reports)}")
    print(f"Linked rows: {total_links}")
    print(f"Inserted: {inserted}")
    print(f"Updated: {updated}")
    print(f"Unchanged: {unchanged}")
    print(f"Unresolved: {len(unresolved)}")

    print()
    print("RESOLUTION METHODS")

    for method in sorted(method_counts):
        print(
            f"  {method}: "
            f"{method_counts[method]}"
        )

    print()
    print("UNRESOLVED SAMPLE")

    if unresolved:
        for item in unresolved[:20]:
            print(
                f"  report={item[0]} | "
                f"code={item[1]!r} | "
                f"reason={item[2]}"
            )
    else:
        print("  None")

    print()
    print("HD714 SAMPLE")

    rows = conn.execute(
        """
        SELECT
            d.report_date,
            d.machine_code_raw,
            m.canonical_code,
            m.model_key,
            m.identity_status,
            i.resolution_method
        FROM daily_fault_reports d

        JOIN daily_fault_report_identity i
            ON i.daily_fault_report_id = d.id

        JOIN machines m
            ON m.id = i.machine_id

        WHERE d.machine_code_raw = 'HD714'

        ORDER BY d.report_date DESC
        LIMIT 3
        """
    ).fetchall()

    for row in rows:
        print(
            f"{row[0]} | "
            f"raw={row[1]} | "
            f"machine={row[2]} | "
            f"model={row[3]} | "
            f"identity={row[4]} | "
            f"via={row[5]}"
        )

    print()
    print("VERIFIED ALIAS SAMPLES")

    for raw_code in (
        "601",
        "W601",
        "152",
        "1254",
        "1255",
        "702",
    ):
        row = conn.execute(
            """
            SELECT DISTINCT
                d.machine_code_raw,
                m.canonical_code,
                m.model_key,
                i.resolution_method
            FROM daily_fault_reports d

            JOIN daily_fault_report_identity i
                ON i.daily_fault_report_id = d.id

            JOIN machines m
                ON m.id = i.machine_id

            WHERE d.machine_code_raw = ?

            LIMIT 1
            """,
            (raw_code,),
        ).fetchone()

        if row:
            print(
                f"{row[0]} -> "
                f"{row[1]} | "
                f"model={row[2]} | "
                f"via={row[3]}"
            )

    conn.close()


if __name__ == "__main__":
    main()