"""Durable pointers to the document being reviewed, scoped to actor and chat."""
import sqlite3

from tools.fleet.work_orders.core import db


def review_context(actor, chat, role, *, number=None, stage=None, db_path=None):
    con = sqlite3.connect(str(db_path)) if db_path is not None else db.connect_db()
    try:
        with con:
            con.execute('''CREATE TABLE IF NOT EXISTS service_work_order_conversation (
                actor TEXT NOT NULL, chat TEXT NOT NULL, role TEXT NOT NULL,
                number TEXT NOT NULL, stage TEXT NOT NULL,
                PRIMARY KEY(actor, chat, role))''')
            if number is not None:
                con.execute('''INSERT INTO service_work_order_conversation VALUES (?,?,?,?,?)
                    ON CONFLICT(actor,chat,role) DO UPDATE SET number=excluded.number,stage=excluded.stage''',
                    (actor, str(chat), role, number, stage))
            row = con.execute('SELECT number,stage FROM service_work_order_conversation WHERE actor=? AND chat=? AND role=?',
                              (actor, str(chat), role)).fetchone()
            return tuple(row) if row else None
    finally:
        con.close()
