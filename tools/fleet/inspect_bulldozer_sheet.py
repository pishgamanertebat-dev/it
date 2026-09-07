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

ws = wb["بلدوزر 301"]

print("SHEET: بلدوزر 301")
print("="*70)

for row in ws.iter_rows(min_row=1, max_row=20, values_only=True):
    print(row)

conn.close()