"""Local operator commands. Not exposed to messaging/agent toolsets."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .store import AuthorizationStore, DB_PATH, OFFICE_SUPERVISOR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=DB_PATH)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('migrate')
    commands.add_parser('resolve')
    commands.add_parser('migrate-mechanical')
    recipients=commands.add_parser('resolve-recipients')
    recipients.add_argument('--capability',required=True)
    assign = commands.add_parser('assign')
    assign.add_argument('--bale-user-id', required=True)
    assign.add_argument('--role', default=OFFICE_SUPERVISOR)
    assign.add_argument('--actor', required=True)
    assign.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    store = AuthorizationStore(args.database)
    if args.command == 'migrate':
        print('Migration complete. Backup:', store.migrate())
    elif args.command == 'migrate-mechanical':
        print('Mechanical migration complete. Backup:',store.migrate_mechanical())
    elif args.command == 'resolve-recipients':
        print(json.dumps(asdict(store.resolve_active_recipients(args.capability))))
    elif args.command == 'assign':
        print('Pre-assignment backup:', store.backup())
        store.assign_role(args.bale_user_id, args.role, actor=args.actor, replace=args.replace)
        print('Role assigned; registration and profiles unchanged.')
    else:
        print(json.dumps(asdict(store.resolve_daily_recipient())))


if __name__ == '__main__':
    main()
