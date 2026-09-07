import sqlite3
from pathlib import Path
from datetime import datetime


DB_PATH = Path(r"E:\KomatsoAI\data\fleet\db\fleet_ops.db")
BACKUP_DIR = DB_PATH.parent / "backups"

PLANNED_TABLES = {
    "service_staff",
    "service_work_orders",
    "service_work_order_items",
}


def get_tables(con):
    cur = con.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        ORDER BY name
    """)

    return {
        row[0]
        for row in cur.fetchall()
    }


def make_backup():
    BACKUP_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup_path = BACKUP_DIR / (
        f"fleet_ops_before_work_order_v1_{stamp}.db"
    )

    src = sqlite3.connect(
        str(DB_PATH)
    )

    dst = sqlite3.connect(
        str(backup_path)
    )

    try:
        src.backup(dst)

    finally:
        dst.close()
        src.close()

    return backup_path


def create_schema(con):

    con.execute(
        "PRAGMA foreign_keys = ON"
    )

    con.executescript("""
    CREATE TABLE IF NOT EXISTS service_staff (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        display_name TEXT NOT NULL,

        bale_id TEXT UNIQUE,

        service_role TEXT NOT NULL,

        active INTEGER NOT NULL DEFAULT 1,

        notes TEXT,

        created_at TEXT NOT NULL
            DEFAULT CURRENT_TIMESTAMP,

        updated_at TEXT NOT NULL
            DEFAULT CURRENT_TIMESTAMP
    );


    CREATE TABLE IF NOT EXISTS service_work_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        work_order_no TEXT NOT NULL UNIQUE,

        work_order_type TEXT NOT NULL,

        jalali_date TEXT NOT NULL,

        shift TEXT,

        status TEXT NOT NULL
            DEFAULT 'DRAFT',

        assigned_staff_id INTEGER,

        template_key TEXT,

        excel_path TEXT,

        pdf_path TEXT,

        notes TEXT,

        created_by TEXT,

        approved_by TEXT,

        send_attempts INTEGER NOT NULL
            DEFAULT 0,

        last_send_error TEXT,

        sent_at TEXT,

        acknowledged_at TEXT,

        created_at TEXT NOT NULL
            DEFAULT CURRENT_TIMESTAMP,

        approved_at TEXT,

        finalized_at TEXT,

        updated_at TEXT NOT NULL
            DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (
            assigned_staff_id
        )
        REFERENCES service_staff(id)
        ON UPDATE CASCADE
        ON DELETE SET NULL
    );


    CREATE TABLE IF NOT EXISTS service_work_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        work_order_id INTEGER NOT NULL,

        item_no INTEGER NOT NULL,

        machine_id INTEGER,

        machine_code TEXT NOT NULL,

        machine_name TEXT,

        action_code TEXT,

        action_text TEXT NOT NULL,

        item_status TEXT NOT NULL
            DEFAULT 'PENDING',

        note TEXT,

        created_at TEXT NOT NULL
            DEFAULT CURRENT_TIMESTAMP,

        updated_at TEXT NOT NULL
            DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (
            work_order_id
        )
        REFERENCES service_work_orders(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

        FOREIGN KEY (
            machine_id
        )
        REFERENCES machines(id)
        ON UPDATE CASCADE
        ON DELETE SET NULL,

        UNIQUE (
            work_order_id,
            item_no
        )
    );


    CREATE INDEX IF NOT EXISTS
        idx_service_work_orders_date
    ON service_work_orders(
        jalali_date
    );


    CREATE INDEX IF NOT EXISTS
        idx_service_work_orders_status
    ON service_work_orders(
        status
    );


    CREATE INDEX IF NOT EXISTS
        idx_service_work_orders_type
    ON service_work_orders(
        work_order_type
    );


    CREATE INDEX IF NOT EXISTS
        idx_service_work_orders_staff
    ON service_work_orders(
        assigned_staff_id
    );


    CREATE INDEX IF NOT EXISTS
        idx_service_work_order_items_order
    ON service_work_order_items(
        work_order_id
    );


    CREATE INDEX IF NOT EXISTS
        idx_service_work_order_items_machine_code
    ON service_work_order_items(
        machine_code
    );
    """)

    con.commit()


def show_table(con, table):

    print()
    print(
        f"TABLE: {table}"
    )

    print("-" * 78)

    rows = con.execute(
        f'PRAGMA table_info("{table}")'
    ).fetchall()

    for row in rows:

        print(
            f"{row[1]:25} | "
            f"{row[2]:10} | "
            f"notnull={row[3]} | "
            f"default={row[4]} | "
            f"pk={row[5]}"
        )

    count = con.execute(
        f'SELECT COUNT(*) FROM "{table}"'
    ).fetchone()[0]

    print(
        f"ROWS: {count}"
    )


def main():

    print("=" * 78)
    print("WORK ORDER SCHEMA V1")
    print("=" * 78)

    if not DB_PATH.exists():

        raise SystemExit(
            f"DB NOT FOUND: {DB_PATH}"
        )

    pre = sqlite3.connect(
        str(DB_PATH)
    )

    current_tables = get_tables(pre)

    pre.close()

    missing = (
        PLANNED_TABLES
        - current_tables
    )

    if missing:

        print(
            "Schema change required."
        )

        print(
            "Missing tables: "
            + ", ".join(
                sorted(missing)
            )
        )

        print()
        print(
            "Creating safety backup..."
        )

        backup_path = make_backup()

        print(
            f"BACKUP: {backup_path}"
        )

    else:

        print(
            "All Work Order V1 tables "
            "already exist."
        )

        print(
            "No new backup required "
            "for this idempotency run."
        )

    con = sqlite3.connect(
        str(DB_PATH)
    )

    try:

        create_schema(con)

        final_tables = get_tables(con)

        still_missing = (
            PLANNED_TABLES
            - final_tables
        )

        if still_missing:

            raise RuntimeError(
                "Schema creation incomplete: "
                + ", ".join(
                    sorted(still_missing)
                )
            )

        print()
        print("=" * 78)
        print("SCHEMA DETAILS")
        print("=" * 78)

        for table in [
            "service_staff",
            "service_work_orders",
            "service_work_order_items",
        ]:

            show_table(
                con,
                table
            )

        print()
        print("=" * 78)
        print("FOREIGN KEY CHECK")
        print("=" * 78)

        fk_errors = con.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        if fk_errors:

            print(
                "FAILED"
            )

            for row in fk_errors:
                print(row)

            raise RuntimeError(
                "Foreign key check failed"
            )

        else:

            print(
                "PASS - no foreign key violations"
            )

        print()
        print("=" * 78)
        print("RESULT")
        print("=" * 78)
        print(
            "WORK ORDER SCHEMA V1 READY"
        )
        print(
            "No work orders inserted."
        )
        print("=" * 78)

    finally:

        con.close()


if __name__ == "__main__":
    main()