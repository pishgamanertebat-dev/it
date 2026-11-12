"""Keep report text before asynchronous processing, including rejected versions."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

RUNTIME = Path(__file__).resolve().parents[3]/'runtime/maintenance-entry'
NAMESPACE = uuid.UUID('2d750e14-8fc8-43a5-805c-ea361eeb75da')


def operation_id(actor, chat, source_id):
    return uuid.uuid5(NAMESPACE,json.dumps([str(actor),str(chat),str(source_id)],ensure_ascii=False)).hex


def connect(runtime):
    runtime = Path(runtime)
    runtime.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(runtime/'audit.sqlite3',timeout=30)
    db.execute('''CREATE TABLE IF NOT EXISTS maintenance_report_messages (
        operation TEXT, raw_hash TEXT, actor TEXT, chat TEXT, source_id TEXT, raw_text TEXT,
        status TEXT, result TEXT, PRIMARY KEY(operation,raw_hash))''')
    return db


def remember(runtime, operation, actor, chat, source_id, raw):
    digest = hashlib.sha256(raw.encode('utf8')).hexdigest()
    with closing(connect(runtime)) as db,db:
        previous = db.execute('SELECT raw_hash FROM maintenance_report_messages WHERE operation=?',(operation,)).fetchall()
        conflict = any(row[0]!=digest for row in previous)
        db.execute('INSERT OR IGNORE INTO maintenance_report_messages VALUES (?,?,?,?,?,?,?,?)',
                   (operation,digest,str(actor),str(chat),str(source_id),raw,'conflict' if conflict else 'received','{}'))
    if conflict:
        raise ValueError('متن همین شناسهٔ پیام تغییر کرده است؛ نسخه‌ها محفوظ‌اند و ثبت تازه انجام نشد.')
    return digest


def record_outcome(runtime, operation, digest, status, result):
    with closing(connect(runtime)) as db,db:
        db.execute('UPDATE maintenance_report_messages SET status=?,result=? WHERE operation=? AND raw_hash=?',
                   (status,json.dumps(result,ensure_ascii=False),operation,digest))
