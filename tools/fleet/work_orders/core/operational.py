"""Request-time work-order validity. No expiry migration or historical rewrite."""
from datetime import datetime
from zoneinfo import ZoneInfo

EXPIRED_MESSAGE = 'مهلت عملیاتی این حکم پایان یافته است'


class OperationalDayExpired(ValueError):
    pass


def operational_today(now=None):
    local = datetime.now(ZoneInfo('Asia/Tehran')) if now is None else now.astimezone(ZoneInfo('Asia/Tehran'))
    # The Gateway has no Excel/jdatetime packages. Keep this request-time guard
    # dependency-free; tests compare it with jdatetime across calendar boundaries.
    gy, gm, gd = local.year, local.month, local.day
    elapsed = (0,31,59,90,120,151,181,212,243,273,304,334)
    leap_year = gy + (gm > 2)
    days = 355666 + 365*gy + (leap_year+3)//4 - (leap_year+99)//100 + (leap_year+399)//400 + gd + elapsed[gm-1]
    jy = -1595 + 33*(days//12053)
    days %= 12053
    jy += 4*(days//1461)
    days %= 1461
    if days > 365:
        jy += (days-1)//365
        days = (days-1)%365
    jm, jd = (1+days//31, 1+days%31) if days < 186 else (7+(days-186)//30, 1+(days-186)%30)
    return f'{jy:04d}/{jm:02d}/{jd:02d}'


def require_date(value):
    today = operational_today()
    if value != today:
        raise OperationalDayExpired(EXPIRED_MESSAGE if str(value) < today else 'تاریخ عملیاتی حکم باید امروز باشد.')


def require_order(row):
    if row['work_order_type'] in {'OIL_CHANGE', 'GREASING', 'AIR_FILTER'}:
        require_date(row['jalali_date'])


def require_reviewable(con, number, actor=None):
    from .oil_safety import current
    row = current(con, number)
    if actor is not None and row['created_by'] != f'bale:{actor}':
        raise ValueError('این حکم متعلق به حساب شما نیست.')
    if row['status'] not in {'FILE_READY', 'ASSIGNED', 'APPROVED'}:
        raise ValueError('این حکم قابل بازبینی نیست؛ وضعیت: ' + row['status'])
    dispatch = con.execute('SELECT state FROM service_work_order_dispatch WHERE work_order_no=?', (number,)).fetchone()
    if dispatch and dispatch['state'] in {'SENDING', 'UNCERTAIN', 'SENT'}:
        raise ValueError('ابتدا نتیجهٔ ارسال قبلی را در «بررسی ارسال» تعیین تکلیف کنید.')
    return row
