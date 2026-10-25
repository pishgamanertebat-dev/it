"""Small draft editor; persisted work orders are created only after confirmation."""
import re
from copy import deepcopy

from tools.fleet.air_filter.rules import ACTIONS, BOTH, OUTER, rule_for


def oil_edit_candidates(proposal):
    """Selected rows take precedence over the unmodified planning snapshot."""
    if proposal.get('work_order_type') != 'OIL_CHANGE':
        return []
    selected = {item['machine_code']: item for item in proposal['items']}
    snapshot = proposal.get('evaluations', [])
    known = {item['machine_code'] for item in snapshot}
    # Confirming a unit must not move it (or its neighbours) to another page.
    return [*(selected.get(item['machine_code'], item) for item in snapshot),
            *(item for code, item in selected.items() if code not in known)]


def oil_edit_item(proposal, code):
    from tools.fleet.work_orders.types.oil_change.builder import planning_code
    code = planning_code(code)
    matches = [item for item in oil_edit_candidates(proposal) if item['machine_code'] == code or
               (code.isdecimal() and re.sub(r'^[A-Z]+', '', item['machine_code']) == code)]
    if len(matches) != 1:
        raise ValueError('دستگاه در فهرست معتبر این پیشنهاد پیدا نشد یا مبهم است؛ از دکمه‌ها انتخاب کنید.')
    return matches[0]


def edit_oil_interval(proposal, code, interval):
    from tools.fleet.work_orders.types.oil_change.builder import override_interval, normalize_interval, parse_action
    item = oil_edit_item(proposal, code)
    interval = normalize_interval(interval)
    selected = any(candidate['machine_code'] == item['machine_code'] for candidate in proposal['items'])
    if selected and interval == parse_action(item['action_code'])[1]:
        rebuild_oil_warnings(proposal)
        return
    baseline = next((i for i in proposal.get('evaluations', []) if i['machine_code'] == item['machine_code']), item)
    planned = parse_action(baseline['action_code'])[1]
    if interval == planned:
        edited = deepcopy(baseline)
        if not selected:
            edited['manual_note'] = 'با انتخاب مسئول نت و با نوبت پیشنهادی برنامه به حکم اضافه شد.'
        proposal.get('service_overrides', {}).pop(item['machine_code'], None)
    else:
        edited = override_interval(item, interval)
        proposal.setdefault('service_overrides', {})[item['machine_code']] = interval
    # Selecting a valid planning row is enough to edit it. Add it only after
    # confirmation, using this proposal's snapshot rather than rereading Excel.
    if not selected:
        proposal['items'].append(edited)
    proposal['items'] = [edited if candidate['machine_code'] == item['machine_code'] else candidate
                         for candidate in proposal['items']]
    rebuild_oil_warnings(proposal)


def apply_oil_edits(proposal, codes, intervals):
    """Validate and apply the complete confirmed edit without partial draft changes."""
    draft = deepcopy(proposal)
    if not codes or len(codes) > 100 or len(set(codes)) != len(codes) or set(codes) != set(intervals):
        raise ValueError('انتخاب دستگاه‌ها معتبر نیست؛ دوباره انتخاب کنید.')
    for code in codes:
        edit_oil_interval(draft, code, intervals[code])
    # The creation contract allows at most 100 units in one selection.
    if len(draft['items']) > 100:
        raise ValueError('حداکثر ۱۰۰ دستگاه در پیشنهاد انتخاب کنید.')
    proposal.clear()
    proposal.update(draft)


def rebuild_oil_warnings(proposal):
    if proposal.get('work_order_type') != 'OIL_CHANGE':
        return
    selected = {i['machine_code'] for i in proposal['items']}
    if 'evaluations' not in proposal:
        # Compatibility for older saved drafts without a calculation snapshot.
        proposal['warnings'] = [w for w in proposal.get('warnings', [])
                                if w.split(':', 1)[0].strip() not in selected]
        return
    proposal['warnings'] = [
        f"{i['machine_code']} : تا موعد بعدی {i['components']['oil_change']['remaining']:g} ساعت، "
        f"برای سرویس {i['components']['oil_change']['next_interval']} ساعتی"
        for i in proposal.get('evaluations', [])
        if i['machine_code'] not in selected and i['components']['oil_change']['state'] == 'NEAR_DUE'
    ]


