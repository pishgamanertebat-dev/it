import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)

CORRECTIONS = {
    "EX321": "بیل مکانیکی 320",
    "EX331": "بیل مکانیکی 330",
    "EX332": "بیل مکانیکی 330",
    "EX521": "بیل مکانیکی 520",
}


def main():
    conn = sqlite3.connect(DB_PATH)

    now = datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")

    with conn:
        for code, type_hint in CORRECTIONS.items():

            conn.execute(
                """
                UPDATE machines
                SET
                    machine_type_hint = ?,
                    updated_at = ?
                WHERE canonical_code = ?
                """,
                (
                    type_hint,
                    now,
                    code,
                ),
            )

    print("IDENTITY LABEL CLEANUP COMPLETE")

    rows = conn.execute(
        """
        SELECT
            canonical_code,
            machine_type_hint,
            model_key,
            identity_status

        FROM machines

        WHERE canonical_code IN (
            'EX321',
            'EX331',
            'EX332',
            'EX521'
        )

        ORDER BY canonical_code
        """
    ).fetchall()

    for row in rows:
        print(
            f"{row[0]} | "
            f"{row[1]} | "
            f"model={row[2]} | "
            f"{row[3]}"
        )

    conn.close()


if __name__ == "__main__":
    main()