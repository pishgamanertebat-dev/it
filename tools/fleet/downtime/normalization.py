"""Persian text/digit normalization, Jalali calendar helpers, Excel time conversion, and note theme extraction.

Pure Python standard library only so read-only query paths work seamlessly in both
the project .venv and Hermes's isolated Python runtime without heavy imports.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re
import unicodedata
from typing import Any

DIGITS_TO_ASCII = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
DIGITS_TO_PERSIAN = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

JALALI_MONTH_NAMES: dict[int, str] = {
    1: "فروردین",
    2: "اردیبهشت",
    3: "خرداد",
    4: "تیر",
    5: "مرداد",
    6: "شهریور",
    7: "مهر",
    8: "آبان",
    9: "آذر",
    10: "دی",
    11: "بهمن",
    12: "اسفند",
}

JALALI_MONTH_ALIASES: dict[str, int] = {
    "فروردین": 1,
    "اردیبهشت": 2,
    "خرداد": 3,
    "تیر": 4,
    "مرداد": 5,
    "شهریور": 6,
    "مهر": 7,
    "آبان": 8,
    "ابان": 8,
    "آذر": 9,
    "اذر": 9,
    "دی": 10,
    "بهمن": 11,
    "اسفند": 12,
}

# Canonical cause keys and Persian labels
CAUSE_FIELDS: tuple[tuple[str, str], ...] = (
    ("repair_minutes", "تعمیرات"),
    ("no_load_minutes", "بی‌باری"),
    ("blast_delay_minutes", "تأخیر انفجار"),
    ("no_loader_minutes", "نبود بارگیر"),
    ("driver_delay_minutes", "تأخیر راننده"),
)

CAUSE_LABEL_BY_KEY: dict[str, str] = dict(CAUSE_FIELDS)

# Deterministic Persian note topic rules (evaluated on normalized Persian text).
# Note topics are qualitative tags and NEVER replace or split numeric downtime causes.
NOTE_TOPIC_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "blast_delay",
        "تأخیر انفجار",
        (r"انفجار", r"آتش\s*باری", r"اتش\s*باری"),
    ),
    (
        "driver_delay_or_absence",
        "نبود یا تأخیر راننده",
        (
            r"نداشتن\s*راننده",
            r"نبود\s*راننده",
            r"تاخیر\s*راننده",
            r"مرخصی\s*راننده",
            r"غیبت\s*راننده",
            r"بدون\s*راننده",
        ),
    ),
    (
        "oil_leak",
        "نشتی روغن / روغن‌ریزی",
        (r"نشتی\s*روغن", r"روغن\s*ریزی", r"نشت\s*روغن"),
    ),
    (
        "engine_issue",
        "خرابی یا تعمیر موتور",
        (
            r"خرابی\s*موتور",
            r"تعمیر(?:ات)?\s*موتور",
            r"موتور\s*(?:خراب|باز|پایین|روشن\s*نمی)",
            r"واشر\s*سر\s*سیلندر",
            r"رادیاتور",
            r"اب\s*رادیاتور",
            r"آب\s*رادیاتور",
            r"نشتی\s*(?:اب|آب)",
            r"توربو",
            r"انژکتور",
            r"سوپر",
        ),
    ),
    (
        "hydraulic_issue",
        "مشکلات هیدرولیک",
        (
            r"هیدرولیک",
            r"شیلنگ",
            r"شلنگ",
            r"امپر\s*روغن",
            r"آمپر\s*روغن",
            r"داغ\s*کردن\s*روغن",
        ),
    ),
    (
        "pump_issue",
        "خرابی پمپ",
        (r"پمپ", r"پمپچه"),
    ),
    (
        "jack_cylinder_issue",
        "خرابی جک و بالابر",
        (r"\bجک\b", r"جک\s*(?:های|پاکت|بالابر|بوم|استیک|فرمان|دامپ)", r"سیلندر\s*(?:جک|بالابر)"),
    ),
    (
        "swing_issue",
        "خرابی گردان",
        (r"گردان", r"اسوینگ", r"swing"),
    ),
    (
        "welding_body_chassis",
        "جوشکاری و آهن‌کشی (دامپ / شاسی / پاکت)",
        (r"جوشکاری", r"اهن\s*کشی", r"آهن\s*کشی", r"شاسی", r"پاکت", r"ناخن", r"نردبان\s*باک", r"اماده\s*نبودن\s*دامپ"),
    ),
    (
        "tire_puncture",
        "پنچری و لاستیک",
        (r"پنچر", r"لاستیک", r"تایر", r"رینگ", r"باد\s*چرخ", r"تنظیم\s*باد"),
    ),
    (
        "drivetrain_brakes_undercarriage",
        "گیربکس، ترمز، چرخ و زیربندی",
        (
            r"دنده",
            r"گیربکس",
            r"قفل\s*(?:کردن|شدن)\s*چرخ",
            r"ترمز",
            r"زیر\s*بندی",
            r"دیفرانسیل",
            r"کلاچ",
            r"اکسل",
            r"چرخ",
            r"زنجیر",
            r"پین\s*و\s*بوش",
        ),
    ),
    (
        "electrical_issue",
        "مشکلات سیستم برق، دینام و استارت",
        (
            r"برق",
            r"دینام",
            r"استارت",
            r"باتری",
            r"باطری",
            r"سیم\s*کشی",
            r"سنسور",
            r"کولر",
            r"بخاری",
            r"چراغ",
            r"فیوز",
        ),
    ),
    (
        "service_pm",
        "سرویس‌کاری و تعویض روغن/فیلتر",
        (r"تعویض\s*روغن", r"سرویس", r"فیلتر", r"گریس"),
    ),
    (
        "weather_hse",
        "بارندگی، لغزندگی و تعطیلی HSE",
        (r"بارندگی", r"لغزندگی", r"hse", r"هوا", r"طوفان", r"برف", r"یخ"),
    ),
    (
        "preparation_standby",
        "آماده‌سازی / آماده به کار",
        (r"اماده\s*(?:به\s*کار|بکار|سازی)", r"آماده\s*(?:به\s*کار|بکار|سازی)", r"دستگاه\s*خواب"),
    ),
    (
        "no_load_material",
        "اتمام بار / بی‌باری",
        (r"اتمام\s*بار", r"اتعام\s*بار", r"بی\s*باری", r"نبود\s*بار"),
    ),
    (
        "workshop_general",
        "تعمیرگاه / مشکل کلی",
        (r"تعمیرگاه", r"مشکل\s*کلی"),
    ),
    (
        "unspecified_cause",
        "علت اعلام‌نشده / نامشخص",
        (r"اعلام\s*نشده", r"گفته\s*نشده", r"نامشخص"),
    ),
)

_COMPILED_TOPIC_RULES: tuple[tuple[str, str, tuple[re.Pattern[str], ...]], ...] = tuple(
    (key, label, tuple(re.compile(pat, re.IGNORECASE) for pat in patterns))
    for key, label, patterns in NOTE_TOPIC_RULES
)

NOTE_TOPIC_LABELS: dict[str, str] = {key: label for key, label, _ in NOTE_TOPIC_RULES}


def to_ascii_digits(value: Any) -> str:
    """Convert Persian and Arabic-Indic digits to ASCII digits."""
    if value is None:
        return ""
    return str(value).translate(DIGITS_TO_ASCII)


def to_persian_digits(value: Any) -> str:
    """Convert ASCII digits to Persian digits for Persian display."""
    if value is None:
        return ""
    return str(value).translate(DIGITS_TO_PERSIAN)


def normalize_persian(value: Any, *, preserve_half_space: bool = False) -> str:
    """Normalize Persian text: unify ی/ي, ک/ك, آ/ا where appropriate, digits, whitespace."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = text.translate(DIGITS_TO_ASCII)
    text = text.replace("\u064a", "\u06cc").replace("\u0643", "\u06a9")  # ي -> ی, ك -> ک
    text = text.replace("\u0629", "\u0647")  # ة -> ه
    text = text.replace("\ufeff", "").replace("\u200e", "").replace("\u200f", "")
    text = text.replace("\u0640", "")  # kashida
    if not preserve_half_space:
        text = text.replace("\u200c", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_key(value: Any) -> str:
    """Compact lowercase comparison key for Persian/ASCII labels."""
    text = normalize_persian(value).replace("آ", "ا").replace("أ", "ا").replace("إ", "ا")
    text = re.sub(r"[\s_\-ـ.،,:;/\\()]+", "", text)
    return text.casefold()


def normalize_raw_code(value: Any) -> str:
    """Normalize a raw equipment code cell without guessing a fleet identity."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return to_ascii_digits(str(value)).strip()
    text = normalize_persian(value)
    if not text:
        return ""
    # Uppercase ASCII letters while keeping Persian words like گریدر intact
    if re.fullmatch(r"[A-Za-z0-9\-_]+", text):
        return text.upper()
    return text


def format_minutes_hhmm(minutes: int | float | None, *, persian_digits: bool = False) -> str:
    """Format integer minutes as H:MM (never wraps at 24h!)."""
    if minutes is None:
        return "—"
    total = int(round(minutes))
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours, mins = divmod(total, 60)
    formatted = f"{sign}{hours}:{mins:02d}"
    return to_persian_digits(formatted) if persian_digits else formatted


def format_minutes_hours_decimal(minutes: int | float | None, decimals: int = 1) -> float:
    """Convert minutes to rounded decimal hours for chart axes/sorting."""
    if minutes is None:
        return 0.0
    return round(float(minutes) / 60.0, decimals)


def is_jalali_leap_year(year: int) -> bool:
    """Exact 33-year cycle / astronomical leap check for modern Jalali years (1300-1500)."""
    # Verified against jdatetime / Kazimierz Borkowski algorithm
    breaks = (-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178)
    bl = len(breaks)
    jp = breaks[0]
    jump = 0
    for i in range(1, bl):
        jm = breaks[i]
        jump = jm - jp
        if year < jm:
            break
        jp = jm
    n = year - jp
    if jump - n < 6:
        n = n - jump + ((jump + 4) // 33) * 33
    leap = (((n + 1) % 33) - 1) % 4
    if leap == -1:
        leap = 4
    return leap == 0


def jalali_month_days(year: int, month: int) -> int:
    """Return number of calendar days in a Jalali month."""
    if not 1 <= month <= 12:
        raise ValueError(f"Invalid Jalali month: {month}")
    if month <= 6:
        return 31
    if month <= 11:
        return 30
    return 30 if is_jalali_leap_year(year) else 29


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> date:
    """Convert valid Jalali date (jy, jm, jd) to standard Gregorian datetime.date (pure stdlib)."""
    max_d = jalali_month_days(jy, jm)
    if not 1 <= jd <= max_d:
        raise ValueError(f"Invalid Jalali date: {jy:04d}/{jm:02d}/{jd:02d}")
    jy_adj = jy + 1595
    days = (
        -355668
        + (365 * jy_adj)
        + ((jy_adj // 33) * 8)
        + (((jy_adj % 33) + 3) // 4)
        + jd
        + ((jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186)
    )
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    is_g_leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    sal_a = [0, 31, 29 if is_g_leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    while gm <= 12 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return date(gy, gm, gd)


def parse_period(value: Any) -> str:
    """Normalize a Jalali period expression into 'YYYY-MM' (e.g., '1405-08')."""
    text = normalize_persian(value)
    if not text:
        raise ValueError("Empty period")
    # Direct numeric YYYY-MM or YYYY/MM
    m = re.fullmatch(r"(1[34]\d{2})[-/](\d{1,2})", text)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"Month out of range: {value}")
        return f"{year:04d}-{month:02d}"
    # Month name + year (e.g., 'آبان 1405' or 'ابان ۱۴۰۵')
    year_m = re.search(r"\b(1[34]\d{2})\b", text)
    found_month = None
    for alias, mnum in JALALI_MONTH_ALIASES.items():
        if alias in text or alias.replace("آ", "ا") in text.replace("آ", "ا"):
            found_month = mnum
            break
    if year_m and found_month is not None:
        return f"{int(year_m.group(1)):04d}-{found_month:02d}"
    raise ValueError(f"Unsupported period format: {value!r}")


def period_label_fa(period: str, *, persian_digits: bool = True) -> str:
    """Human-readable Persian period name, e.g., 'آبان ۱۴۰۵'."""
    year_s, month_s = period.split("-")
    mname = JALALI_MONTH_NAMES[int(month_s)]
    label = f"{mname} {year_s}"
    return to_persian_digits(label) if persian_digits else label


def detect_month_from_filename(filename: str) -> tuple[int | None, int | None]:
    """Extract optional (year, month) hint from a Persian or ASCII workbook filename.

    Never trusted alone: caller MUST verify against actual worksheet dates.
    """
    text = normalize_persian(filename)
    year_m = re.search(r"(1[34]\d{2})", text)
    year = int(year_m.group(1)) if year_m else None
    month = None
    m_num = re.search(r"1[34]\d{2}[-_/\s](\d{1,2})\b", text)
    if m_num and 1 <= int(m_num.group(1)) <= 12:
        month = int(m_num.group(1))
    if month is None:
        plain = text.replace("آ", "ا")
        for alias, mnum in sorted(JALALI_MONTH_ALIASES.items(), key=lambda kv: -len(kv[0])):
            if alias.replace("آ", "ا") in plain:
                month = mnum
                break
    return year, month


def parse_sheet_month_day(sheet_name: str) -> tuple[int, int] | None:
    """Parse daily worksheet names like '08.30', '08.9', '07.05 ', '8/14', '1405-08-20'."""
    text = normalize_persian(sheet_name)
    m = re.fullmatch(r"(?:1[34]\d{2}[./-])?(\d{1,2})[./-](\d{1,2})", text)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day


def parse_jalali_date(value: Any, *, default_year: int | None = None) -> tuple[str, str]:
    """Parse Jalali date string into canonical ('YYYY/MM/DD', 'ISO-YYYY-MM-DD').

    Accepts '1405/08/20', '1405-8-9', '20 آبان 1405', '۲۰ ابان ۱۴۰۵', etc.
    """
    text = normalize_persian(value)
    if not text:
        raise ValueError("Empty Jalali date")
    m = re.fullmatch(r"(1[34]\d{2})[/\-.](\d{1,2})[/\-.](\d{1,2})", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        iso = jalali_to_gregorian(y, mo, d).isoformat()
        return f"{y:04d}/{mo:02d}/{d:02d}", iso
    # Natural Persian: "20 آبان 1405" or "20 آبان"
    plain = text.replace("آ", "ا")
    for alias, mnum in sorted(JALALI_MONTH_ALIASES.items(), key=lambda kv: -len(kv[0])):
        a_plain = alias.replace("آ", "ا")
        pat = re.search(rf"\b(\d{{1,2}})\s*{re.escape(a_plain)}(?:\s*(1[34]\d{{2}}))?\b", plain)
        if pat:
            d = int(pat.group(1))
            y = int(pat.group(2)) if pat.group(2) else default_year
            if y is None:
                raise ValueError(f"Year missing in Jalali date: {value!r}")
            iso = jalali_to_gregorian(y, mnum, d).isoformat()
            return f"{y:04d}/{mnum:02d}/{d:02d}", iso
    raise ValueError(f"Unsupported Jalali date: {value!r}")


def parse_excel_duration_minutes(value: Any) -> tuple[int | None, bool, str]:
    """Convert an Excel duration/time cell to exact integer minutes.

    Returns (minutes_or_none, is_empty, value_kind).
    Never wraps >=24h durations; distinguishes empty cells from explicit 0:00.
    """
    if value is None:
        return None, True, "empty"
    if isinstance(value, bool):
        return None, False, f"invalid_bool:{value}"
    if isinstance(value, timedelta):
        total_sec = value.total_seconds()
        if total_sec < 0:
            return round(total_sec / 60.0), False, "negative_timedelta"
        return int(round(total_sec / 60.0)), False, "timedelta"
    if isinstance(value, time):
        mins = value.hour * 60 + value.minute + (value.second / 60.0)
        return int(round(mins)), False, "time"
    if isinstance(value, datetime):
        # Excel stores durations >= 24h as 1899-12-30 + delta (or 1900-01-01...)
        if value.year in (1899, 1900):
            delta = value - datetime(1899, 12, 30)
            return int(round(delta.total_seconds() / 60.0)), False, "excel_datetime_duration"
        return None, False, f"invalid_datetime:{value.isoformat()}"
    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):
            return None, False, "invalid_nonfinite"
        if value < 0:
            # Excel fractional day if abs <= 10, else invalid negative
            return int(round(value * 24 * 60)), False, "negative_number"
        if value == 0:
            return 0, False, "numeric_zero"
        # Excel serial time is fraction of a 24h day (e.g. 0.458333 = 11:00, 1.25 = 30:00)
        return int(round(value * 24 * 60)), False, "excel_serial_day"
    if isinstance(value, str):
        cleaned = normalize_persian(value)
        if not cleaned or cleaned in {"-", "_", "—"}:
            return None, True, "empty_string"
        if cleaned.startswith("#"):
            return None, False, f"excel_error:{cleaned}"
        m = re.fullmatch(r"(-?)(\d+):(\d{1,2})(?::(\d{1,2}))?", cleaned)
        if m:
            sign = -1 if m.group(1) == "-" else 1
            h, mi, se = int(m.group(2)), int(m.group(3)), int(m.group(4) or 0)
            if mi >= 60 or se >= 60:
                return None, False, f"invalid_time_str:{cleaned}"
            mins = sign * int(round(h * 60 + mi + se / 60.0))
            return mins, False, "negative_str_hm" if sign < 0 else "str_hm"
        return None, False, f"invalid_str:{cleaned}"
    return None, False, f"unsupported_type:{type(value).__name__}"


def map_semantic_headers(header_pairs: list[tuple[int, str, str]]) -> dict[str, int]:
    """Map column indices by semantic meaning across single-row or two-row Persian headers.

    Each item is (col_index_1based, main_header_text, sub_header_text).
    Supports both 13-column Aban sheets and older 11-column sheets (including typos like 'ساعتات کار').
    """
    col_map: dict[str, int] = {}
    for col_idx, raw_main, raw_sub in header_pairs:
        main = normalize_persian(raw_main)
        sub = normalize_persian(raw_sub)
        combined = f"{main} {sub}".strip()
        k_main = normalize_key(main)
        k_sub = normalize_key(sub)
        k_comb = normalize_key(combined)
        if not k_comb:
            continue

        if k_main == normalize_key("ردیف") or k_sub == normalize_key("ردیف"):
            col_map.setdefault("row_num", col_idx)
        elif "کد" in k_comb and "دستگاه" in k_comb:
            col_map["device_code"] = col_idx
        elif "جمع" in k_comb and "توقف" in k_comb:
            col_map["total_downtime"] = col_idx
        elif "جمع" in k_comb and "کار" in k_comb:
            col_map["net_work"] = col_idx
        elif k_main in {normalize_key("ساعات کار"), normalize_key("ساعتات کار"), normalize_key("ساعت کار")} and "جمع" not in k_comb:
            col_map["base_hours"] = col_idx
        elif "تعمیرات" in k_comb:
            col_map["repair"] = col_idx
        elif "بیباری" in k_comb:
            col_map["no_load"] = col_idx
        elif "انفجار" in k_comb:
            col_map["blast_delay"] = col_idx
        elif "بارگیر" in k_comb:
            col_map["no_loader"] = col_idx
        elif "تاخیرراننده" in k_comb or ("راننده" in k_comb and "تاخیر" in k_comb):
            col_map["driver_delay"] = col_idx
        elif "تاریخ" in k_comb:
            col_map["date"] = col_idx
        elif "راننده" in k_comb or "نامونامخانوادگی" in k_comb:
            col_map["drivers"] = col_idx
        elif "ملاحظات" in k_comb or "توضیحات" in k_comb:
            col_map["notes"] = col_idx
    return col_map


def extract_note_topics(note_raw: str | None) -> list[dict[str, str]]:
    """Extract qualitative themes from Persian notes text without modifying raw text."""
    text = normalize_persian(note_raw)
    if not text or text in {"-", "_", "—"}:
        return []
    # Skip pure "no downtime / no fault" night-shift confirmation notes from fault topics
    plain = text.replace("آ", "ا")
    if re.fullmatch(r"شیفت\s*شب\s*بدون(?:ه)?\s*توقف\s*و\s*خرابی", plain):
        return []
    matched: list[dict[str, str]] = []
    for key, label, patterns in _COMPILED_TOPIC_RULES:
        if any(pat.search(plain) for pat in patterns):
            matched.append({"topic_key": key, "topic_label": label})
    return matched
