from tools.fleet.work_orders.core.registry import (
    list_work_order_types,
    get_work_order_type,
)


def main():

    print("="*70)
    print("WORK ORDER REGISTRY TEST")
    print("="*70)


    print()
    print("ENABLED TYPES")
    print("-"*70)

    for item in list_work_order_types():

        print(
            item["key"],
            "|",
            item["label"],
            "|",
            "ENABLED"
        )


    print()
    print("ALL TYPES")
    print("-"*70)

    for item in list_work_order_types(
        enabled_only=False
    ):

        print(
            item["key"],
            "|",
            item["label"],
            "|",
            item["enabled"]
        )


    print()
    print("SINGLE LOOKUP")
    print("-"*70)

    item = get_work_order_type(
        "AIR_FILTER"
    )

    print(item)


if __name__ == "__main__":
    main()