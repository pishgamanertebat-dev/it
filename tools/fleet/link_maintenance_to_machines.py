from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)


SPECIAL_SHEETS = {
    "متفرقه",
    "تعمیرگاه",
}


def canonical_from_sheet(sheet: str):
    sheet = sheet.strip()

    # Special known collision
    if sheet == "601":
        return "EX601"

    if sheet == "601.":
        return "WA601"

    # Excavators
    if sheet in {
        "231",
        "321",
        "331",
        "332",
        "521",
        "801",
        "802",
        "851",
        "852",
        "853",
        "1251",
        "1252",
        "1253",
        "1254",
        "1255",
    }:
        return f"EX{sheet}"

    # Loaders
    if sheet in {
        "471",
        "472",
        "473",
    }:
        return f"W{sheet}"

    # Dozers
    if sheet in {
        "151",
        "152",
    }:
        return f"D{sheet}"

    # HD465 fleet
    if sheet.isdigit():
        number = int(sheet)

        if 461 <= number <= 469:
            return f"HD{sheet}"

        if 701 <= number <= 716:
            return f"HD{sheet}"

    # Already canonical-ish support equipment
    if sheet in {
        "S1",
        "S2",
        "TA1",
        "TR1",
        "MZ4",
        "MZ6",
        "MZ10",
    }:
        return sheet

    return None


def expected_raw_code(sheet: str):
    if sheet == "601.":
        return "601"

    return sheet.rstrip(".")


