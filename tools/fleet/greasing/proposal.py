"""Greasing-only proposals from actual orange events and subsequent work."""
import re
from .source import SOURCE, read_source, numeric, clean, ordinal, from_ordinal, format_date, format_shift

ACTION = 'GREASING_FULL'
TEXT = 'گریسکاری کامل'


def rule_for(code, name):
    if code.startswith('HD') or 'دامپ' in name:
        return 60, 54, 'ساعت'
    if code.startswith(('EX', 'W', 'D')) or any(word in name for word in ('بیل','لودر','بلدوزر')):
        return 10, 7, 'ساعت'
    if code in {'S1','s1','S2','S3','TA1','TR1'} or any(word in name for word in ('کامیون','خاور','آب پاش','آبپاش')):
        return 8, 6, 'روز'
    return None


def evaluate(machine, cutoff, plan):
    entries = [e for e in machine['entries'] if tuple(e['stamp']) <= tuple(cutoff)]
    rule = rule_for(machine['code'], machine['name'])
    if rule is None:
        return None, 'دورهٔ گریس‌کاری مشخص نیست.'
    events = [e for e in entries if e['color'] == 'ORANGE']
    if not events:
        return None, 'آخرین گریس‌کاری معتبر مشخص نیست؛ مسئول نت باید مبنا را تعیین کند.'
    last = max(events, key=lambda e:e['stamp'])
    following = [e for e in entries if tuple(e['stamp']) > tuple(last['stamp'])]
    suspicious = [e for e in following if e['color'] == 'SUSPECT']
    if suspicious:
        return None, 'رنگ نامعتبر پس از آخرین سرویس: ' + '، '.join(e['cell'] for e in suspicious)
    interval, warning, unit = rule
    if unit == 'ساعت':
        invalid = [e for e in following if clean(e['hours']) not in {'','-'} and not numeric(e['hours'])]
        if invalid:
            return None, 'کارکرد نامعتبر پس از آخرین سرویس: ' + '، '.join(e['cell'] for e in invalid)
        value = sum(e['hours'] for e in following if numeric(e['hours']))
    else:
        value = ordinal(plan) - ordinal(last['stamp'][:2])
    state = 'DUE' if value >= interval else 'NEAR_DUE' if value >= warning else 'OK'
    return {'state':state, 'value':value, 'threshold':interval, 'warning_threshold':warning,
            'unit':unit, 'last_service':format_shift(last['stamp']), 'remaining':max(0,interval-value)}, None


def catalog(source):
    result=[]
    codes={m['code'] for m in source['machines']}
    for m in source['machines']:
        code=m['code']
        # Duplicate model codes (e.g. D155) are distinguished by verified old IDs.
        if m.get('duplicate'):
            legacy=m['legacy_code']
            if not legacy or legacy in codes or sum(x['legacy_code']==legacy for x in source['machines']) != 1:
                continue
            code=legacy
        if not code or rule_for(m['code'],m['name']) is None:
            continue
        result.append({**m, 'order_code':code})
    return result


def item_for(machine):
    name=machine['name']
    if machine.get('duplicate'):
        name += ' (' + machine['code'] + ')'
    return {'machine_code':machine['order_code'], 'machine_name':name,
            'action_code':ACTION, 'action_text':TEXT, 'source_row':machine['row']}


def resolve_items(codes, source=None):
    source=source if source is not None else read_source()
    machines=catalog(source)
    items=[]
    for raw in codes:
        code=clean(raw)
        matches=[m for m in machines if m['order_code']==code]
        if not matches and code.isdecimal():
            matches=[m for m in machines if re.sub(r'^[A-Za-z]+','',m['order_code'])==code]
        if len(matches)!=1:
            raise ValueError(f'کد {code} ناشناخته، غیرفعال یا مبهم است؛ حروف بزرگ و کوچک کد، مانند S1 و s1، مهم‌اند.')
        item=item_for(matches[0])
        if any(i['machine_code']==item['machine_code'] for i in items):
            raise ValueError('کد دستگاه تکراری است: '+code)
        items.append(item)
    if not 1 <= len(items) <= 100:
        raise ValueError('بین ۱ تا ۱۰۰ دستگاه انتخاب کنید.')
    return items


def build_proposal(path=SOURCE, as_of=None):
    source=read_source(path,as_of=as_of)
    cutoff=source['cutoff']
    plan=from_ordinal(ordinal(cutoff[:2])+1)
    proposal={'work_order_type':'GREASING','plan_date':format_date(plan),
              'cutoff':format_shift(cutoff),'source_cutoff':format_shift(cutoff),
              'source_sha256':source['sha256'],'items':[],'warnings':[], 'review':[],
              'source_warnings':source['warnings'],'evaluations':[]}
    available={m['row']:m for m in catalog(source)}
    for m in source['machines']:
        machine=available.get(m['row'])
        label=machine['order_code'] if machine else m['code'] or f"ردیف {m['row']} ({m['name']})"
        suspect=[e['cell'] for e in m['entries'] if e['color']=='SUSPECT']
        if suspect:
            proposal['review'].append({'code':label,'reason':'رنگ مشکوک؛ انجام سرویس محسوب نشد: '+'، '.join(suspect)})
        component,reason=evaluate(m,cutoff,plan)
        if not machine:
            reason = reason or 'کد دستگاه تکراری است و شناسهٔ متمایز معتبر ندارد.'
        if reason:
            proposal['review'].append({'code':label,'reason':reason})
            continue
        item={**item_for(machine),'components':{'greasing':component}}
        proposal['evaluations'].append(item)
        if component['state']=='DUE':
            proposal['items'].append(item)
        elif component['state']=='NEAR_DUE':
            proposal['warnings'].append(f"{label}: {component['value']:g} از {component['threshold']} {component['unit']}؛ {component['remaining']:g} {component['unit']} تا موعد")
    return proposal
