from pathlib import Path

from tools.fleet.work_orders.core.service import (
    get_work_order,
    list_eligible_staff,
)

from tools.fleet.work_orders.types.air_filter.builder import (
    build_document,
    get_items,
)


EXISTING_ORDER = (
    "AF-1405-06-10-001"
)

TEST_FILE = Path(
    r"E:\KomatsoAI"
    r"\work_orders"
    r"\air_filter"
    r"\_core_builder_test.xlsx"
)


def main():

    print("=" * 78)
    print("GENERIC WORK ORDER CORE TEST")
    print("=" * 78)

    # --------------------------------------------------------
    # 1) Existing DB order — READ ONLY
    # --------------------------------------------------------

    order = get_work_order(
        EXISTING_ORDER
    )

    print()
    print("1) EXISTING ORDER")

    print(
        "NUMBER: ",
        order["work_order_no"]
    )

    print(
        "TYPE:   ",
        order["work_order_type"]
    )

    print(
        "LABEL:  ",
        order[
            "work_order_label_fa"
        ]
    )

    print(
        "STATUS: ",
        order["status"]
    )

    print(
        "ITEMS:  ",
        len(order["items"])
    )

    assert (
        order["status"]
        == "SENT"
    )

    assert (
        order["work_order_type"]
        == "AIR_FILTER"
    )

    assert (
        len(order["items"])
        == 4
    )

    # --------------------------------------------------------
    # 2) Staff routing — READ ONLY
    # --------------------------------------------------------

    staff = list_eligible_staff(
        "AIR_FILTER"
    )

    print()
    print("2) STAFF ROUTING")

    print(
        "ELIGIBLE STAFF:",
        len(staff)
    )

    assert len(staff) >= 1

    # --------------------------------------------------------
    # 3) Builder test — temporary Excel only
    # --------------------------------------------------------

    print()
    print("3) AIR FILTER BUILDER")

    items = get_items(
        [
            "465",
            "710",
            "714",
            "MZ5",
        ]
    )

    if TEST_FILE.exists():
        TEST_FILE.unlink()

    build_document(
        output_path=TEST_FILE,
        jalali_date="1405/06/10",
        items=items,
    )

    assert TEST_FILE.exists()

    print(
        "TEMP EXCEL CREATED:",
        TEST_FILE
    )

    size = TEST_FILE.stat().st_size

    print(
        "TEMP EXCEL SIZE:",
        size
    )

    assert size > 0

    TEST_FILE.unlink()

    assert not TEST_FILE.exists()

    print(
        "TEMP EXCEL CLEANED: YES"
    )

    print()
    print("=" * 78)
    print("RESULT")
    print("=" * 78)

    print(
        "GENERIC WORK ORDER CORE PASS"
    )

    print(
        "NO DATABASE WRITE PERFORMED"
    )

    print(
        "NO BALE MESSAGE SENT"
    )

    print("=" * 78)


if __name__ == "__main__":
    main()