import argparse
import sqlite3
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--work-order-no",
        required=True
    )

    parser.add_argument(
        "--staff-id",
        required=True,
        type=int
    )

    args = parser.parse_args()

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

        # --------------------------------------------
        # حکم
        # --------------------------------------------

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
                excel_path
            FROM service_work_orders
            WHERE work_order_no = ?
            """,
            (
                args.work_order_no,
            )
        ).fetchone()

        if not order:
            raise SystemExit(
                "WORK ORDER NOT FOUND: "
                + args.work_order_no
            )

        # --------------------------------------------
        # سرویسکار
        # --------------------------------------------

        staff = con.execute(
            """
            SELECT
                id,
                display_name,
                bale_id,
                service_role,
                active
            FROM service_staff
            WHERE id = ?
            """,
            (
                args.staff_id,
            )
        ).fetchone()

        if not staff:
            raise SystemExit(
                f"STAFF NOT FOUND: {args.staff_id}"
            )

        if staff[4] != 1:
            raise SystemExit(
                f"STAFF IS INACTIVE: {staff[1]}"
            )

        if not staff[2]:
            raise SystemExit(
                f"STAFF HAS NO BALE ID: {staff[1]}"
            )

        # فعلاً AIR_FILTER باید فقط به سرویسکار AIR_FILTER وصل شود.
        if (
            order[2] == "AIR_FILTER"
            and staff[3] != "AIR_FILTER"
        ):
            raise SystemExit(
                "ROLE MISMATCH: "
                f"order={order[2]} "
                f"staff_role={staff[3]}"
            )

        # --------------------------------------------
        # اگر قبلاً همین فرد Assign شده
        # --------------------------------------------

        if order[6] == staff[0]:

            print("=" * 78)
            print("STAFF ALREADY ASSIGNED")
            print("=" * 78)

            print(
                f"WORK ORDER: {order[1]}"
            )

            print(
                f"STAFF:      {staff[1]}"
            )

            print(
                f"STAFF ID:   {staff[0]}"
            )

            print(
                f"BALE ID:    {staff[2]}"
            )

            print()
            print(
                "NO DUPLICATE UPDATE PERFORMED"
            )

            return

        # --------------------------------------------
        # اگر قبلاً فرد دیگری Assign شده
        # فعلاً خودکار overwrite نکن.
        # --------------------------------------------

        if order[6] is not None:

            old_staff = con.execute(
                """
                SELECT
                    display_name,
                    bale_id
                FROM service_staff
                WHERE id = ?
                """,
                (
                    order[6],
                )
            ).fetchone()

            old_name = (
                old_staff[0]
                if old_staff
                else "UNKNOWN"
            )

            raise SystemExit(
                "WORK ORDER ALREADY ASSIGNED "
                f"TO ANOTHER STAFF: {old_name}"
            )

        # --------------------------------------------
        # Assign
        # --------------------------------------------

        con.execute(
            """
            UPDATE service_work_orders
            SET
                assigned_staff_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                staff[0],
                order[0]
            )
        )

        con.commit()

        # --------------------------------------------
        # Verify JOIN
        # --------------------------------------------

        result = con.execute(
            """
            SELECT
                wo.id,
                wo.work_order_no,
                wo.work_order_type,
                wo.jalali_date,
                wo.shift,
                wo.status,
                wo.excel_path,
                s.id,
                s.display_name,
                s.bale_id,
                s.service_role,
                s.active
            FROM service_work_orders wo
            LEFT JOIN service_staff s
                ON s.id = wo.assigned_staff_id
            WHERE wo.id = ?
            """,
            (
                order[0],
            )
        ).fetchone()

        items = con.execute(
            """
            SELECT
                item_no,
                machine_code,
                machine_name,
                action_text
            FROM service_work_order_items
            WHERE work_order_id = ?
            ORDER BY item_no
            """,
            (
                order[0],
            )
        ).fetchall()

        print("=" * 78)
        print("WORK ORDER STAFF ASSIGNMENT")
        print("=" * 78)

        print(
            f"WORK ORDER ID: {result[0]}"
        )

        print(
            f"NUMBER:        {result[1]}"
        )

        print(
            f"TYPE:          {result[2]}"
        )

        print(
            f"DATE:          {result[3]}"
        )

        print(
            f"SHIFT:         {result[4]}"
        )

        print(
            f"STATUS:        {result[5]}"
        )

        print(
            f"EXCEL:         {result[6]}"
        )

        print()
        print("ASSIGNED STAFF")
        print("-" * 78)

        print(
            f"STAFF ID:      {result[7]}"
        )

        print(
            f"NAME:          {result[8]}"
        )

        print(
            f"BALE ID:       {result[9]}"
        )

        print(
            f"ROLE:          {result[10]}"
        )

        print(
            f"ACTIVE:        {result[11]}"
        )

        print()
        print(
            f"ITEMS ({len(items)})"
        )
        print("-" * 78)

        for row in items:

            print(
                f"{row[0]}. "
                f"{row[1]} | "
                f"{row[2]} | "
                f"{row[3]}"
            )

        print()
        print("=" * 78)
        print("RESULT")
        print("=" * 78)

        print(
            "WORK ORDER ASSIGNED TO STAFF"
        )

        print(
            "NO BALE MESSAGE SENT YET"
        )

        print("=" * 78)

    except Exception:

        con.rollback()
        raise

    finally:

        con.close()


if __name__ == "__main__":
    main()