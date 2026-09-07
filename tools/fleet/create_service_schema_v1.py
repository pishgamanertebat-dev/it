import sqlite3
from pathlib import Path


DB = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)

conn = sqlite3.connect(DB)
cur = conn.cursor()


# ============================================================
# 1. DAILY / SHIFT WORK HOURS
# ============================================================

cur.execute("""
CREATE TABLE IF NOT EXISTS service_shift_hours (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    ingest_source_id INTEGER NOT NULL,

    machine_id INTEGER,
    canonical_code TEXT NOT NULL,

    jalali_date TEXT NOT NULL,
    shift TEXT NOT NULL,

    work_hours REAL,
    note TEXT,
    raw_value TEXT,

    source_sheet TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    source_col INTEGER NOT NULL,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (
        ingest_source_id,
        source_sheet,
        source_row,
        source_col
    )
)
""")


cur.execute("""
CREATE INDEX IF NOT EXISTS
idx_service_shift_hours_machine_date
ON service_shift_hours (
    canonical_code,
    jalali_date
)
""")


cur.execute("""
CREATE INDEX IF NOT EXISTS
idx_service_shift_hours_machine
ON service_shift_hours (
    machine_id
)
""")


# ============================================================
# 2. PM / SERVICE STATUS SNAPSHOT
# ============================================================

cur.execute("""
CREATE TABLE IF NOT EXISTS service_pm_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    ingest_source_id INTEGER NOT NULL,

    machine_id INTEGER,
    canonical_code TEXT NOT NULL,

    jalali_asof_date TEXT,

    next_service_hour REAL,
    current_meter_hour REAL,
    remaining_hours REAL,

    pm_issue_raw TEXT,
    oil_analysis_raw TEXT,

    source_sheet TEXT NOT NULL,
    source_row INTEGER NOT NULL,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (
        ingest_source_id,
        source_sheet,
        source_row
    )
)
""")


cur.execute("""
CREATE INDEX IF NOT EXISTS
idx_service_pm_machine
ON service_pm_snapshots (
    canonical_code
)
""")


conn.commit()


print("SERVICE SCHEMA V1")
print("=" * 70)

for table in (
    "service_shift_hours",
    "service_pm_snapshots",
):
    cols = cur.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    count = cur.execute(
        f"SELECT COUNT(*) FROM {table}"
    ).fetchone()[0]

    print()
    print(table)
    print("-" * 70)

    for col in cols:
        print(
            f"{col[1]:25} {col[2]}"
        )

    print(f"ROWS: {count}")


print()
print("=" * 70)
print("SERVICE SCHEMA V1 READY")

conn.close()