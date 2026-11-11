"""Read delivery evidence only; never regenerate, alter or send a PDF."""
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo


def delivery_review(date, section, ledger):
    path=Path(ledger)
    evidence=[]
    if not path.is_file():
        return dict(status='verification_required',receipts=[])
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
            db.row_factory=sqlite3.Row
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'office_artifact_receipts','runs'} <= tables:
                return dict(status='verification_required',receipts=[])
            rows=list(db.execute('''SELECT a.* FROM office_artifact_receipts a JOIN runs r
                ON r.schedule_id=a.schedule_id AND r.due=a.due
                WHERE r.task IN ('mechanical_driver_daily','metalwork_driver_daily')
                AND a.artifact_id=? AND a.status IN ('sent','uncertain','sending')''',(section,)).fetchall())
            if 'receipts' in tables:
                # Older aggregate receipts remain authoritative even without an artifact row.
                rows.extend(db.execute('''SELECT a.*,? AS artifact_id FROM receipts a JOIN runs r
                    ON r.schedule_id=a.schedule_id AND r.due=a.due
                    WHERE r.task IN ('mechanical_driver_daily','metalwork_driver_daily')
                    AND a.delivery_kind='report' AND a.status IN ('sent','uncertain','sending')
                    AND NOT EXISTS (SELECT 1 FROM office_artifact_receipts detail
                        WHERE detail.schedule_id=a.schedule_id AND detail.due=a.due
                        AND detail.recipient_id=a.recipient_id AND detail.artifact_id=?)''',(section,section)).fetchall())
            import jdatetime
            for row in rows:
                due=datetime.fromisoformat(row['due'])
                if due.utcoffset() is None:
                    return dict(status='verification_required',receipts=[])
                target=jdatetime.date.fromgregorian(date=due.astimezone(ZoneInfo('Asia/Tehran')).date()-timedelta(days=1))
                if f'{target.year:04d}/{target.month:02d}/{target.day:02d}' == date:
                    evidence.append(dict(row))
    except (OSError,sqlite3.Error,ValueError):
        return dict(status='verification_required',receipts=[])
    return dict(status='correction_required' if evidence else 'no_recorded_delivery',receipts=evidence)
