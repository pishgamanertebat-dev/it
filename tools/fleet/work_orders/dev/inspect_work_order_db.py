import sqlite3
from pathlib import Path


DB_PATH = Path(r"E:\KomatsoAI\data\fleet\db\fleet_ops.db")


def main():

    if not DB_PATH.exists():
        raise SystemExit(
            f"DB NOT FOUND: {DB_PATH}"
        )

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    print("=" * 80)
    print("WORK ORDER DB PRECHECK")
    print("=" * 80)
    print(f"DB: {DB_PATH}")
    print()

    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        ORDER BY name
    """)

    tables = [
        row[0]
        for row in cur.fetchall()
    ]

    print("CURRENT TABLES")
    print("-" * 80)

    for name in tables:
        print(name)

    print()
    print("=" * 80)
    print("WORK ORDER NAME COLLISION CHECK")
    print("=" * 80)

    planned = [
        "service_work_orders",
        "service_work_order_items",
        "service_staff",
    ]

    for name in planned:

        if name in tables:
            print(f"EXISTS:  {name}")

            cur.execute(
                f'PRAGMA table_info("{name}")'
            )

            for row in cur.fetchall():
                print(
                    f"    {row[1]} | "
                    f"{row[2]} | "
                    f"notnull={row[3]} | "
                    f"default={row[4]} | "
                    f"pk={row[5]}"
                )

        else:
            print(f"FREE:    {name}")

    print()
    print("=" * 80)
    print("RESULT")
    print("=" * 80)
    print("READ-ONLY CHECK COMPLETE")
    print("NO DATABASE CHANGES MADE")
    print("=" * 80)

    con.close()


if __name__ == "__main__":
    main()