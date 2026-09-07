import sqlite3
import json
from datetime import datetime

DB = r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"

conn = sqlite3.connect(DB)
cur = conn.cursor()

# check source
cur.execute("""
SELECT id, raw_path
FROM ingest_sources
WHERE source_type='service_events'
ORDER BY id DESC
LIMIT 1
""")

source = cur.fetchone()

if not source:
    raise SystemExit("service_events source not found")

source_id, raw_path = source

print("SERVICE NORMALIZATION V2")
print("=" * 70)
print("SOURCE:", raw_path)

# inspect existing normalized table
tables = [
    r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
]

print()
print("AVAILABLE TABLES")
for t in tables:
    print("-", t)

print()
print("CURRENT SERVICE EVENTS")
print("-" * 70)

count = cur.execute("""
SELECT COUNT(*)
FROM maintenance_events
WHERE source_id=?
""", (source_id,)).fetchone()[0]

print("Existing maintenance events:", count)

print()
print("NEXT STEP")
print("Service normalization schema will be created after header mapping.")

conn.close()