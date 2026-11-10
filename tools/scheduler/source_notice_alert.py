"""Retain the legacy source-notice schema and baseline for audit and receipt adoption."""
from .delivery_alert import _iso

ACTIVATION_KEY = 'source_notice_alert_activated_at'


def ensure_activation(conn, now):
    """Baseline already-sent notices once; new notices are observed after this tick."""
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS source_notice_baseline (
            schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient_id TEXT NOT NULL,
            PRIMARY KEY(schedule_id,due,recipient_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS source_notice_alert_receipt (
            schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient_id TEXT NOT NULL,
            operator_id TEXT NOT NULL, status TEXT NOT NULL, message_id TEXT,
            error TEXT, updated TEXT NOT NULL,
            PRIMARY KEY(schedule_id,due,recipient_id,operator_id))''')
        if conn.execute('SELECT 1 FROM notifier_meta WHERE key=?', (ACTIVATION_KEY,)).fetchone():
            return
        conn.execute('''INSERT OR IGNORE INTO source_notice_baseline
            SELECT schedule_id,due,recipient_id FROM receipts
            WHERE delivery_kind='notice' AND status='sent' ''')
        conn.execute('INSERT INTO notifier_meta VALUES (?,?)', (ACTIVATION_KEY, _iso(now)))
