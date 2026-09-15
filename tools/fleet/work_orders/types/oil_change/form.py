"""Manual oil-order input, independent from automatic service proposals."""
from datetime import date, datetime, timedelta, timezone
from tools.fleet.work_orders.types.oil_change.builder import normalize_code, normalize_interval, action_for

MODELS = {'1': 'HD785-5', '2': 'HD465-7R', '3': 'HD785-7', '4': 'PC800-7', '5': 'R330-9', '6': 'PC850-8', '7': 'WA600-6', '8': 'WA470-3',
          '9': 'D155A-2', '10': 'D155A-6', '11': 'R320-9', '12': 'R520-9', '13': 'PC600-8', '14': 'PC1250-8'}
MODEL_PROMPT = 'مدل دستگاه را انتخاب کنید:\n1) 785 خط ۵\n2) 465 خط ۷ (465-7R)\n3) 785 خط ۷\n4) 800 خط ۷\n5) 330 خط ۹\n6) 850 خط ۸\n7) لودر 600 خط ۶\n8) لودر 470 خط ۳\n9) بلدوزر 155 خط ۲\n10) بلدوزر 155 خط ۶\n11) بیل 320 خط ۹\n12) بیل 520 خط ۹\n13) بیل 600 خط ۸\n14) بیل 1250 خط ۸'
CODE_PROMPT = 'کد یک دستگاه را وارد کنید؛ مانند HD701 یا 701.'
INTERVAL_PROMPT = 'نوبت سرویس را وارد کنید: ۲۰۰، ۴۰۰، ۶۰۰، ۸۰۰، ۱۰۰۰، ۱۲۰۰، ۱۴۰۰، ۱۶۰۰، ۱۸۰۰ یا ۲۰۰۰.'


def code_prompt(model):
    if model.startswith('D'):
        return 'کد یک بلدوزر را وارد کنید؛ مانند D152 یا 152.'
    if model.startswith('WA'):
        return 'کد یک لودر را وارد کنید؛ مانند W601 یا 601.'
    return CODE_PROMPT if model.startswith('HD') else 'کد یک بیل را وارد کنید؛ مانند EX801 یا 801.'


def order_date():
    today = datetime.now(timezone(timedelta(hours=3, minutes=30))).date()
    day = (today - date(2026, 3, 21)).days
    if not 0 <= day < 365:
        raise ValueError('تاریخ فعلی خارج از سال عملیاتی ۱۴۰۵ است.')
    for month, length in enumerate((31,) * 6 + (30,) * 5 + (29,), 1):
        if day < length:
            return f'1405/{month:02d}/{day + 1:02d}'
        day -= length


def create_request(session, text, actor):
    interval = normalize_interval(text)
    code = normalize_code(session.machine_codes[0], session.oil_model)
    return {'action': 'create', 'bale_id': actor, 'work_order_type': 'OIL_CHANGE',
            'machine_codes': [code], 'jalali_date': order_date(), 'shift': 'روزانه',
            'item_actions': {code: action_for(session.oil_model, interval)}}
