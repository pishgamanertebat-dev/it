import sqlite3
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)

WORK_ORDER_NO = "AF-1405-06-10-001"


def main():

    if not DB_PATH.exists():
        raise SystemExit(
            f"DB NOT FOUND: {DB_PATH}"
        )

    con = sqlite3.connect(
        str(DB_PATH)
    )

    try:

        row = con.execute(
            """
            SELECT
                wo.id,
                wo.work_order_no,
                wo.work_order_type,
                wo.status,
                wo.excel_path,
                wo.send_attempts,
                wo.sent_at,
                wo.last_send_error,
                s.id,
                s.display_name,
                s.bale_id,
                s.service_role
            FROM service_work_orders wo
            LEFT JOIN service_staff s
                ON s.id = wo.assigned_staff_id
            WHERE wo.work_order_no = ?
            """,
            (
                WORK_ORDER_NO,
            )
        ).fetchone()

        print("=" * 78)
        print("POST-MOVE DATABASE CHECK")
        print("=" * 78)

        if row is None:

            print(
                "WORK ORDER NOT FOUND"
            )

            raise SystemExit(1)

        print(
            f"ID:              {row[0]}"
        )

        print(
            f"NUMBER:          {row[1]}"
        )

        print(
            f"TYPE:            {row[2]}"
        )

        print(
            f"STATUS:          {row[3]}"
        )

        print(
            f"EXCEL:           {row[4]}"
        )

        print(
            f"EXCEL EXISTS:    "
            f"{Path(row[4]).exists() if row[4] else False}"
        )

        print(
            f"SEND ATTEMPTS:   {row[5]}"
        )

        print(
            f"SENT AT:         {row[6]}"
        )

        print(
            f"LAST SEND ERROR: {row[7]}"
        )

        print()
        print("ASSIGNED STAFF")
        print("-" * 78)

        print(
            f"STAFF ID:        {row[8]}"
        )

        print(
            f"NAME:            {row[9]}"
        )

        print(
            f"BALE ID:         {row[10]}"
        )

        print(
            f"ROLE:            {row[11]}"
        )

        item_count = con.execute(
            """
            SELECT COUNT(*)
            FROM service_work_order_items
            WHERE work_order_id = ?
            """,
            (
                row[0],
            )
        ).fetchone()[0]

        print()
        print(
            f"ITEM COUNT:      {item_count}"
        )

        fk_errors = con.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        print(
            f"FOREIGN KEY:     "
            f"{'PASS' if not fk_errors else 'FAILED'}"
        )

        print()
        print("=" * 78)
        print("RESULT")
        print("=" * 78)

        if (
            row[3] == "SENT"
            and row[4]
            and Path(row[4]).exists()
            and item_count > 0
            and not fk_errors
        ):

            print(
                "WORK ORDER V1 STATE IS HEALTHY"
            )

        else:

            print(
                "WORK ORDER STATE NEEDS REVIEW"
            )

        print("=" * 78)

    finally:

        con.close()


if __name__ == "__main__":
    main()