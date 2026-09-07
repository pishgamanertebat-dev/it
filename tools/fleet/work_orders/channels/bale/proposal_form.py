"""Small draft editor; persisted work orders are created only after confirmation."""
from tools.fleet.air_filter.rules import ACTIONS, BOTH, OUTER, rule_for


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
    return [
        f"{icon} {label}",
        f"⏱ {'فاصله تا تاریخ حکم' if field == 'greasing' and component['unit'] == 'روز' else 'کارکرد'}: {component['value']:g} {component['unit']}",
        f"🎯 دوره سرویس: {component['threshold']} {component['unit']}",
        f"⚠️ وضعیت: {_status_text(component)}",
        f"📅 آخرین سرویس: {component['last_service']}",
    ]


def render(proposal):
    greasing = proposal.get('work_order_type') == 'GREASING'
    label = 'گریس‌کاری' if greasing else 'هواکش'
    lines = [f"پیشنهاد حکم {label} برای {proposal['plan_date']}", f"داده‌ها تا: {proposal['cutoff']}"]
    lines += ['', 'دستگاه‌های انتخاب‌شده:']
    for i,item in enumerate(proposal['items'],1):
        lines += ['', f"{i}) دستگاه {item['machine_code']}", item['action_text'], '']
        fields = ('greasing',) if greasing else (('inner', 'outer') if item['action_code'] == BOTH else ('outer',))
        components = item.get('components', {})
        rendered = False
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
    if proposal.get('source_warnings'):
        lines += ['', '⚠️ وضعیت اطلاعات:'] + proposal['source_warnings']
    if proposal.get('warnings'):
        lines += ['', '⚠️ نزدیک موعد؛ داخل حکم نیستند:'] + proposal['warnings']
    if proposal.get('review'):
        lines += ['', '🔎 نیازمند بررسی داده:'] + [f"{i['code']}: {i['reason']}" for i in proposal['review']]
    confirm_text = 'تایید: ساخت اکسل' if greasing else 'تایید: انتخاب شیفت و ساخت اکسل'
    lines += ['', 'حذف: حذف دستگاه با شمارهٔ ردیف', 'اضافه: افزودن دستگاه با کد', confirm_text, 'انصراف: خروج']
    return '\n'.join(lines)


def add_items(proposal, items, action):
    additions = []
    for item in items:
        if proposal.get('work_order_type') == 'GREASING':
            if action != 'GREASING_FULL':
                raise ValueError('شرح کار گریس‌کاری ثابت است.')
            additions.append({**item,'action_code':action,'action_text':'گریسکاری کامل', 'evidence':'با انتخاب مسئول نت اضافه شد.'})
            continue
        rule = rule_for(item['machine_code'],item['machine_name'])
        if rule is None or ('calendar' in rule and action != OUTER) or (rule.get('together') and action != BOTH):
            raise ValueError('نوع تعویض با قانون دستگاه ' + item['machine_code'] + ' سازگار نیست؛ مزدا/ریچ فقط بیرونی و خاور هر دو با هم است.')
        additions.append({**item,'action_code':action,'action_text':ACTIONS[action]})
    codes = {i['machine_code'] for i in additions}
    proposal['items'] = [i for i in proposal['items'] if i['machine_code'] not in codes] + additions