def looks_like_machine_code(value: str):
    value = (value or "").strip()

    if not value:
        return False

    return bool(
        re.fullmatch(
            r"[A-Za-z]*\d+[A-Za-z0-9.-]*",
            value,
        )
    )


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS maintenance_event_identity (
            maintenance_event_id INTEGER PRIMARY KEY,

            machine_id INTEGER,

            resolution_status TEXT NOT NULL,
            resolution_method TEXT NOT NULL,

            canonical_code_candidate TEXT,

            resolved_at TEXT NOT NULL,

            FOREIGN KEY (maintenance_event_id)
                REFERENCES maintenance_events(id),

            FOREIGN KEY (machine_id)
                REFERENCES machines(id)
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_maintenance_identity_machine
        ON maintenance_event_identity(machine_id)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_maintenance_identity_status
        ON maintenance_event_identity(resolution_status)
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

    events = conn.execute(
        """
        SELECT
            id,
            sheet_name,
            COALESCE(machine_code_raw, ''),
            event_date_raw,
            action_raw
        FROM maintenance_events
        ORDER BY id
        """
    ).fetchall()

    now = datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")

    inserted = 0
    updated = 0
    unchanged = 0

    counts = {}

    with conn:

        for (
            event_id,
            sheet_name,
            raw_code,
            event_date,
            action,
        ) in events:

            sheet = (sheet_name or "").strip()
            raw = (raw_code or "").strip()

            machine_id = None
            candidate = None

            # ----------------------------------------
            # Special non-machine sheets
            # ----------------------------------------

            if sheet in SPECIAL_SHEETS:
                status = "SPECIAL_SHEET"
                method = "NO_MACHINE_LINK"

            else:

                candidate = canonical_from_sheet(
                    sheet
                )

                if candidate is None:
                    status = "UNRESOLVED_SHEET"
                    method = "NO_MAPPING"

                else:

                    expected = expected_raw_code(
                        sheet
                    )

                    # Different machine-looking numeric/code
                    # inside a device sheet -> do NOT guess.
                    if (
                        raw
                        and looks_like_machine_code(raw)
                        and raw.upper()
                            != expected.upper()
                    ):
                        status = "REVIEW_REQUIRED"
                        method = "RAW_CODE_CONFLICT"

                    else:

                        machine = conn.execute(
                            """
                            SELECT id
                            FROM machines
                            WHERE UPPER(canonical_code)
                                = UPPER(?)
                            """,
                            (candidate,),
                        ).fetchone()

                        if machine is None:
                            status = "MACHINE_NOT_REGISTERED"
                            method = "SHEET_MAPPING"

                        else:
                            machine_id = machine[0]
                            status = "LINKED"

                            if (
                                raw
                                and raw.upper()
                                    == expected.upper()
                            ):
                                method = (
                                    "SHEET_AND_RAW_CODE"
                                )
                            else:
                                method = (
                                    "SHEET_FALLBACK"
                                )

            counts[status] = (
                counts.get(status, 0) + 1
            )

            new_values = (
                machine_id,
                status,
                method,
                candidate,
            )

            existing = conn.execute(
                """
                SELECT
                    machine_id,
                    resolution_status,
                    resolution_method,
                    canonical_code_candidate

                FROM maintenance_event_identity

                WHERE maintenance_event_id = ?
                """,
                (event_id,),
            ).fetchone()

            if existing is None:

                conn.execute(
                    """
                    INSERT INTO maintenance_event_identity (
                        maintenance_event_id,
                        machine_id,
                        resolution_status,
                        resolution_method,
                        canonical_code_candidate,
                        resolved_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        machine_id,
                        status,
                        method,
                        candidate,
                        now,
                    ),
                )

                inserted += 1

            elif existing == new_values:

                unchanged += 1

            else:

                conn.execute(
                    """
                    UPDATE maintenance_event_identity

                    SET
                        machine_id = ?,
                        resolution_status = ?,
                        resolution_method = ?,
                        canonical_code_candidate = ?,
                        resolved_at = ?

                    WHERE maintenance_event_id = ?
                    """,
                    (
                        machine_id,
                        status,
                        method,
                        candidate,
                        now,
                        event_id,
                    ),
                )

                updated += 1

    print()
    print("MAINTENANCE IDENTITY COMPLETE")
    print(f"Events: {len(events)}")
    print(f"Inserted: {inserted}")
    print(f"Updated: {updated}")
    print(f"Unchanged: {unchanged}")

    print()
    print("STATUS COUNTS")

    for status in sorted(counts):
        print(
            f"  {status}: "
            f"{counts[status]}"
        )

    print()
    print("REVIEW REQUIRED")

    rows = conn.execute(
        """
        SELECT
            e.sheet_name,
            e.machine_code_raw,
            e.event_date_raw,
            e.action_raw,
            i.canonical_code_candidate,
            i.resolution_method

        FROM maintenance_event_identity i

        JOIN maintenance_events e
            ON e.id = i.maintenance_event_id

        WHERE i.resolution_status = 'REVIEW_REQUIRED'

        ORDER BY
            e.sheet_name,
            e.event_date_raw
        """
    ).fetchall()

    if not rows:
        print("  None")

    for row in rows:
        print(
            f"sheet={row[0]} | "
            f"raw={row[1]!r} | "
            f"date={row[2]} | "
            f"candidate={row[4]} | "
            f"{row[3]}"
        )

    print()
    print("MACHINE NOT REGISTERED")

    rows = conn.execute(
        """
        SELECT DISTINCT
            e.sheet_name,
            i.canonical_code_candidate

        FROM maintenance_event_identity i

        JOIN maintenance_events e
            ON e.id = i.maintenance_event_id

        WHERE
            i.resolution_status =
            'MACHINE_NOT_REGISTERED'

        ORDER BY e.sheet_name
        """
    ).fetchall()

    if not rows:
        print("  None")

    for row in rows:
        print(
            f"sheet={row[0]} -> "
            f"{row[1]}"
        )

    print()
    print("SPECIAL SHEETS")

    rows = conn.execute(
        """
        SELECT
            e.sheet_name,
            COUNT(*)

        FROM maintenance_event_identity i

        JOIN maintenance_events e
            ON e.id = i.maintenance_event_id

        WHERE
            i.resolution_status =
            'SPECIAL_SHEET'

        GROUP BY e.sheet_name

        ORDER BY e.sheet_name
        """
    ).fetchall()

    for row in rows:
        print(
            f"{row[0]}: {row[1]}"
        )

    print()
    print("601 COLLISION CHECK")

    rows = conn.execute(
        """
        SELECT
            e.sheet_name,
            e.machine_code_raw,
            m.canonical_code,
            i.resolution_status,
            i.resolution_method,
            COUNT(*)

        FROM maintenance_event_identity i

        JOIN maintenance_events e
            ON e.id = i.maintenance_event_id

        LEFT JOIN machines m
            ON m.id = i.machine_id

        WHERE e.sheet_name IN ('601', '601.')

        GROUP BY
            e.sheet_name,
            e.machine_code_raw,
            m.canonical_code,
            i.resolution_status,
            i.resolution_method

        ORDER BY e.sheet_name
        """
    ).fetchall()

    for row in rows:
        print(
            f"sheet={row[0]} | "
            f"raw={row[1]} | "
            f"machine={row[2]} | "
            f"status={row[3]} | "
            f"via={row[4]} | "
            f"events={row[5]}"
        )

    print()
    print("HD714 MAINTENANCE SAMPLE")

    rows = conn.execute(
        """
        SELECT
            e.event_date_raw,
            e.mechanic_raw,
            e.action_raw,
            e.parts_raw

        FROM maintenance_event_identity i

        JOIN maintenance_events e
            ON e.id = i.maintenance_event_id

        JOIN machines m
            ON m.id = i.machine_id

        WHERE m.canonical_code = 'HD714'

        ORDER BY
            e.event_date_raw DESC,
            e.id DESC

        LIMIT 5
        """
    ).fetchall()

    for row in rows:
        print(
            f"{row[0]} | "
            f"mechanic={row[1]} | "
            f"action={row[2]} | "
            f"parts={row[3]}"
        )

    conn.close()


if __name__ == "__main__":
    main()