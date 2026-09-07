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
        "--approved-by",
        required=True,
        help="Bale User ID or operator identity"
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

        order = con.execute(
            """
            SELECT
                wo.id,
                wo.work_order_no,
                wo.work_order_type,
                wo.jalali_date,
                wo.shift,
                wo.status,
                wo.assigned_staff_id,
                wo.excel_path,
                wo.approved_by,
                wo.approved_at,
                s.display_name,
                s.bale_id,
                s.active
            FROM service_work_orders wo
            LEFT JOIN service_staff s
                ON s.id = wo.assigned_staff_id
            WHERE wo.work_order_no = ?
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
        # قبلاً تأیید شده
        # --------------------------------------------

        if order[5] == "APPROVED":

            print("=" * 78)
            print("WORK ORDER ALREADY APPROVED")
            print("=" * 78)

            print(
                f"NUMBER:       {order[1]}"
            )

            print(
                f"STATUS:       {order[5]}"
            )

            print(
                f"APPROVED BY:  {order[8]}"
            )

            print(
                f"APPROVED AT:  {order[9]}"
            )

            print()
            print(
                "NO DUPLICATE APPROVAL PERFORMED"
            )

            return

        # --------------------------------------------
        # فقط FILE_READY قابل تأیید است
        # --------------------------------------------

        if order[5] != "FILE_READY":

            raise SystemExit(
                "APPROVAL BLOCKED: "
                f"STATUS IS {order[5]}, "
                "EXPECTED FILE_READY"
            )

        # --------------------------------------------
        # سرویسکار باید مشخص باشد
        # --------------------------------------------

        if order[6] is None:

            raise SystemExit(
                "APPROVAL BLOCKED: "
                "NO STAFF ASSIGNED"
            )

        if not order[11]:

            raise SystemExit(
                "APPROVAL BLOCKED: "
                "ASSIGNED STAFF HAS NO BALE ID"
            )

        if order[12] != 1:

            raise SystemExit(
                "APPROVAL BLOCKED: "
                "ASSIGNED STAFF IS INACTIVE"
            )

        # --------------------------------------------
        # فایل حکم باید واقعاً موجود باشد
        # --------------------------------------------

        if not order[7]:

            raise SystemExit(
                "APPROVAL BLOCKED: "
                "NO EXCEL PATH"
            )

        excel_path = Path(
            order[7]
        )

        if not excel_path.exists():

            raise SystemExit(
                "APPROVAL BLOCKED: "
                f"EXCEL FILE NOT FOUND: {excel_path}"
            )

        # --------------------------------------------
        # حکم باید آیتم داشته باشد
        # --------------------------------------------

        item_count = con.execute(
            """
            SELECT COUNT(*)
            FROM service_work_order_items
            WHERE work_order_id = ?
            """,
            (
                order[0],
            )
        ).fetchone()[0]

        if item_count <= 0:

            raise SystemExit(
                "APPROVAL BLOCKED: "
                "WORK ORDER HAS NO ITEMS"
            )

        # --------------------------------------------
        # Approval
        # --------------------------------------------

        con.execute(
            """
            UPDATE service_work_orders
            SET
                status = 'APPROVED',
                approved_by = ?,
                approved_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                f"BALE:{args.approved_by}",
                order[0]
            )
        )

        con.commit()

        # --------------------------------------------
        # Verify
        # --------------------------------------------

        result = con.execute(
            """
            SELECT
                wo.work_order_no,
                wo.status,
                wo.jalali_date,
                wo.shift,
                wo.approved_by,
                wo.approved_at,
                wo.excel_path,
                s.display_name,
                s.bale_id
            FROM service_work_orders wo
            LEFT JOIN service_staff s
                ON s.id = wo.assigned_staff_id
            WHERE wo.id = ?
            """,
            (
                order[0],
            )
        ).fetchone()

        print("=" * 78)
        print("WORK ORDER APPROVAL")
        print("=" * 78)

        print(
            f"NUMBER:       {result[0]}"
        )

        print(
            f"STATUS:       {result[1]}"
        )

        print(
            f"DATE:         {result[2]}"
        )

        print(
            f"SHIFT:        {result[3]}"
        )

        print(
            f"APPROVED BY:  {result[4]}"
        )

        print(
            f"APPROVED AT:  {result[5]}"
        )

        print(
            f"EXCEL:        {result[6]}"
        )

        print()
        print("TARGET STAFF")
        print("-" * 78)

        print(
            f"NAME:         {result[7]}"
        )

        print(
            f"BALE ID:      {result[8]}"
        )

        print()
        print("=" * 78)
        print("RESULT")
        print("=" * 78)

        print(
            "WORK ORDER APPROVED"
        )

        print(
            "SEND GATE: OPEN"
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