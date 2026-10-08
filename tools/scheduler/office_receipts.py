"""Artifact detail beside the unchanged recipient ledger used by delivery alerts."""
from datetime import datetime, timezone
from .misfire import outcome_from_receipts


def create_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS office_artifact_receipts (
        schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL, status TEXT NOT NULL, message_id TEXT, sha256 TEXT,
        updated TEXT NOT NULL, PRIMARY KEY(schedule_id,due,recipient_id,artifact_id))""")


def states(c, key):
    return {(r['recipient_id'], r['artifact_id']): r['status'] for r in c.execute(
        'SELECT * FROM office_artifact_receipts WHERE schedule_id=? AND due=?', key)}


def save(c, key, user, artifact, status, message_id=None, digest=None):
    with c:
        c.execute("""INSERT INTO office_artifact_receipts VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(schedule_id,due,recipient_id,artifact_id) DO UPDATE SET
            status=CASE WHEN office_artifact_receipts.status IN ('sent','uncertain')
                   THEN office_artifact_receipts.status ELSE excluded.status END,
            message_id=COALESCE(excluded.message_id,office_artifact_receipts.message_id),
            sha256=COALESCE(excluded.sha256,office_artifact_receipts.sha256),updated=excluded.updated""",
            (*key, str(user), artifact, status, message_id, digest, datetime.now(timezone.utc).isoformat()))


def recover(c, key):
    with c:
        c.execute("UPDATE office_artifact_receipts SET status='uncertain' WHERE schedule_id=? AND due=? AND status='sending'", key)
        found = states(c, key)
        for user in {u for u, a in found}:
            outcome = outcome_from_receipts(v for (u, a), v in found.items() if u == user)
            aggregate = 'failed' if outcome in {'partial','retry_wait'} else {'succeeded':'sent','skipped':'revoked'}.get(outcome, outcome)
            c.execute("UPDATE receipts SET status=? WHERE schedule_id=? AND due=? AND recipient_id=? AND delivery_kind='report'",
                      (aggregate, *key, user))
