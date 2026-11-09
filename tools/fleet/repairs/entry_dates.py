"""Calendar policy exclusively for the manual Drivers2 form."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tools.fleet.overflow.report import normalize


def jalali_date(value):
    import jdatetime  # Only the Excel subprocess needs this dependency.
    parts = normalize(value).replace('-', '/').replace('.', '/').split('/')
    if len(parts) != 3 or any(not part.isdecimal() for part in parts):
        raise ValueError('تاریخ شمسی معتبر نیست.')
    year, month, day = map(int, parts)
    if not 1300 <= year <= 1499:
        raise ValueError('تاریخ شمسی معتبر نیست.')
    return jdatetime.date(year, month, day)


def formatted(value):
    return f'{value.year:04d}/{value.month:02d}/{value.day:02d}'


def today():
    from tools.fleet.work_orders.core.operational import operational_today
    return operational_today(datetime.now(ZoneInfo('Asia/Tehran')))


def choices(current=None):
    import jdatetime
    day = jalali_date(current or today()).togregorian()
    return [formatted(jdatetime.date.fromgregorian(date=day-timedelta(days=offset))) for offset in range(8)]


def validate_selected(value, current=None):
    selected = formatted(jalali_date(value))
    if selected not in choices(current):
        raise ValueError('تاریخ انتخابی خارج از بازهٔ امروز تا ۷ روز گذشتهٔ تهران است؛ تاریخ را دوباره انتخاب کنید.')
    return selected


def readable(value):
    return value.translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))
