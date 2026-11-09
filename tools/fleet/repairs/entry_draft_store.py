"""Durable draft/confirmation records; no Excel or model imports in the Gateway."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import sqlite3
import time
import uuid

RUNTIME=Path(__file__).resolve().parents[3]/'runtime/repairs-entry'
ACTORS=frozenset(('1732374823','654806764','641220453','387679249'))

class DraftError(ValueError):
    pass

def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def content_hash(payload):
    return hashlib.sha256(encoded(payload).encode()).hexdigest()


class DraftStore:
    def __init__(self, runtime=RUNTIME):
        self.runtime = runtime

    def connect(self):
        self.runtime.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.runtime/'audit.sqlite3', timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('''CREATE TABLE IF NOT EXISTS entry_drafts (
            id TEXT PRIMARY KEY, actor TEXT, chat TEXT, version INTEGER, hash TEXT,
            payload TEXT, status TEXT, text_ok TEXT, write_ok TEXT, binding TEXT, results TEXT)''')
        db.execute('''CREATE TABLE IF NOT EXISTS entry_draft_events (
            id INTEGER PRIMARY KEY, draft TEXT, actor TEXT, chat TEXT, version INTEGER,
            hash TEXT, event TEXT, evidence TEXT, created REAL)''')
        return db

    @staticmethod
    def event(db, d, event, evidence=None):
        db.execute('''INSERT INTO entry_draft_events
            (draft,actor,chat,version,hash,event,evidence,created) VALUES (?,?,?,?,?,?,?,?)''',
            (d['id'], d['actor'], d['chat'], d['version'], d['hash'], event, encoded(evidence), time.time()))

    @staticmethod
    def decoded(row):
        d = dict(row)
        for name in ('payload', 'text_ok', 'write_ok', 'binding', 'results'):
            d[name] = json.loads(d[name]) if d[name] else None
        if d['hash'] != content_hash(d['payload']):
            raise DraftError('هش پیش‌نویس معتبر نیست؛ ثبت متوقف شد.')
        return d

    def load(self, identifier, actor, chat):
        with closing(self.connect()) as db:
            row = db.execute('SELECT * FROM entry_drafts WHERE id=? AND actor=? AND chat=?',
                             (identifier, str(actor), str(chat))).fetchone()
            if row is None:
                raise DraftError('پیش‌نویس متعلق به این حساب و گفتگو نیست.')
            return self.decoded(row)

    def latest(self, actor, chat):
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM entry_drafts WHERE actor=? AND chat=? AND status NOT IN ('CANCELLED','DONE') ORDER BY rowid DESC LIMIT 1",
                             (str(actor), str(chat))).fetchone()
            return self.decoded(row) if row else None

    def create(self, actor, chat, payload):
        d = dict(id=uuid.uuid4().hex, actor=str(actor), chat=str(chat), version=1,
                 hash=content_hash(payload), payload=payload, status='EDIT',
                 text_ok=None, write_ok=None, binding=None, results={})
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO entry_drafts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                tuple(encoded(d[k]) if k in ('payload','text_ok','write_ok','binding','results') else d[k]
                      for k in ('id','actor','chat','version','hash','payload','status','text_ok','write_ok','binding','results')))
            self.event(db, d, 'created')
        return d

    def update(self, d, event, *, edit=False, evidence=None):
        old_version = d['version']
        if edit:
            if d['status'] in ('COMMITTING', 'PARTIAL', 'DONE') or d['results']:
                raise DraftError('ثبت آغاز شده است؛ ابتدا نتیجهٔ همان عملیات را بازیابی کنید. اصلاح، گزارش جداگانه می‌خواهد.')
            d.update(version=old_version+1, hash=content_hash(d['payload']), status='EDIT',
                     text_ok=None, write_ok=None, binding=None)
        with closing(self.connect()) as db, db:
            cursor = db.execute('''UPDATE entry_drafts SET version=?,hash=?,payload=?,status=?,text_ok=?,write_ok=?,binding=?,results=?
                WHERE id=? AND actor=? AND chat=? AND version=?''',
                (d['version'], d['hash'], encoded(d['payload']), d['status'], encoded(d['text_ok']),
                 encoded(d['write_ok']), encoded(d['binding']), encoded(d['results']),
                 d['id'], d['actor'], d['chat'], old_version))
            if cursor.rowcount != 1:
                raise DraftError('نسخهٔ پیش‌نویس تغییر کرده است؛ دوباره باز کنید.')
            self.event(db, d, event, evidence)
        return d

    def bind(self, d, stage, token, message):
        d['binding'] = dict(stage=stage, token=token, message=str(message), expires=time.time()+1800,
                            version=d['version'], hash=d['hash'])
        return self.update(d, 'prompt', evidence=d['binding'])

    def consume(self, identifier, actor, chat, stage, token, message, action):
        # Atomically consume a one-shot callback before doing any asynchronous work.
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM entry_drafts WHERE id=? AND actor=? AND chat=?',
                             (identifier,str(actor),str(chat))).fetchone()
            if not row:
                raise DraftError('دکمه متعلق به این حساب نیست.')
            d = self.decoded(row)
            b = d['binding'] or {}
            if (b.get('stage') != stage or b.get('token') != token or not token
                    or b.get('message') != str(message) or b.get('expires',0) < time.time()
                    or b.get('version') != d['version'] or b.get('hash') != d['hash']):
                raise DraftError('دکمه قدیمی یا نامعتبر است؛ «ادامه پیش‌نویس» را بفرستید.')
            evidence = dict(b, action=action)
            db.execute('UPDATE entry_drafts SET binding=NULL WHERE id=?', (identifier,))
            d['binding'] = None
            if action == 'confirm_text':
                if stage != 'TEXT_CONFIRM' or d['status'] != 'EDIT' or d['payload'].get('issues'):
                    raise DraftError('گزارش هنوز آمادهٔ تأیید نیست.')
                d.update(text_ok=evidence, write_ok=None, status='TEXT_OK')
                db.execute("UPDATE entry_drafts SET text_ok=?,write_ok=NULL,status='TEXT_OK' WHERE id=?", (encoded(evidence),identifier))
            elif action == 'confirm_write':
                if stage != 'WRITE_CONFIRM' or d['status'] not in ('TEXT_OK','PARTIAL','COMMITTING') or not d['text_ok']:
                    raise DraftError('تأیید متن معتبر وجود ندارد.')
                d.update(write_ok=evidence, status='COMMITTING')
                db.execute("UPDATE entry_drafts SET write_ok=?,status='COMMITTING' WHERE id=?", (encoded(evidence),identifier))
            self.event(db,d,'callback',evidence)
            return d
