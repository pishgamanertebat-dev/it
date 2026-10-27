"""Owner-requested 2026-10-27 roster/NET membership change; no messaging.

Run as a module. The default validates copied databases only; --apply also
backs up both live databases and commits their changes in one transaction.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

from tools.authorization import AuthorizationStore
from tools.authorization.net import NET_STAFF, assign_net_staff
from tools.authorization.store import DB_PATH as AUTH_DB
from tools.fleet.work_orders.core import db, staff_dispatch
from tools.fleet.work_orders.core.paths import DB_PATH as FLEET_DB

ROOT = Path(__file__).resolve().parents[2]
HAMID = ('630499683', 'حمید ریش سیاه مرندی')
HUSSEIN = ('247562718', 'حسین محمودی')
USERS = (HAMID[0], HUSSEIN[0])
EXPECTED_ROSTER = [('1432500464','محسن غضنفری'), HUSSEIN, ('1294822197','پوریا آسترکی'), HAMID]
ACTOR = 'explicit-user-service-staff-net-20261027'
AUTH_MUTABLE = {'auth_roles', 'auth_role_profiles', 'auth_user_roles', 'auth_events', 'auth_extensions'}
FLEET_MUTABLE = {'service_staff', 'service_staff_roster'}


def backup(source, target):
    if target.exists():
        raise FileExistsError(target)
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise sqlite3.DatabaseError('Backup integrity failed')
    return {'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}


def snapshot(con, schema, mutable):
    result = {}
    tables = [r[0] for r in con.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name<>'sqlite_sequence' ORDER BY name")]
    for table in tables:
        query = f'SELECT * FROM {schema}."{table}" ORDER BY rowid'
        if table in mutable:
            result[table] = con.execute(query).fetchall()
        else:
            digest = hashlib.sha256()
            for row in con.execute(query):
                digest.update(json.dumps(row, ensure_ascii=True, default=repr).encode('utf-8') + b'\n')
            result[table] = digest.hexdigest()
    return result


def add_hamid(con):
    row = con.execute('SELECT id,display_name,service_role,active FROM fleet.service_staff WHERE bale_id=?', (HAMID[0],)).fetchone()
    if row is None:
        staff_id = con.execute("""INSERT INTO fleet.service_staff(display_name,bale_id,service_role,active,notes)
            VALUES (?,?,'GENERAL',1,?)""", (HAMID[1], HAMID[0], ACTOR)).lastrowid
    else:
        staff_id, *details = row
        if details != [HAMID[1], 'GENERAL', 1]:
            raise ValueError('Conflicting Hamid staff identity')
    roster = con.execute('SELECT display_name,staff_id,active FROM fleet.service_staff_roster WHERE staff_id=? OR display_name=?', (staff_id,HAMID[1])).fetchall()
    if not roster:
        con.execute('INSERT INTO fleet.service_staff_roster(display_name,staff_id,active) VALUES (?,?,1)', (HAMID[1],staff_id))
    elif roster != [(HAMID[1],staff_id,1)]:
        raise ValueError('Conflicting Hamid roster identity')


def verify_delta(before, after, mutable):
    if before.keys() != after.keys():
        raise AssertionError('Unexpected schema change')
    for table in before:
        if table in mutable:
            if after[table][:len(before[table])] != before[table]:
                raise AssertionError('Existing rows changed: ' + table)
        elif before[table] != after[table]:
            raise AssertionError('Protected table changed: ' + table)


def change(auth_path, fleet_path, backup_dir=None):
    if not auth_path.exists() or not fleet_path.exists():
        raise FileNotFoundError('Both existing databases are required')
    with closing(sqlite3.connect(auth_path.resolve().as_uri() + '?mode=rw', uri=True, timeout=15)) as con, con:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('ATTACH DATABASE ? AS fleet', (fleet_path.resolve().as_uri() + '?mode=rw',))
        # SQLite's attached-database commit is atomic with rollback journals.
        for schema in ('main', 'fleet'):
            if con.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0] not in ('delete','truncate','persist'):
                raise sqlite3.DatabaseError('Atomic cross-database change requires rollback journals')
        con.execute('BEGIN IMMEDIATE')
        for schema in ('main', 'fleet'):
            if (con.execute(f'PRAGMA {schema}.integrity_check').fetchall() != [('ok',)] or
                    con.execute(f'PRAGMA {schema}.foreign_key_check').fetchall()):
                raise sqlite3.DatabaseError('Pre-change integrity failed: ' + schema)
        before_auth = snapshot(con, 'main', AUTH_MUTABLE)
        before_fleet = snapshot(con, 'fleet', FLEET_MUTABLE)
        backups = []
        if backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=False)
            backups = [backup(auth_path, backup_dir/'authorization.sqlite3'), backup(fleet_path, backup_dir/'fleet.sqlite3')]
        add_hamid(con)
        assign_net_staff(con, USERS, actor=ACTOR)
        roster = con.execute('''SELECT s.bale_id,r.display_name FROM fleet.service_staff_roster r
            JOIN fleet.service_staff s ON s.id=r.staff_id WHERE r.active=1 AND s.active=1 ORDER BY r.id''').fetchall()
        if roster != EXPECTED_ROSTER:
            raise AssertionError('Unexpected active roster; refusing to commit')
        after_auth = snapshot(con, 'main', AUTH_MUTABLE)
        after_fleet = snapshot(con, 'fleet', FLEET_MUTABLE)
        verify_delta(before_auth, after_auth, AUTH_MUTABLE)
        verify_delta(before_fleet, after_fleet, FLEET_MUTABLE)
        for schema in ('main', 'fleet'):
            if (con.execute(f'PRAGMA {schema}.integrity_check').fetchall() != [('ok',)] or
                    con.execute(f'PRAGMA {schema}.foreign_key_check').fetchall()):
                raise sqlite3.DatabaseError('Post-change integrity failed: ' + schema)
        changed = {'authorization': [t for t in before_auth if before_auth[t] != after_auth[t]],
                   'fleet': [t for t in before_fleet if before_fleet[t] != after_fleet[t]]}
    return {'changed_tables': changed, 'backups': backups, 'integrity': 'ok', 'foreign_keys': 'ok',
            'existing_rows_and_history_preserved': True}


def verify_app(auth_path, fleet_path):
    store = AuthorizationStore(auth_path)
    for user in USERS:
        if store.resolve_profile(user,user) != 'net' or NET_STAFF not in store.roles(user):
            raise AssertionError('NET staff routing failed: ' + user)
    if store.capabilities_for_role(NET_STAFF):
        raise AssertionError('Staff membership unexpectedly grants capabilities')
    with patch.object(db, 'DB_PATH', fleet_path):
        options = staff_dispatch.staff_options()
        if [(r['bale_id'],r['display_name']) for r in options] != EXPECTED_ROSTER:
            raise AssertionError('Unexpected service roster: ' + repr(options))
        menus = [staff_dispatch.staff_menu(kind)[0] for kind in ('AIR_FILTER','GREASING','OIL_CHANGE')]
        if len(set(menus)) != 1 or not staff_dispatch.is_recipient(HAMID[0]):
            raise AssertionError('Shared roster or recipient lookup failed')
    return {'staff_options': options, 'shared_menu': menus[0],
            'net_profiles': {u: store.resolve_profile(u,u) for u in USERS},
            'net_staff_capabilities': list(store.capabilities_for_role(NET_STAFF))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    out = args.evidence.resolve()
    if not out.is_relative_to(ROOT/'runtime'):
        raise ValueError('Evidence must be stored below project runtime')
    out.mkdir(parents=True, exist_ok=False)
    auth_copy, fleet_copy = out/'authorization-test.sqlite3', out/'fleet-test.sqlite3'
    backup(AUTH_DB, auth_copy)
    backup(FLEET_DB, fleet_copy)
    result = {'copy_change': change(auth_copy,fleet_copy), 'copy_verification': verify_app(auth_copy,fleet_copy)}
    repeated = change(auth_copy,fleet_copy)
    if any(repeated['changed_tables'].values()):
        raise AssertionError('Repeated application must be a no-op')
    result['idempotent'] = True
    (out/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.apply:
        result['live_change'] = change(AUTH_DB,FLEET_DB,out/'backups')
        result['live_verification'] = verify_app(AUTH_DB,FLEET_DB)
    result.update(applied_to_live=args.apply, messages_sent=0, restart_required=False)
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
