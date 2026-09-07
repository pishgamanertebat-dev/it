import sqlite3
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)

DISPLAY_NAME = "سرویسکار آزمایشی هواکش"
BALE_ID = "455740857"
SERVICE_ROLE = "AIR_FILTER"


def main():

    print("=" * 78)
    print("REGISTER PILOT AIR FILTER STAFF")
    print("=" * 78)

    if not DB_PATH.exists():
        raise SystemExit(
            f"DB NOT FOUND: {DB_PATH}"
        )

    con = sqlite3.connect(
        str(DB_PATH)
    )

    con.execute(
        "PRAGMA foreign_keys = ON"
    )

    try:

        existing = con.execute(
            """
            SELECT
                id,
                display_name,
                bale_id,
                service_role,
                active
            FROM service_staff
            WHERE bale_id = ?
            """,
            (BALE_ID,)
        ).fetchone()

        if existing:

            print("STAFF ALREADY EXISTS")
            print(f"ID:       {existing[0]}")
            print(f"NAME:     {existing[1]}")
            print(f"BALE ID:  {existing[2]}")
            print(f"ROLE:     {existing[3]}")
            print(f"ACTIVE:   {existing[4]}")
            print()
            print("NO DUPLICATE INSERT PERFORMED")
            return

        cur = con.execute(
            """
            INSERT INTO service_staff (
                display_name,
                bale_id,
                service_role,
                active,
                notes
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                DISPLAY_NAME,
                BALE_ID,
                SERVICE_ROLE,
                1,
                "Pilot user for Air Filter Work Order V1",
            )
        )

        staff_id = cur.lastrowid

        con.commit()

        row = con.execute(
            """
            SELECT
                id,
                display_name,
                bale_id,
                service_role,
                active,
                created_at
            FROM service_staff
            WHERE id = ?
            """,
            (staff_id,)
        ).fetchone()

        print()
        print("=" * 78)
        print("STAFF CREATED")
        print("=" * 78)

        print(f"ID:         {row[0]}")
        print(f"NAME:       {row[1]}")
        print(f"BALE ID:    {row[2]}")
        print(f"ROLE:       {row[3]}")
        print(f"ACTIVE:     {row[4]}")
        print(f"CREATED AT: {row[5]}")

        count = con.execute(
            """
            SELECT COUNT(*)
            FROM service_staff
            """
        ).fetchone()[0]

        print()
        print(f"SERVICE STAFF COUNT: {count}")

        print()
        print("=" * 78)
        print("RESULT")
        print("=" * 78)
        print("PILOT AIR FILTER STAFF READY")
        print("=" * 78)

    except Exception:
        con.rollback()
        raise

    finally:
        con.close()


if __name__ == "__main__":
    main()