def creation_metadata(proposal):
    metadata = {key: proposal[key] for key in ('cutoff', 'plan_date', 'source_sha256')}
    for key in ('reissue_reason','operation_id'):
        if proposal.get(key):
            metadata[key] = proposal[key]
    if proposal.get('work_order_type') in {'GREASING','AIR_FILTER'} and proposal.get('revision_of'):
        metadata['revision_of'] = proposal['revision_of']
    if proposal.get('work_order_type') == 'OIL_CHANGE':
        if proposal.get('revision_of'):
            metadata['revision_of'] = proposal['revision_of']
        selected = {item['machine_code'] for item in proposal['items']}
        metadata['service_overrides'] = {code: interval for code, interval in
            proposal.get('service_overrides', {}).items() if code in selected}
    return metadata


def _status_text(component):
    value = component['value']
    threshold = component['threshold']
    unit = component['unit']
    difference = value - threshold
    if difference > 0:
        return f"{difference:g} {unit} تأخیر"
    if difference == 0:
        return "موعد سرویس رسیده"
    return f"{-difference:g} {unit} تا موعد سرویس"


def _component_card(field, component):
    label = 'گریس‌کاری کامل' if field == 'greasing' else ('هواکش داخلی' if field == 'inner' else 'هواکش بیرونی')
    due = component.get('state') == 'DUE'
    near = component.get('state') == 'NEAR_DUE' and field in {'inner', 'outer'}
    icon = '🔴' if due else '🟡' if near else '🟢'
    lines = [
        f"⏱ {'فاصله تا تاریخ حکم' if field == 'greasing' and component['unit'] == 'روز' else 'کارکرد'}: {component['value']:g} {component['unit']}",
        f"🎯 دوره سرویس: {component['threshold']} {component['unit']}",
        f"⚠️ وضعیت: {'نزدیک موعد — پیش‌بینی؛ ' if near else ''}{_status_text(component)}",
        f"📅 آخرین سرویس: {component['last_service']}",
    ]
    # The greasing action is already printed immediately above the component
    # details; repeating "🔴/🟢 گریس‌کاری کامل" adds no information.
    return lines if field == 'greasing' else [f"{icon} {label}", *lines]


def render(proposal):
    greasing = proposal.get('work_order_type') == 'GREASING'
    oil = proposal.get('work_order_type') == 'OIL_CHANGE'
    label = 'تعویض روغن' if oil else 'گریس‌کاری' if greasing else 'هواکش'
    lines = [f"پیشنهاد حکم {label} برای {proposal['plan_date']}", f"داده‌ها تا: {proposal['cutoff']}"]
    air_filter = not oil and not greasing
    lines += ['', 'دستگاه‌های انتخاب‌شده (موعدرسیده یا انتخاب صریح مسئول نت):' if air_filter else 'دستگاه‌های انتخاب‌شده:']
    for i,item in enumerate(proposal['items'],1):
        if oil:
            action_parts = item['action_text'].split(' — ', 1)
            lines += ['', f"{i}) دستگاه: {item['machine_code']}"]
            if len(action_parts) == 2:
                lines += [f"مدل: {action_parts[0]}", f"سرویس مورد نیاز: {action_parts[1]}", '']
            else:
                lines += [f"سرویس مورد نیاز: {item['action_text']}", '']
        else:
            lines += ['', f"{i}) دستگاه {item['machine_code']}", item['action_text'], '']
        if air_filter and not item.get('manual_note') and any(
                c.get('state') == 'DUE' for c in item.get('components', {}).values()):
            lines.append('🔴 موعدرسیده')
        if item.get('manual_note'):
            lines += [item['manual_note'], '']
        fields = ('greasing',) if greasing else (('inner', 'outer') if item['action_code'] == BOTH else ('outer',))
        components = item.get('components', {})
        if air_filter:
            fields = tuple(k for k in ('inner', 'outer') if k in fields or
                           components.get(k, {}).get('state') == 'NEAR_DUE')
        rendered = False
        if oil and components.get('oil_change'):
            c = components['oil_change']
            remaining = c['remaining']
            if remaining < 0:
                due_text = f"🔴 {abs(remaining):g} ساعت از موعد تعویض گذشته است"
            elif remaining == 0:
                due_text = "🔴 موعد تعویض روغن رسیده است"
            else:
                due_text = f"🟢 {remaining:g} ساعت تا موعد تعویض روغن باقی مانده است"
            lines += [f"ساعت‌کار فعلی دستگاه: {c['current_meter']:g} ساعت",
                      f"ساعت‌کار موعد تعویض: {c['target_meter']:g} ساعت",
                      f"وضعیت: {due_text}",
                      f"نوبت قبلی: سرویس {c['last_interval']} ساعتی",
                      f"نوبت این حکم: سرویس {c['next_interval']} ساعتی",
                      f"آخرین ثبت تعویض روغن: {c['last_service']}"]
            rendered = True
            fields = ()
        for field in fields:
            component = components.get(field)
            if component and component.get('value') is not None:
                if rendered:
                    lines.append('')
                lines.extend(_component_card(field, component))
                rendered = True
        if not rendered and item.get('evidence'):
            lines.append(item['evidence'])
        lines.append('━━━━━━━━━━━━━━')
    if not proposal['items']:
        lines.append('هیچ دستگاهی انتخاب نشده است.')
    source_warnings = proposal.get('source_warnings', [])
    if greasing:
        source_warnings = [warning for warning in source_warnings
                           if 'آخرین داده قدیمی‌تر از آخرین شیفت قابل محاسبه است' not in warning]
    if source_warnings and not oil:
        lines += ['', '⚠️ وضعیت اطلاعات:'] + source_warnings
    if proposal.get('warnings'):
        heading = (f"⚠️ نزدیک موعد — پیش‌بینی تا {proposal.get('forecast_horizon_hours', 3):g} ساعت کارکرد دیگر:"
                   if air_filter and 'forecast_horizon_hours' in proposal else '⚠️ نزدیک موعد:')
        lines += ['', heading] + proposal['warnings']
        if air_filter and 'forecast_horizon_hours' in proposal:
            lines += ['این موارد اختیاری‌اند و به حکم اضافه نشده‌اند؛ برای انتخاب از «اضافه» استفاده کنید و سپس تأیید کنید.']
    if proposal.get('review'):
        lines += ['', '🔎 نیازمند بررسی داده:'] + [f"{i['code']}: {i['reason']}" for i in proposal['review']]
    if not greasing and not oil:
        lines += ['', 'پس از تایید، شیفت حکم را انتخاب می‌کنید.']
    lines += ['', 'عملیات مورد نظر را از دکمه‌های زیر انتخاب کنید.']
    return '\n'.join(lines)


