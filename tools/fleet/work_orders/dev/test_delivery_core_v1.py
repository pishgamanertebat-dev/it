from tools.fleet.work_orders.core.delivery import send_work_order


class FakeSender:

    def send_document(
        self,
        *,
        chat_id,
        file_path,
        file_name,
    ):

        print("FAKE SEND")
        print("----------------")
        print("CHAT ID:", chat_id)
        print("FILE:", file_name)

        return {
            "ok": True
        }


def main():

    print("="*70)
    print("GENERIC DELIVERY CORE TEST")
    print("="*70)


    sender = FakeSender()


    print()
    print("FIRST RUN")
    print("-"*70)


    result = send_work_order(
        work_order_no="AF-1405-06-11-001",
        sender=sender,
    )

    print(result)


    print()
    print("SECOND RUN / IDEMPOTENCY TEST")
    print("-"*70)


    result2 = send_work_order(
        work_order_no="AF-1405-06-11-001",
        sender=sender,
    )

    print(result2)


if __name__ == "__main__":
    main()