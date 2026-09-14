"""The planning workbook stores the completed cycle, never the next order."""
from collections import Counter
import re

from tools.fleet.greasing.source import clean, format_date, format_shift, from_ordinal, ordinal
from tools.fleet.work_orders.types.oil_change.builder import action_for, get_items, normalize_interval
from .source import SOURCE, PLANNING_SOURCE, read_source, number

WARNING_HOURS = 24
MODEL_MAP = {
    ('دامپتراک','785-5'):'HD785-5', ('دامپتراک','785-7'):'HD785-7',
    ('دامپتراک','465-7'):'HD465-7R', ('دامپتراک','465-7R'):'HD465-7R',
    ('بیل مکانیکی','800-7'):'PC800-7', ('بیل مکانیکی','850-8'):'PC850-8',
    ('بیل مکانیکی','600-8'):'PC600-8', ('بیل مکانیکی','330-9'):'R330-9',
    ('بیل مکانیکی','320-9'):'R320-9', ('بیل مکانیکی','520-9'):'R520-9',
    ('لودر','600-6'):'WA600-6', ('لودر','470-3'):'WA470-3',
    ('بلدوزر','155-2'):'D155A-2', ('بلدوزر','155-6'):'D155A-6',
}

# Confirmed by the operator: planning W151/W152 are the two D155 units.
PLANNING_ALIASES = {'W151':'D151', 'W152':'D152'}


def planning_code(code):
    code = code.upper()
    return PLANNING_ALIASES.get(code, code)


def next_interval(last):
    if number(last) and last == int(last):
        last = int(last)
    return normalize_interval(last) % 2000 + 200


def evaluate(source):
    items, review = [], []
    counts = Counter(planning_code(p['code']) for p in source['plans'])
    seen = set()
    for plan in source['plans']:
        code = planning_code(plan['code'])
        seen.add(code)
        try:
            if not code or counts[code] != 1:
                raise ValueError('کد خالی یا تکراری در برنامه‌ریزی سرویس.')
            model = MODEL_MAP.get((plan['kind'],plan['model'].upper()))
            if model is None:
                raise ValueError('الگوی حکم برای مدل ' + plan['model'] + ' تعریف نشده است.')
            matches = [m for m in source['machines'] if m['code'].upper() == code]
            if not matches and code in {'D151','D152'}:
                matches = [m for m in source['machines'] if m['code']=='D155' and m['legacy_code']==code[1:]]
            if len(matches) != 1:
                raise ValueError('دستگاه در فایل کارکرد پیدا نشد یا کد آن تکراری است.')
            machine = matches[0]
            if machine['errors']:
                raise ValueError('؛ '.join(machine['errors']))
            interval = next_interval(plan['last_interval'])
            action = action_for(model, interval)
            item = get_items([code],{code:action})[0]
            remaining = machine['remaining']
            last_service = format_shift(machine['last_service']) if machine['last_service'] else 'ثبت زرد در سال عملیاتی موجود نیست'
            component = dict(remaining=remaining, current_meter=machine['current_meter'], target_meter=machine['target_meter'],
                             last_interval=interval-200 if interval>200 else 2000, next_interval=interval,
                             last_service=last_service, state='DUE' if remaining<=0 else 'NEAR_DUE' if remaining<=WARNING_HOURS else 'OK')
            items.append(dict(**item, source_row=machine['row'], planning_row=plan['row'], components={'oil_change':component}))
        except ValueError as exc:
            review.append({'code':code or f"ردیف {plan['row']}", 'reason':str(exc)})
    for machine in source['machines']:
        if machine['code'] and machine['code'].upper() not in seen and number(machine['remaining']) and machine['remaining'] <= WARNING_HOURS:
            review.append({'code':machine['code'],'reason':f"مانده {machine['remaining']:g} ساعت؛ در برنامه‌ریزی سرویس نوبت و مدل معتبر ندارد."})
    return items, review


def build_proposal(path=SOURCE, planning_path=PLANNING_SOURCE, as_of=None):
    source = read_source(path,planning_path,as_of=as_of)
    items, review = evaluate(source)
    # Old sources must not backdate a new work order.
    plan_date = format_date(from_ordinal(ordinal(source['as_of'][:2])+1))
    due, warnings = [], []
    for item in items:
        component = item['components']['oil_change']
        if component['state']=='DUE':
            due.append(item)
        elif component['state']=='NEAR_DUE':
            warnings.append(f"{item['machine_code']}: {component['remaining']:g} ساعت تا موعد؛ {item['action_text']}")
    return dict(work_order_type='OIL_CHANGE',plan_date=plan_date,
                cutoff=format_shift(source['cutoff']) if source['cutoff'] else 'ثبت موجود نیست',
                source_sha256=source['sha256'],items=due,warnings=warnings,review=review,
                source_warnings=source['warnings'],evaluations=items)


def resolve_items(codes, source=None):
    items, review = evaluate(source if source is not None else read_source())
    result = []
    for raw in codes:
        code = planning_code(clean(raw))
        matches = [i for i in items if i['machine_code']==code or (code.isdecimal() and re.sub(r'^[A-Z]+','',i['machine_code'])==code)]
        if len(matches)!=1:
            reason = next((r['reason'] for r in review if r['code']==code),'کد ناشناخته یا مبهم است.')
            raise ValueError(code + ': ' + reason)
        if matches[0] in result:
            raise ValueError('کد دستگاه تکراری است: ' + code)
        result.append(matches[0])
    if not 1 <= len(result) <= 100:
        raise ValueError('بین ۱ تا ۱۰۰ دستگاه انتخاب کنید.')
    return result
