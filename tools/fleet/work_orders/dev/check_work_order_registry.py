from tools.fleet.work_orders.core.registry import (
    get_work_order_spec,
    list_work_order_specs,
)


def main():

    print("=" * 78)
    print("WORK ORDER TYPE REGISTRY")
    print("=" * 78)

    types = list_work_order_specs()

    for spec in types:

        state = (
            "OPERATIONAL"
            if spec.operational
            else "REGISTERED / DISABLED"
        )

        print(
            f"{spec.menu_order}. "
            f"{spec.code:<12} | "
            f"{spec.label_fa:<15} | "
            f"{state}"
        )

    print()
    print("=" * 78)
    print("VALIDATION")
    print("=" * 78)

    assert len(types) == 3

    assert (
        get_work_order_spec(
            "AIR_FILTER"
        ).operational
        is True
    )

    assert (
        get_work_order_spec(
            "OIL_CHANGE"
        ).operational
        is False
    )

    assert (
        get_work_order_spec(
            "GREASING"
        ).operational
        is False
    )

    assert (
        get_work_order_spec(
            "AIR_FILTER"
        ).number_prefix
        == "AF"
    )

    print("TYPE COUNT:       PASS (3)")
    print("AIR FILTER:       OPERATIONAL")
    print("OIL CHANGE:       DISABLED / READY FOR FUTURE")
    print("GREASING:         DISABLED / READY FOR FUTURE")

    print()
    print("=" * 78)
    print("RESULT")
    print("=" * 78)

    print(
        "WORK ORDER REGISTRY PASS"
    )

    print("=" * 78)


if __name__ == "__main__":
    main()
