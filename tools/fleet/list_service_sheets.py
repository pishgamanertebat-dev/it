import sqlite3
import openpyxl

DB = r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"

conn = sqlite3.connect(DB)
cur = conn.cursor()

row = cur.execute("""
SELECT raw_path
FROM ingest_sources
WHERE source_type='service_events'
ORDER BY id DESC
LIMIT 1
""").fetchone()

path = row[0]

wb = openpyxl.load_workbook(path, data_only=True)

print("SERVICE SHEETS")
print("="*70)

for ws in wb.worksheets:
    print(ws.title)

conn.close()