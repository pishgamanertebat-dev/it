from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)


def get_machine(conn, code):
    return conn.execute(
        """
        SELECT id
        FROM machines
        WHERE canonical_code = ?
        """,
        (code,),
    ).fetchone()


def ensure_machine(
    conn,
    code,
    type_hint,
    model_key=None,
):
    now = datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")

    row = get_machine(conn, code)

    if row:
        machine_id = row[0]

        conn.execute(
            """
            UPDATE machines
            SET
                machine_type_hint = ?,
                model_key = COALESCE(?, model_key),
                identity_status = 'VERIFIED',
                updated_at = ?
            WHERE id = ?
            """,
            (
                type_hint,
                model_key,
                now,
                machine_id,
            ),
        )

        return machine_id

    cur = conn.execute(
        """
        INSERT INTO machines (
            canonical_code,
            machine_type_hint,
            model_key,
            identity_status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, 'VERIFIED', ?, ?)
        """,
        (
            code,
            type_hint,
            model_key,
            now,
            now,
        ),
    )

    return cur.lastrowid


def rename_machine(conn, old_code, new_code):
    old = get_machine(conn, old_code)
    new = get_machine(conn, new_code)

    if not old:
        return

    if new:
        raise RuntimeError(
            f"STOP: both {old_code} and {new_code} exist"
        )

    conn.execute(
        """
        UPDATE machines
        SET canonical_code = ?
        WHERE id = ?
        """,
        (
            new_code,
            old[0],
        ),
    )


def add_verified_alias(
    conn,
    machine_id,
    alias_code,
):
    now = datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")

    conn.execute(
        """
        INSERT OR IGNORE INTO machine_aliases (
            machine_id,
            alias_code,
            alias_type_raw,
            source_type,
            verified,
            created_at
        )
        VALUES (
            ?, ?, '',
            'manual_verification',
            1,
            ?
        )
        """,
        (
            machine_id,
            alias_code,
            now,
        ),
    )


def mark_observation_verified(
    conn,
    raw_code,
):
    conn.execute(
        """
        UPDATE machine_identity_observations
        SET resolution_status = 'VERIFIED'
        WHERE raw_code = ?
        """,
        (raw_code,),
    )


def main():
    if not DB_PATH.exists():
        raise SystemExit(
            f"ERROR: DB not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    with conn:

        # ------------------------------------------------
        # 601 / W601 -> WA601
        # ------------------------------------------------

        rename_machine(
            conn,
            "W601",
            "WA601",
        )

        wa601 = ensure_machine(
            conn,
            "WA601",
            "لودر کوماتسو 600",
        )

        for alias in (
            "WA601",
            "W601",
            "601",
        ):
            add_verified_alias(
                conn,
                wa601,
                alias,
            )

        mark_observation_verified(
            conn,
            "601",
        )

        mark_observation_verified(
            conn,
            "W601",
        )

        # ------------------------------------------------
        # 152 -> D152
        # ------------------------------------------------

        d152 = ensure_machine(
            conn,
            "D152",
            "بلدوزر کوماتسو 155",
        )

        for alias in (
            "D152",
            "152",
        ):
            add_verified_alias(
                conn,
                d152,
                alias,
            )

        mark_observation_verified(
            conn,
            "152",
        )

        # ------------------------------------------------
        # 1254 -> EX1254
        # 1255 -> EX1255
        # ------------------------------------------------

        for numeric, canonical in (
            ("1254", "EX1254"),
            ("1255", "EX1255"),
        ):
            machine_id = ensure_machine(
                conn,
                canonical,
                "بیل مکانیکی 1250",
            )

            add_verified_alias(
                conn,
                machine_id,
                canonical,
            )

            add_verified_alias(
                conn,
                machine_id,
                numeric,
            )

            mark_observation_verified(
                conn,
                numeric,
            )

        # ------------------------------------------------
        # EX1253 confirmed.
        # Hyundai text in Excel is treated as raw-data error.
        # ------------------------------------------------

        ex1253 = ensure_machine(
            conn,
            "EX1253",
            "بیل مکانیکی 1250",
        )

        add_verified_alias(
            conn,
            ex1253,
            "EX1253",
        )

        mark_observation_verified(
            conn,
            "EX1253",
        )

        # ------------------------------------------------
        # No HD875 fleet exists.
        # HD701..HD707 are HD785-5.
        # ------------------------------------------------

        for number in range(701, 708):
            code = f"HD{number}"

            machine_id = ensure_machine(
                conn,
                code,
                "دامپتراک کوماتسو HD785-5",
                model_key="HD785-5",
            )

            add_verified_alias(
                conn,
                machine_id,
                code,
            )

            mark_observation_verified(
                conn,
                code,
            )

        # Numeric 702 is alias of HD702.
        hd702 = get_machine(
            conn,
            "HD702",
        )[0]

        add_verified_alias(
            conn,
            hd702,
            "702",
        )

        mark_observation_verified(
            conn,
            "702",
        )

    print("VERIFIED IDENTITY UPDATE COMPLETE")
    print()

    print("CONFIRMED MACHINES")

    targets = (
        "D152",
        "WA601",
        "EX1253",
        "EX1254",
        "EX1255",
        "HD701",
        "HD702",
        "HD703",
        "HD704",
        "HD705",
        "HD706",
        "HD707",
    )

    placeholders = ",".join(
        "?" for _ in targets
    )

    rows = conn.execute(
        f"""
        SELECT
            canonical_code,
            machine_type_hint,
            model_key,
            identity_status
        FROM machines
        WHERE canonical_code IN ({placeholders})
        ORDER BY canonical_code
        """,
        targets,
    ).fetchall()

    for row in rows:
        print(
            f"{row[0]} | "
            f"{row[1]} | "
            f"model={row[2]} | "
            f"{row[3]}"
        )

    print()
    print("VERIFIED MANUAL ALIASES")

    aliases = conn.execute(
        """
        SELECT
            m.canonical_code,
            a.alias_code
        FROM machine_aliases a
        JOIN machines m
            ON m.id = a.machine_id
        WHERE
            a.source_type = 'manual_verification'
            AND a.verified = 1
        ORDER BY
            m.canonical_code,
            a.alias_code
        """
    ).fetchall()

    for canonical, alias in aliases:
        print(
            f"{alias} -> {canonical}"
        )

    print()
    print("STILL UNRESOLVED")

    unresolved = conn.execute(
        """
        SELECT DISTINCT raw_code
        FROM machine_identity_observations
        WHERE resolution_status = 'UNRESOLVED'
        ORDER BY raw_code
        """
    ).fetchall()

    if unresolved:
        for row in unresolved:
            print(f"  {row[0]}")
    else:
        print("  None")

    conn.close()


if __name__ == "__main__":
    main()