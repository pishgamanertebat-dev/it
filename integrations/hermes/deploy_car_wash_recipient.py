"""Register the owner-approved car-wash recipient; never send an order.

The default validates a copied fleet DB. --apply backs up the live DB and
adds only the dedicated service_staff row. Registry roles stay unchanged.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

from integrations.hermes.deploy_service_staff_net import backup, snapshot, verify_delta
from tools.authorization.store import DB_PATH as AUTH_DB
from tools.fleet.work_orders.core import db, staff_dispatch
from tools.fleet.work_orders.core.paths import DB_PATH as FLEET_DB, PROJECT_ROOT

NAME = 'مهدی کارگری'
ACTOR = 'explicit-owner-car-wash-recipient-20261104'


def verify_identity(auth_path):
    with closing(sqlite3.connect(auth_path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        identity = con.execute("""SELECT chat_id,verified_name,registration_status FROM channel_users
            WHERE platform='bale' AND user_id=?""", (staff_dispatch.CAR_WASH_RECIPIENT_ID,)).fetchone()
        if identity != (staff_dispatch.CAR_WASH_RECIPIENT_ID, NAME, 'approved'):
            raise ValueError('Approved private Bale identity does not match the owner request')


def change(path, backup_dir=None):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=15)) as con, con:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('BEGIN IMMEDIATE')
        before = snapshot(con, 'main', {'service_staff'})
        backups = []
        if backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=False)
            backups.append(backup(path, backup_dir / 'fleet.sqlite3'))
        row = con.execute('SELECT display_name,service_role,active FROM service_staff WHERE bale_id=?',
                          (staff_dispatch.CAR_WASH_RECIPIENT_ID,)).fetchone()
        if row is None:
            con.execute("""INSERT INTO service_staff(display_name,bale_id,service_role,active,notes)
                VALUES (?,?,'CAR_WASH',1,?)""", (NAME, staff_dispatch.CAR_WASH_RECIPIENT_ID, ACTOR))
        elif row != (NAME, 'CAR_WASH', 1):
            raise ValueError('Conflicting recipient identity; existing staff record preserved')
        after = snapshot(con, 'main', {'service_staff'})
        verify_delta(before, after, {'service_staff'})
        if (con.execute('PRAGMA integrity_check').fetchall() != [('ok',)]
                or con.execute('PRAGMA foreign_key_check').fetchall()):
            raise sqlite3.DatabaseError('Fleet integrity verification failed')
        changed = [table for table in before if before[table] != after[table]]
    with patch.object(db, 'DB_PATH', path):
        options = staff_dispatch.staff_options('CAR_WASH')
        if [(s['bale_id'], s['display_name']) for s in options] != [(staff_dispatch.CAR_WASH_RECIPIENT_ID, NAME)]:
            raise AssertionError('Dedicated car-wash recipient lookup failed')
        general = staff_dispatch.staff_options()
        if any(s['bale_id'] == staff_dispatch.CAR_WASH_RECIPIENT_ID for s in general):
            raise AssertionError('Car-wash recipient leaked into the general roster')
    return {'changed_tables': changed, 'backups': backups, 'integrity': 'ok', 'foreign_keys': 'ok',
            'existing_rows_and_history_preserved': True, 'car_wash_options': options, 'service_options': general}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    out = args.evidence.resolve()
    if not out.is_relative_to(PROJECT_ROOT / 'runtime'):
        raise ValueError('Evidence must be stored below project runtime')
    out.mkdir(parents=True, exist_ok=False)
    verify_identity(AUTH_DB)
    copy_path = out / 'fleet-test.sqlite3'
    copied = backup(FLEET_DB, copy_path)
    result = {'source_copy': copied, 'copy_change': change(copy_path)}
    if change(copy_path)['changed_tables']:
        raise AssertionError('Repeated application must be a no-op')
    result['idempotent'] = True
    if args.apply:
        verify_identity(AUTH_DB)
        result['live_change'] = change(FLEET_DB, out / 'backups')
    result.update(applied_to_live=args.apply, messages_sent=0, authorization_changed=False)
    (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
