from tools.fleet.work_orders.core.assignment import assign_work_order


def main():

    print("="*70)
    print("GENERIC ASSIGN CORE TEST")
    print("="*70)


    print()
    print("FIRST RUN")
    print("-"*70)

    result = assign_work_order(
        work_order_no="AF-1405-06-11-001",
        staff_id=1,
    )

    print(result)


    print()
    print("SECOND RUN / IDEMPOTENCY TEST")
    print("-"*70)

    result2 = assign_work_order(
        work_order_no="AF-1405-06-11-001",
        staff_id=1,
    )

    print(result2)


if __name__ == "__main__":
    main()