"""Read-only verification of future copy receipts; never sends or renders."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from contextlib import closing

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--number')
    args=parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    database=Path('E:/KomatsoAI/data/fleet/db/fleet_ops.db')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
        rows=c.execute("""SELECT w.work_order_no,w.work_order_type,w.status AS work_order_status,
            w.created_by,w.approved_by,w.sent_at,w.pdf_path,d.recipient_id,d.status AS copy_status,
            d.artifact_sha256,d.artifact_path,d.file_name,d.attempts,d.message_id,d.last_error,d.created_at
            FROM work_order_final_copy d JOIN service_work_orders w ON w.id=d.work_order_id
            WHERE (? IS NULL OR w.work_order_no=?) ORDER BY d.created_at DESC LIMIT 10""",
            (args.number,args.number)).fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        path=Path(item['artifact_path']) if item['artifact_path'] else None
        item['pinned_hash_verified']=bool(path and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==item['artifact_sha256'])
        result.append(item)
    print(json.dumps({'copies':result,'note':'No future copy obligations yet' if not result else 'Read-only ledger and pinned artifact verification'},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
