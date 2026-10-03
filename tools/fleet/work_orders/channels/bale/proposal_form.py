"""Small draft editor; persisted work orders are created only after confirmation."""
import re

from tools.fleet.air_filter.rules import ACTIONS, BOTH, OUTER, rule_for


def oil_edit_item(proposal, code):
    from tools.fleet.work_orders.types.oil_change.builder import planning_code
    code = planning_code(code)
    matches = [item for item in proposal['items'] if item['machine_code'] == code or
               (code.isdecimal() and re.sub(r'^[A-Z]+', '', item['machine_code']) == code)]
    if proposal.get('work_order_type') != 'OIL_CHANGE' or len(matches) != 1:
        raise ValueError('کد یک دستگاه از فهرست همین پیشنهاد را وارد کنید؛ ابتدا دستگاه مورد نظر را اضافه کنید.')
    return matches[0]


def edit_oil_interval(proposal, code, interval):
    from tools.fleet.work_orders.types.oil_change.builder import override_interval
    item = oil_edit_item(proposal, code)
    edited = override_interval(item, interval)
    proposal['items'] = [edited if candidate['machine_code'] == item['machine_code'] else candidate
                         for candidate in proposal['items']]
    proposal.setdefault('service_overrides', {})[item['machine_code']] = edited['components']['oil_change']['next_interval']


def creation_metadata(proposal):
    metadata = {key: proposal[key] for key in ('cutoff', 'plan_date', 'source_sha256')}
    if proposal.get('work_order_type') == 'OIL_CHANGE':
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
    icon = '🔴' if due else '🟢'
    lines = [
        f"⏱ {'فاصله تا تاریخ حکم' if field == 'greasing' and component['unit'] == 'روز' else 'کارکرد'}: {component['value']:g} {component['unit']}",
        f"🎯 دوره سرویس: {component['threshold']} {component['unit']}",
        f"⚠️ وضعیت: {_status_text(component)}",
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
    lines += ['', 'دستگاه‌های انتخاب‌شده:']
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
        if item.get('manual_note'):
            lines += [item['manual_note'], '']
        fields = ('greasing',) if greasing else (('inner', 'outer') if item['action_code'] == BOTH else ('outer',))
        components = item.get('components', {})
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
        lines += ['', '⚠️ نزدیک موعد:'] + proposal['warnings']
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
            item = {**item, 'components':calculated['components']}
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
    # A manually selected near-due machine is now inside the draft order and
    # must no longer be repeated under "near due; not in the order".
    proposal['warnings'] = [
        warning for warning in proposal.get('warnings', [])
        if str(warning).split(':', 1)[0].strip().split()[0] not in codes
    ]
