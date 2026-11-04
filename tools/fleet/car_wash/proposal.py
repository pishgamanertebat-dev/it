"""Operator-approved rules: 170+ recorded hours or seven calendar days."""
import re

from tools.fleet.greasing.source import clean, format_shift, ordinal
from tools.fleet.oil_change.source import number
from tools.fleet.work_orders.core import operational
from .source import read_source

ACTION = 'CAR_WASH'


def rule_for(code, name):
    if code.startswith(('MZ', 'PR')):
        return 7, 7, 'روز'
    if (code.startswith(('EX', 'HD', 'W', 'D')) or code in {'s1', 'S1', 'S2', 'S3', 'TR1', 'TA1'}
            or any(word in name for word in ('بیل', 'بلدوزر', 'لودر', 'دامپ', 'لیفتراک', 'کامیون', 'خاور'))):
        return 200, 170, 'ساعت'
    return None


def item_for(machine):
    return {'machine_code': machine['code'], 'machine_name': machine['name'],
            'action_code': ACTION, 'action_text': 'کارواش', 'source_row': machine.get('row'),
            'source_code': machine.get('raw_code', machine['code'])}


def evaluate(machine, plan_date):
    rule = rule_for(machine['code'], machine['name'])
    if rule is None:
        return None, 'گروه دستگاه برای محاسبهٔ کارواش مشخص نیست.'
    events = [e for e in machine['entries'] if e['blue']]
    if not events:
        return None, 'آخرین کارواش ثبت آبی ندارد؛ مبنا باید توسط مسئول نت بررسی شود.'
    last = max(events, key=lambda e: tuple(e['stamp']))
    interval, threshold, unit = rule
    following = [e for e in machine['entries'] if tuple(e['stamp']) >= tuple(last['stamp'])]
    if unit == 'ساعت':
        invalid = [e['cell'] for e in following if clean(e['hours']) not in {'', '-'}
                   and (not number(e['hours']) or not 0 <= e['hours'] <= 24)]
        if invalid:
            return None, 'ساعت نامعتبر در دورهٔ پس از آخرین کارواش: ' + '، '.join(invalid)
        # Blank/"-" means no work. Include the blue shift's own numeric hours.
        value = sum(e['hours'] for e in following if number(e['hours']))
    else:
        _, month, day = (int(n) for n in plan_date.split('/'))
        value = ordinal((month, day)) - ordinal(last['stamp'][:2])
        if value < 0:
            return None, 'تاریخ آخرین کارواش پس از تاریخ حکم است.'
    return {'value': value, 'unit': unit, 'threshold': interval,
            'proposal_threshold': threshold, 'last_service': format_shift(last['stamp']),
            'state': 'DUE' if value >= interval else 'NEAR_DUE' if value >= threshold else 'OK'}, None


def build_proposal(path=None, as_of=None, plan_date=None):
    source = read_source(path, as_of)
    plan_date = plan_date or operational.operational_today()
    proposal = {'work_order_type': 'CAR_WASH', 'plan_date': plan_date,
                'source_sha256': source['sha256'],
                'cutoff': format_shift(source['cutoff']) if source['cutoff'] else 'ثبت موجود نیست',
                'source_warnings': source['warnings'], 'items': [], 'review': [],
                'evaluations': [], 'catalog': [], 'manual_items': {}}
    for machine in source['machines']:
        code = machine['code']
        if not code or machine['duplicate']:
            proposal['review'].append({'code': code or f"ردیف {machine['row']}",
                                      'reason': 'کد خالی یا تکراری است؛ هویت دستگاه باید مشخص شود.'})
            continue
        item = item_for(machine)
        proposal['catalog'].append(item)
        component, reason = evaluate(machine, plan_date)
        if reason:
            proposal['review'].append({'code': code, 'reason': reason})
            continue
        item = {**item, 'components': {'car_wash': component}}
        proposal['evaluations'].append(item)
        if component['value'] >= component['proposal_threshold']:
            proposal['items'].append(item)
    return proposal


def resolve_code(raw, catalog):
    code = clean(raw)
    matches = [i for i in catalog if i['machine_code'] == code]
    if not matches:
        matches = [i for i in catalog if i['machine_code'].casefold() == code.casefold()]
    if not matches and code.isdecimal():
        matches = [i for i in catalog if re.sub(r'^[A-Za-z]+', '', i['machine_code']) == code]
    if len(matches) != 1:
        raise ValueError('کد دستگاه ناشناخته یا مبهم است؛ کد کامل دستگاه را انتخاب کنید.')
    return matches[0]
