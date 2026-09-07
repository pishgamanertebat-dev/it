from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
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

    now = datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")

    # فقط Mappingهایی که الان برایشان اطمینان داریم.
    mappings = {}

    # HD701 ... HD707 = HD785-5
    for number in range(701, 708):
        mappings[f"HD{number}"] = (
            "HD785-5",
            "دامپتراک کوماتسو HD785-5",
        )

    # HD708 ... HD716 = HD785-7
    for number in range(708, 717):
        mappings[f"HD{number}"] = (
            "HD785-7",
            "دامپتراک کوماتسو HD785-7",
        )

    updated = 0
    missing = []

    with conn:
        for code, (model_key, type_hint) in mappings.items():

            row = conn.execute(
                """
                SELECT id
                FROM machines
                WHERE canonical_code = ?
                """,
                (code,),
            ).fetchone()

            if not row:
                missing.append(code)
                continue

            conn.execute(
                """
                UPDATE machines
                SET
                    model_key = ?,
                    machine_type_hint = ?,
                    identity_status = 'VERIFIED',
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    model_key,
                    type_hint,
                    now,
                    row[0],
                ),
            )

            updated += 1

    print("MODEL MAPPING COMPLETE")
    print(f"Updated: {updated}")

    print()
    print("MISSING MACHINES")

    if missing:
        for code in missing:
            print(f"  {code}")
    else:
        print("  None")

    print()
    print("HD785 FLEET")

    rows = conn.execute(
        """
        SELECT
            canonical_code,
            model_key,
            identity_status
        FROM machines
        WHERE canonical_code LIKE 'HD7%'
        ORDER BY canonical_code
        """
    ).fetchall()

    for code, model, status in rows:
        print(
            f"{code} | "
            f"{model} | "
            f"{status}"
        )

    conn.close()


if __name__ == "__main__":
    main()