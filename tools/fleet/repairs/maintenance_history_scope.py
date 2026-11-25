"""Pinned scope and journal format for the two historical maintenance reports."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
CASE = ROOT / 'runtime/karamali-history-finalization-20261122'
SCOPE_FILE = CASE / 'owner-approval-scope.json'
SCOPE_SHA256 = '13db049aafcb3acaa98723f4c62310299eeb17ea904e6bcc8a8f0909604f7f30'
OWNER = '455740857'
REPORTER = '1732374823'
OPERATIONS = ('733af30ccdc358f3b3b5a7d1a10f69db', 'f722406dd4905048825b099c45995dc8')
COMMAND_PREFIX = 'ثبت نهایی گزارش‌های کرمعلی '


LIVE_SCOPE = '832f1520a3fb5d0e4e3d2011f885052d81a97d422b854ed411e667f90cd69db9'
NEW_OPERATIONS = ('1234a637b82e5b74bc569c54f8d6eac4', '42038983bebd5883aab42acebf6df05c',
                  'f9f47e7ffa815735b571f1321cc73a47')


def command(pin=None):
    return COMMAND_PREFIX + (pin or SCOPE_SHA256)


def digest(data):
    return sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def load(pin=None):
    from .entry_service import EntryError

    if pin == LIVE_SCOPE:
        document = json.loads((ROOT/'settings/karamali_history_20261124.json').read_text('utf8'))
        frozen = document['scope']
        if (document['scope_sha256'] != pin or digest(encoded(frozen).encode()) != pin
                or document['approval'] is not None):
            raise EntryError('HISTORICAL_SCOPE_INVALID')
        return frozen
    if pin not in (None, SCOPE_SHA256):
        raise EntryError('HISTORICAL_SCOPE_INVALID')
    document = json.loads(SCOPE_FILE.read_text('utf8'))
    scope = document['scope']
    if (document['scope_sha256'] != SCOPE_SHA256 or digest(encoded(scope).encode()) != SCOPE_SHA256
            or document['approval'] is not None or scope['owner_bale_id'] != OWNER
            or scope['original_bale_actor'] != REPORTER or scope['maximum_rows'] != 15
            or scope['publication_1230'] is not False
            or [(r['source_id'], r['date'], r['writer_operation'], len(r['approved_items']),
                 [(i['number'], i['code'], i['status']) for i in r['excluded_items']]) for r in scope['reports']] != [
                ('bale:7949', '1405/08/27', OPERATIONS[0], 8, [(1, '600', 'PENDING_CORRECTION')]),
                ('bale:8009', '1405/08/28', OPERATIONS[1], 7, [(6, '600', 'PENDING_CORRECTION')])]):
        raise EntryError('HISTORICAL_SCOPE_INVALID')
    return scope


def verify_approval(approval):
    from .entry_service import EntryError

    if (not isinstance(approval, dict) or set(approval) != {
            'platform', 'owner', 'chat', 'message_id', 'received_at', 'scope_sha256', 'text'}
            or approval['platform'] != 'bale' or approval['owner'] != OWNER or approval['chat'] != OWNER
            or approval['scope_sha256'] not in (SCOPE_SHA256, LIVE_SCOPE) or approval['text'] != command(approval['scope_sha256'])
            or not isinstance(approval['message_id'], str)
            or not re.fullmatch(r'[1-9][0-9]*', approval['message_id'])):
        raise EntryError('HISTORICAL_OWNER_APPROVAL_INVALID')
    if datetime.fromisoformat(approval['received_at']).utcoffset() is None:
        raise EntryError('HISTORICAL_OWNER_APPROVAL_TIME_INVALID')


def items(spec):
    return [{name: item[name] for name in ('id', 'number', 'record', 'row_uuid')}
            for item in spec['approved_items']]


def verify_receipt(request, result):
    """Validate an owner Writer receipt during readback, replay and Sync."""
    from .entry_service import EntryError

    approval = request.get('owner_approval')
    verify_approval(approval)
    specs = {spec['writer_operation']: spec for spec in load(approval['scope_sha256'])['reports']}
    spec = specs.get(request.get('operation'))
    if (not spec or request.get('source_id') != spec['source_id'] or request.get('report_id') != spec['report_id']
            or request.get('origin_actor') != REPORTER or request.get('date') != spec['date']
            or digest(request['raw_text'].encode('utf8')) != spec['raw_sha256']
            or request.get('approved_items') != items(spec)
            or result.get('owner_approval') != approval or result.get('operation') != spec['writer_operation']
            or result.get('rejected_items') != {} or result.get('date') != spec['date']):
        raise EntryError('HISTORICAL_WRITER_RECEIPT_SCOPE_CHANGED')
    selected = items(spec)
    if ([r['record'] for r in result['records']] != [i['record'] for i in selected]
            or result.get('item_ids') != [i['id'] for i in selected]
            or len(result.get('row_receipts', [])) != len(selected)):
        raise EntryError('HISTORICAL_WRITER_RECEIPT_ITEMS_CHANGED')
    for group, (item, saved, note) in enumerate(zip(selected, result['records'], result['row_receipts'], strict=True)):
        if (not isinstance(note, dict) or set(note) != {'id', 'operation', 'group', 'sheet', 'row_sha256'}
                or note['id'] != item['row_uuid'] or note['operation'] != spec['writer_operation']
                or note['group'] != group or note['sheet'] != saved['sheet']
                or not re.fullmatch(r'[0-9a-f]{64}', note['row_sha256'])
                or type(saved.get('row')) is not int or saved['row'] < 1):
            raise EntryError('HISTORICAL_WRITER_NOTE_RECEIPT_CHANGED')
