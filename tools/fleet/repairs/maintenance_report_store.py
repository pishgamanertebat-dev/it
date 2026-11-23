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


def remember(runtime, operation, actor, chat, source_id, raw, *, status='received'):
    digest = hashlib.sha256(raw.encode('utf8')).hexdigest()
    with closing(connect(runtime)) as db,db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT raw_hash FROM maintenance_report_messages WHERE operation=?',(operation,)).fetchall()
        conflict = any(row[0]!=digest for row in previous)
        db.execute('INSERT OR IGNORE INTO maintenance_report_messages VALUES (?,?,?,?,?,?,?,?)',
                   (operation,digest,str(actor),str(chat),str(source_id),raw,'conflict' if conflict else status,'{}'))
    if conflict:
        raise ValueError('متن همین شناسهٔ پیام تغییر کرده است؛ نسخه‌ها محفوظ‌اند و ثبت تازه انجام نشد.')
    return digest


def record_outcome(runtime, operation, digest, status, result):
    with closing(connect(runtime)) as db,db:
        db.execute('UPDATE maintenance_report_messages SET status=?,result=? WHERE operation=? AND raw_hash=?',
                   (status,json.dumps(result,ensure_ascii=False),operation,digest))


def partial_receipt(runtime, operation, digest):
    with closing(connect(runtime)) as db:
        return db.execute("""SELECT 1 FROM maintenance_report_messages
            WHERE operation=? AND raw_hash=? AND status='partial_received'""",(operation,digest)).fetchone() is not None


def open_workflows(runtime):
    db = connect(runtime)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS maintenance_report_workflows (
        operation TEXT PRIMARY KEY, actor TEXT NOT NULL, chat TEXT NOT NULL,
        source_id TEXT NOT NULL, payload TEXT NOT NULL)''')
    return db


def load_workflow(db, actor, chat, operation=None):
    query = 'SELECT payload FROM maintenance_report_workflows WHERE actor=? AND chat=?'
    args = (actor,chat)
    if operation is not None:
        query += ' AND operation=?'
        args += (operation,)
    row = db.execute(query+' ORDER BY rowid DESC LIMIT 1',args).fetchone()
    return json.loads(row['payload']) if row else None


def save_workflow(db, actor, chat, report):
    with db:
        db.execute('''INSERT INTO maintenance_report_workflows VALUES (?,?,?,?,?)
                      ON CONFLICT(operation) DO UPDATE SET payload=excluded.payload''',
                   (report['id'],actor,chat,report['source_id'],json.dumps(report,ensure_ascii=False)))


def correction_prompt(db, actor, chat, origin):
    return db.execute("""SELECT operation FROM maintenance_report_workflows WHERE actor=? AND chat=?
        AND json_extract(payload,'$.view.phase')='reply' AND json_extract(payload,'$.view.origin')=?""",
        (actor,chat,origin)).fetchone()


def has_correction_prompt(runtime, actor, chat, origin):
    path = (Path(runtime)/'audit.sqlite3').resolve()
    if not path.is_file():
        return False
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='maintenance_report_workflows'").fetchone()
        return bool(exists and correction_prompt(db,actor,chat,origin))