def add_items(proposal, items, action):
    additions = []
    for item in items:
        # Reuse the calculation already shown in this proposal. This keeps a
        # manually selected machine visually identical to automatic choices
        # and avoids mixing data from a newer source snapshot.
        evaluated = proposal.get('evaluations', [])
        calculated = next((candidate for candidate in evaluated
                           if candidate.get('machine_code') == item['machine_code']), None)
        if calculated is None:
            calculated = next((candidate for candidate in proposal.get('machines', [])
                               if candidate.get('code') == item['machine_code']), None)
        if calculated and calculated.get('components'):
            item = (deepcopy(calculated) if proposal.get('work_order_type') == 'OIL_CHANGE'
                    else {**item, 'components':calculated['components']})
        if proposal.get('work_order_type') == 'OIL_CHANGE':
            if item['machine_code'] in proposal.get('service_overrides', {}):
                from tools.fleet.work_orders.types.oil_change.builder import override_interval
                additions.append(override_interval(item, proposal['service_overrides'][item['machine_code']]))
                continue
            additions.append({**item, 'manual_note':'با انتخاب مسئول نت اضافه شد؛ نوبت سرویس از برنامه‌ریزی خوانده شد.'})
            continue
        if proposal.get('work_order_type') == 'GREASING':
            if action != 'GREASING_FULL':
                raise ValueError('شرح کار گریس‌کاری ثابت است.')
            additions.append({**item,'action_code':action,'action_text':'گریسکاری کامل',
                              'manual_note':'با انتخاب مسئول نت اضافه شد.'})
            continue
        rule = rule_for(item['machine_code'],item['machine_name'])
        if rule is None or ('calendar' in rule and action != OUTER) or (rule.get('together') and action != BOTH):
            raise ValueError('نوع تعویض با قانون دستگاه ' + item['machine_code'] + ' سازگار نیست؛ مزدا/ریچ فقط بیرونی و خاور/لودر/بلدوزر هر دو با هم است.')
        additions.append({**item,'action_code':action,'action_text':ACTIONS[action],
                          'manual_note':'با انتخاب مسئول نت اضافه شد.'})
    codes = {i['machine_code'] for i in additions}
    proposal['items'] = [i for i in proposal['items'] if i['machine_code'] not in codes] + additions
    if proposal.get('work_order_type') == 'OIL_CHANGE':
        rebuild_oil_warnings(proposal)
        return
    # A manually selected near-due machine is now inside the draft order and
    # must no longer be repeated under "near due; not in the order".
    proposal['warnings'] = [
        warning for warning in proposal.get('warnings', [])
        if str(warning).split(':', 1)[0].strip().split()[0] not in codes
    ]
