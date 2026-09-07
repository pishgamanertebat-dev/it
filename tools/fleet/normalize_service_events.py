import sqlite3
import openpyxl
import re

DB = r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"

conn = sqlite3.connect(DB)
cur = conn.cursor()


cur.execute("""
SELECT id, raw_path
FROM ingest_sources
WHERE source_type = 'service_events'
ORDER BY id DESC
LIMIT 1
""")

source = cur.fetchone()

if not source:
    raise SystemExit("No service_events source found")


source_id, raw_path = source


print("SERVICE NORMALIZATION")
print("=" * 70)
print("Source:", raw_path)


wb = openpyxl.load_workbook(raw_path, data_only=True)


events = []


def extract_code(sheet_name):
    m = re.search(r'(\d+)', sheet_name)
    if m:
        return m.group(1)
    return sheet_name


for ws in wb.worksheets:

    name = ws.title

    if name in ["ساعت کاری", "ساعت کاری (2)"]:
        continue

    code = extract_code(name)

    for row in ws.iter_rows(min_row=4, values_only=True):

        date = row[1]

        if not date:
            continue

        technician = row[2]
        hour = row[3]

        values = []

        for idx, value in enumerate(row):

            if value is not None:
                values.append(f"{idx}:{value}")


        if len(values) == 0:
            continue


        events.append({
            "sheet": name,
            "code": code,
            "date": str(date),
            "technician": technician,
            "hour": hour,
            "values": " | ".join(values)
        })


print()
print("PARSED EVENTS:", len(events))

print()
print("SAMPLES")
print("-" * 70)

for e in events[:20]:
    print(
        e["date"],
        "|",
        e["sheet"],
        "|",
        e["technician"],
        "|",
        e["hour"],
        "|",
        e["values"]
    )


conn.close()