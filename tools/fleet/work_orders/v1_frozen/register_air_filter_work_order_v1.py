import sqlite3
from pathlib import Path


DB_PATH = Path(r"E:\KomatsoAI\data\fleet\db\fleet_ops.db")

WORK_ORDER_NO = "AF-1405-06-09-001"
WORK_ORDER_TYPE = "AIR_FILTER"
JALALI_DATE = "1405/06/09"
SHIFT = "صبح"
STATUS = "FILE_READY"

EXCEL_PATH = Path(
    r"E:\KomatsoAI\work_orders\air_filter\TEST_air_filter_1405-06-09.xlsx"
)

TEMPLATE_KEY = "AIR_FILTER_DAY9_V1"

ITEMS = [
    ("465",  "دامپتراک", "AIR_FILTER_OUTER", "تعویض هواکش بیرونی"),
    ("712",  "دامپتراک", "AIR_FILTER_OUTER", "تعویض هواکش بیرونی"),
    ("710",  "دامپتراک", "AIR_FILTER_OUTER", "تعویض هواکش بیرونی"),
    ("708",  "دامپتراک", "AIR_FILTER_OUTER", "تعویض هواکش بیرونی"),
    ("MZ5",  "مزدا",     "AIR_FILTER_OUTER", "تعویض هواکش بیرونی"),
    ("MZ10", "مزدا",     "AIR_FILTER_OUTER", "تعویض هواکش بیرونی"),
]


def main():

    print("=" * 78)
    print("REGISTER AIR FILTER WORK ORDER V1")
    print("=" * 78)

    if not DB_PATH.exists():
        raise SystemExit(
            f"DB NOT FOUND: {DB_PATH}"
        )

    if not EXCEL_PATH.exists():
        raise SystemExit(
            f"EXCEL NOT FOUND: {EXCEL_PATH}"
        )

    con = sqlite3.connect(
        str(DB_PATH)
    )

    con.execute(
        "PRAGMA foreign_keys = ON"
    )

    try:

        # ----------------------------------------------------
        # Duplicate protection
        # ----------------------------------------------------

        existing = con.execute(
            """
            SELECT
                id,
                work_order_no,
                status,
                excel_path
            FROM service_work_orders
            WHERE work_order_no = ?
            """,
            (WORK_ORDER_NO,)
        ).fetchone()

        if existing:

            print("WORK ORDER ALREADY EXISTS")
            print(f"ID:        {existing[0]}")
            print(f"NUMBER:    {existing[1]}")
            print(f"STATUS:    {existing[2]}")
            print(f"EXCEL:     {existing[3]}")
            print()
            print("NO DUPLICATE INSERT PERFORMED")
            return

        # ----------------------------------------------------
        # Transaction
        # ----------------------------------------------------

        con.execute("BEGIN")

        cur = con.execute(
            """
            INSERT INTO service_work_orders (
                work_order_no,
                work_order_type,
                jalali_date,
                shift,
                status,
                assigned_staff_id,
                template_key,
                excel_path,
                pdf_path,
                notes,
                created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                WORK_ORDER_NO,
                WORK_ORDER_TYPE,
                JALALI_DATE,
                SHIFT,
                STATUS,
                None,
                TEMPLATE_KEY,
                str(EXCEL_PATH),
                None,
                "اولین تست Work Order V1 هواکش",
                "SYSTEM_TEST",
            )
        )

        work_order_id = cur.lastrowid

        for item_no, item in enumerate(
            ITEMS,
            start=1
        ):

            (
                machine_code,
                machine_name,
                action_code,
                action_text,
            ) = item

            con.execute(
                """
                INSERT INTO service_work_order_items (
                    work_order_id,
                    item_no,
                    machine_id,
                    machine_code,
                    machine_name,
                    action_code,
                    action_text,
                    item_status,
                    note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    work_order_id,
                    item_no,
                    None,
                    machine_code,
                    machine_name,
                    action_code,
                    action_text,
                    "PENDING",
                    None,
                )
            )

        con.commit()

        # ----------------------------------------------------
        # Verify
        # ----------------------------------------------------

        order = con.execute(
            """
            SELECT
                id,
                work_order_no,
                work_order_type,
                jalali_date,
                shift,
                status,
                assigned_staff_id,
                template_key,
                excel_path,
                pdf_path,
                send_attempts,
                created_at
            FROM service_work_orders
            WHERE id = ?
            """,
            (work_order_id,)
        ).fetchone()

        items = con.execute(
            """
            SELECT
                item_no,
                machine_code,
                machine_name,
                action_code,
                action_text,
                item_status
            FROM service_work_order_items
            WHERE work_order_id = ?
            ORDER BY item_no
            """,
            (work_order_id,)
        ).fetchall()

        print()
        print("=" * 78)
        print("WORK ORDER")
        print("=" * 78)

        print(f"ID:              {order[0]}")
        print(f"WORK ORDER NO:   {order[1]}")
        print(f"TYPE:            {order[2]}")
        print(f"DATE:            {order[3]}")
        print(f"SHIFT:           {order[4]}")
        print(f"STATUS:          {order[5]}")
        print(f"STAFF ID:        {order[6]}")
        print(f"TEMPLATE:        {order[7]}")
        print(f"EXCEL:           {order[8]}")
        print(f"PDF:             {order[9]}")
        print(f"SEND ATTEMPTS:   {order[10]}")
        print(f"CREATED AT:      {order[11]}")

        print()
        print("=" * 78)
        print(f"ITEMS ({len(items)})")
        print("=" * 78)

        for row in items:

            print(
                f"{row[0]}. "
                f"{row[1]} | "
                f"{row[2]} | "
                f"{row[3]} | "
                f"{row[4]} | "
                f"{row[5]}"
            )

        print()
        print("=" * 78)
        print("DATABASE COUNTS")
        print("=" * 78)

        order_count = con.execute(
            """
            SELECT COUNT(*)
            FROM service_work_orders
            """
        ).fetchone()[0]

        item_count = con.execute(
            """
            SELECT COUNT(*)
            FROM service_work_order_items
            """
        ).fetchone()[0]

        staff_count = con.execute(
            """
            SELECT COUNT(*)
            FROM service_staff
            """
        ).fetchone()[0]

        print(
            f"service_work_orders:      {order_count}"
        )

        print(
            f"service_work_order_items: {item_count}"
        )

        print(
            f"service_staff:            {staff_count}"
        )

        print()
        print("=" * 78)
        print("RESULT")
        print("=" * 78)
        print(
            "FIRST AIR FILTER WORK ORDER REGISTERED"
        )
        print("=" * 78)

    except Exception:

        con.rollback()
        raise

    finally:

        con.close()


if __name__ == "__main__":
    main()