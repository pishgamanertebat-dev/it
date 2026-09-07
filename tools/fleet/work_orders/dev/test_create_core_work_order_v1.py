from tools.fleet.work_orders.core.service import (
    create_work_order,
)


def main():

    print("=" * 80)
    print("CREATE WORK ORDER USING GENERIC CORE")
    print("=" * 80)

    order = create_work_order(
        work_order_type="AIR_FILTER",
        jalali_date="1405/06/11",
        shift="صبح",
        machine_codes=[
            "465",
            "710",
            "714",
            "MZ5",
        ],
        created_by="CORE_TEST",
    )

    print()
    print("WORK ORDER CREATED")
    print("-" * 80)

    print(
        "ID:",
        order["id"]
    )

    print(
        "NUMBER:",
        order["work_order_no"]
    )

    print(
        "TYPE:",
        order["work_order_type"]
    )

    print(
        "STATUS:",
        order["status"]
    )

    print(
        "EXCEL:",
        order["excel_path"]
    )

    print()

    print(
        "ITEMS:",
        len(order["items"])
    )

    for item in order["items"]:

        print(
            item["item_no"],
            "|",
            item["machine_code"],
            "|",
            item["action_text"]
        )

    print()
    print("=" * 80)
    print("CORE CREATE TEST PASS")
    print("=" * 80)


if __name__ == "__main__":
    main()