"""Shared report captions; compare with the server's local Gregorian day."""
from datetime import date


def jalali_today(today=None):
    import jdatetime
    value = jdatetime.date.fromgregorian(date=today or date.today())
    return f'{value.year:04d}/{value.month:02d}/{value.day:02d}'


def report_caption(title, report_date, *, today=None, latest=False):
    current = jalali_today(today)
    caption = title if latest else f'{title} {report_date}'
    if latest:
        caption += f'\nآخرین روز ثبت‌شده: {report_date}'
    if report_date != current:
        if report_date < current:
            caption += f'\n⚠️ گزارش به‌روز نشده است؛ تاریخ امروز: {current}.'
        else:
            caption += f'\n⚠️ تاریخ گزارش با امروز ({current}) مطابقت ندارد.'
    return caption
