import sqlite3
import openpyxl

DB = r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"

conn = sqlite3.connect(DB)
cur = conn.cursor()

path = cur.execute("""
SELECT raw_path
FROM ingest_sources
WHERE source_type='service_events'
ORDER BY id DESC
LIMIT 1
""").fetchone()[0]

wb = openpyxl.load_workbook(path, data_only=True)

print("BULLDOZER RELATED SHEETS")
print("="*70)

for ws in wb.worksheets:
    if "بلدوزر" in ws.title:
        print(ws.title)

print()
print("MACHINE D SERIES")
print("="*70)

for r in cur.execute("""
SELECT canonical_code, machine_type_hint
FROM machines
WHERE canonical_code LIKE 'D%'
"""):
    print(r)

conn.close()