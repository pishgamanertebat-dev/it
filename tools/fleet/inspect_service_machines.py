import sqlite3

DB = r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"

conn = sqlite3.connect(DB)
cur = conn.cursor()

print("SERVICE SOURCE")
print("="*70)

for r in cur.execute("""
SELECT id, original_filename, raw_path
FROM ingest_sources
WHERE source_type='service_events'
ORDER BY id DESC
LIMIT 1
"""):
    print(r)


print()
print("MACHINE REGISTER")
print("="*70)

for r in cur.execute("""
SELECT canonical_code, machine_type_hint, model_key, identity_status
FROM machines
ORDER BY canonical_code
LIMIT 100
"""):
    print(r)


conn.close()