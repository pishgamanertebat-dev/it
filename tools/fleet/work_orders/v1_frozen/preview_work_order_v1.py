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
                wo.excel_path,
                wo.pdf_path,
                wo.assigned_staff_id,
                s.display_name,
                s.bale_id,
                s.service_role,
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

        items = con.execute(
            """
            SELECT
                item_no,
                machine_code,
                machine_name,
                action_text,
                item_status
            FROM service_work_order_items
            WHERE work_order_id = ?
            ORDER BY item_no
            """,
            (
                order[0],
            )
        ).fetchall()

        excel_exists = False

        if order[6]:
            excel_exists = Path(
                order[6]
            ).exists()

        print()
        print("=" * 72)
        print("پیش‌نمایش حکم کار")
        print("=" * 72)

        print(
            f"شماره حکم: {order[1]}"
        )

        print(
            f"نوع حکم: هواکش"
        )

        print(
            f"تاریخ: {order[3]}"
        )

        print(
            f"شیفت: {order[4] or '-'}"
        )

        print(
            f"وضعیت فعلی: {order[5]}"
        )

        print()
        print("سرویسکار")
        print("-" * 72)

        if order[8] is None:

            print(
                "⚠️ هنوز سرویسکار تعیین نشده است."
            )

        else:

            print(
                f"نام: {order[9]}"
            )

            print(
                f"Bale ID: {order[10]}"
            )

            print(
                f"Role: {order[11]}"
            )

            print(
                f"Active: {order[12]}"
            )

        print()
        print(
            f"دستگاه‌ها ({len(items)})"
        )

        print("-" * 72)

        for item in items:

            print(
                f"{item[0]}. "
                f"{item[1]} | "
                f"{item[2]} | "
                f"{item[3]}"
            )

        print()
        print("فایل حکم")
        print("-" * 72)

        print(
            f"Excel: {order[6] or '-'}"
        )

        print(
            "Excel Exists: "
            + (
                "YES"
                if excel_exists
                else "NO"
            )
        )

        print(
            f"PDF: {order[7] or '-'}"
        )

        print()
        print("=" * 72)

        ready = True
        problems = []

        if order[5] != "FILE_READY":

            ready = False

            problems.append(
                "وضعیت حکم FILE_READY نیست"
            )

        if order[8] is None:

            ready = False

            problems.append(
                "سرویسکار تعیین نشده"
            )

        if not order[10]:

            ready = False

            problems.append(
                "Bale ID سرویسکار وجود ندارد"
            )

        if order[12] != 1:

            ready = False

            problems.append(
                "سرویسکار فعال نیست"
            )

        if not excel_exists:

            ready = False

            problems.append(
                "فایل Excel حکم روی دیسک پیدا نشد"
            )

        if not items:

            ready = False

            problems.append(
                "حکم هیچ دستگاهی ندارد"
            )

        if ready:

            print(
                "✅ آماده بررسی و تأیید مسئول نت"
            )

            print()
            print(
                "هیچ فایلی هنوز برای سرویسکار ارسال نشده است."
            )

        else:

            print(
                "❌ حکم هنوز آماده تأیید نیست"
            )

            for problem in problems:

                print(
                    f"- {problem}"
                )

        print("=" * 72)

    finally:

        con.close()


if __name__ == "__main__":
    main()