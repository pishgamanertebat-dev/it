"""Manual greasing orders: known fleet machines, always full greasing."""
import re
from tools.fleet.work_orders.core.db import connect_db
from tools.fleet.work_orders.core.paths import WORK_ORDER_TEMPLATE_ROOT
from tools.fleet.work_orders.core.excel_document import build_document as render_excel

ACTION_CODE = 'GREASING_FULL'
ACTION_TEXT = 'گریسکاری کامل'


def get_items(codes, actions=None):
    if actions is not None:
        from tools.fleet.greasing.proposal import resolve_items
        if any(v != ACTION_CODE for v in actions.values()):
            raise ValueError('شرح کار گریس‌کاری همیشه «گریسکاری کامل» است.')
        items = resolve_items(codes)
        if set(actions) != {i['machine_code'] for i in items}:
            raise ValueError('شرح کار دستگاه‌های انتخاب‌شده کامل نیست.')
        con = connect_db()
        try:
            for item in items:
                # Explicit exact identity: lowercase s1 must never become S1.
                matches = con.execute('SELECT id FROM machines WHERE canonical_code COLLATE BINARY = ?', (item['machine_code'],)).fetchall()
                item['machine_id'] = matches[0]['id'] if len(matches) == 1 else None
        finally:
            con.close()
        return items
    con = connect_db()
    try:
        machines = [dict(r) for r in con.execute('SELECT * FROM machines')]
    finally:
        con.close()
    items = []
    seen = set()
    for raw in codes:
        code = str(raw).strip().upper().translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹','0123456789'))
        matches = [m for m in machines if m['canonical_code'].upper() == code]
        if not matches and code.isdecimal():
            matches = [m for m in machines if re.sub(r'^[A-Z]+', '', m['canonical_code'].upper()) == code]
        if len(matches) != 1:
            raise ValueError(f'کد دستگاه ناشناخته یا مبهم است: {code}؛ کد کامل دستگاه را وارد کنید.')
        machine = matches[0]
        canonical = machine['canonical_code'].upper()
        if canonical in seen:
            raise ValueError('کد دستگاه تکراری: ' + code)
        seen.add(canonical)
        items.append({'machine_code':canonical,
                      'machine_name':machine.get('machine_type_hint') or canonical,
                      'action_code':ACTION_CODE,'action_text':ACTION_TEXT})
    if not items or len(items) > 100:
        raise ValueError('بین ۱ تا ۱۰۰ دستگاه انتخاب کنید.')
    if actions is not None and (set(actions) != seen or any(v != ACTION_CODE for v in actions.values())):
        raise ValueError('شرح کار گریس‌کاری همیشه «گریسکاری کامل» است.')
    return items


def build_document(*, output_path, jalali_date, items, shift='صبح'):
    return render_excel(output_path=output_path, jalali_date=jalali_date, items=items,
                        template_path=WORK_ORDER_TEMPLATE_ROOT / 'air_filter' / 'air_filter_work_order_v1.xlsx',
                        template_sheet='Sheet1 (486)', title='لیست روزانه گریسکاری')
