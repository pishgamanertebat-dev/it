from tools.fleet.work_orders.core.approval import approve_work_order


def main():

    print("="*70)
    print("GENERIC APPROVAL CORE TEST")
    print("="*70)


    print()
    print("FIRST RUN")
    print("-"*70)

    result = approve_work_order(
        work_order_no="AF-1405-06-11-001",
        approved_by="455740857",
    )

    print(result)


    print()
    print("SECOND RUN / IDEMPOTENCY TEST")
    print("-"*70)

    result2 = approve_work_order(
        work_order_no="AF-1405-06-11-001",
        approved_by="455740857",
    )

    print(result2)


if __name__ == "__main__":
    main()