"""Guard mine imports, continuing only from an explicit owner-approved baseline."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

from .entry_service import EntryError, open_audit, recover_prepared, source_reader, writer_lock
from .maintenance_service import CONFIG, RUNTIME


class MaintenanceSyncBlocked(EntryError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def retain_snapshot(runtime, data):
    """Content-addressed, verified, durable backup; never rewrite an old backup."""
    folder = runtime / 'backups'
    folder.mkdir(exist_ok=True)
    target = folder / (digest(data) + '.xlsx')
    if target.exists():
        if digest(target.read_bytes()) != target.stem:
            raise MaintenanceSyncBlocked('MAINTENANCE_SYNC_BACKUP_CORRUPT: ' + str(target))
        return target
    temporary = folder / ('.maintenance-guard-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def guard_snapshot(staged, destination, *, config=CONFIG, runtime=RUNTIME):
    settings = json.loads(Path(config).read_text(encoding='utf8'))
    source = Path(settings['source']).resolve(strict=True)
    if Path(destination).resolve(strict=True) != source:
        raise MaintenanceSyncBlocked('MAINTENANCE_SYNC_DESTINATION_DENIED')
    incoming = Path(staged).read_bytes()
    incoming_hash = digest(incoming)
    runtime = Path(runtime)
    with writer_lock(runtime):
        if not (runtime / 'audit.sqlite3').is_file():
            raise MaintenanceSyncBlocked('MAINTENANCE_SYNC_JOURNAL_MISSING: publication denied')
        with closing(open_audit(runtime)) as db, source_reader(source) as locked:
            original = locked.read()
            current_hash = digest(original)
            with db:
                db.execute('''CREATE TABLE IF NOT EXISTS maintenance_sync_failures (
                    identity TEXT PRIMARY KEY, source TEXT NOT NULL,
                    local_hash TEXT NOT NULL, incoming_hash TEXT NOT NULL,
                    local_snapshot TEXT NOT NULL, incoming_snapshot TEXT NOT NULL,
                    reason TEXT NOT NULL, operations TEXT NOT NULL,
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, attempts INTEGER NOT NULL)''')
            reason = ''
            from .maintenance_baseline import sync_from_baseline
            active = False
            try:
                result = sync_from_baseline(db, source, original, incoming, runtime)
                if result is not None:
                    return result
            except EntryError as exc:
                active = True
                reason = str(exc)
            if not active:
                try:
                    recover_prepared(db, source, current_hash)
                except EntryError:
                    reason = 'MAINTENANCE_SYNC_UNCERTAIN_OPERATION'
            rows = db.execute('''SELECT operation,status,before_hash,after_hash FROM edits
                WHERE source=? COLLATE NOCASE ORDER BY rowid''', (str(source),)).fetchall()
            operations = [dict(row) for row in rows]
            confirmed = [row for row in rows if row['status'] == 'succeeded']
            if not reason and any(row['status'] not in ('succeeded', 'not_applied') for row in rows):
                reason = 'MAINTENANCE_SYNC_UNCERTAIN_OPERATION'
            if not reason and confirmed:
                continuous = all(row['before_hash'] == previous['after_hash']
                                 for previous, row in zip(confirmed, confirmed[1:]))
                if not continuous or confirmed[-1]['after_hash'] != current_hash:
                    reason = 'MAINTENANCE_SYNC_UNVERIFIED_HISTORY'
            if not reason and incoming_hash == current_hash:
                # Byte equality is a no-op, never evidence inferred from a row
                # number or similar text. No publication or new edit operation.
                return dict(status='identical_noop', hash=current_hash, operations=len(confirmed))
            reason = reason or 'MAINTENANCE_SYNC_REVIEW_REQUIRED'
            local_backup = retain_snapshot(runtime, original)
            incoming_backup = retain_snapshot(runtime, incoming)
            now = datetime.now(timezone.utc).isoformat()
            identity = digest(json.dumps([str(source), current_hash, incoming_hash, reason]).encode())
            with db:
                db.execute('''INSERT INTO maintenance_sync_failures VALUES (?,?,?,?,?,?,?,?,?,?,1)
                    ON CONFLICT(identity) DO UPDATE SET last_seen=excluded.last_seen,
                        attempts=maintenance_sync_failures.attempts+1, operations=excluded.operations''',
                    (identity, str(source), current_hash, incoming_hash, str(local_backup),
                     str(incoming_backup), reason, json.dumps(operations), now, now))
            raise MaintenanceSyncBlocked(
                f'{reason}: replacement stopped; local={current_hash}; mine={incoming_hash}; '
                f'journal={runtime / "audit.sqlite3"}; both snapshots retained; manual review required')


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(guard_snapshot(args.staged, args.destination)))
    except Exception as exc:
        print(json.dumps(dict(status='blocked', error=str(exc))))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
