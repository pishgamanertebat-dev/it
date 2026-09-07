import argparse
from collections import defaultdict
from pathlib import Path

from openpyxl import load_workbook


# ============================================================
# AIR FILTER V1 RULES
# ============================================================

OPERATIONAL_YEAR = 1405

SOURCE_SHEET = "Sheet1 (2)"

# Dump trucks
DUMP_OUTER_INTERVAL = 20
DUMP_OUTER_ALERT_AT = 17

DUMP_INNER_INTERVAL = 100
DUMP_INNER_ALERT_AT = 90

# Excavator 231
EXCAVATOR_CODE = "231"
EXCAVATOR_INTERVAL = 10
EXCAVATOR_ALERT_AT = 7

# Generator
GENERATOR_CODE = "DG1"
GENERATOR_INTERVAL = 20
GENERATOR_ALERT_AT = 17

# All remaining vehicles
CALENDAR_INTERVAL_DAYS = 2

# Recognized service marks
VALID_SERVICE_MARKS = {
    "صبح",
    "ظهر",
    "عصر",
    "*",
}

NO_WORK_MARKS = {
    "",
    "-",
}


# ============================================================
# HELPERS
# ============================================================

def clean(value):
    if value is None:
        return ""

    return " ".join(
        str(value)
        .replace("\u200c", " ")
        .replace("ي", "ی")
        .replace("ك", "ک")
        .split()
    )


def clean_code(value):
    if value is None:
        return ""

    if isinstance(value, float) and value.is_integer():
        value = int(value)

    return clean(value).upper()


def parse_header_date(value):
    """
    Excel contains a mixture such as:
      '05.01'
      5.13
      5.2   displayed as 5.20
      5.3   displayed as 5.30

    Numeric headers are therefore always formatted
    to two decimal places before parsing.
    """

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        text = f"{float(value):.2f}"
    else:
        text = clean(value).replace("/", ".")

    parts = text.split(".")

    if len(parts) != 2:
        return None

    try:
        month = int(parts[0])
        day = int(parts[1])
    except ValueError:
        return None

    if not (1 <= month <= 12):
        return None

    max_day = 31 if month <= 6 else 30

    if month == 12:
        max_day = 29

    if not (1 <= day <= max_day):
        return None

    return month, day


def jalali_ordinal(month, day):
    month_lengths = [
        31, 31, 31, 31, 31, 31,
        30, 30, 30, 30, 30, 29
    ]

    return sum(
        month_lengths[:month - 1]
    ) + day


def next_jalali_day(month, day):
    month_lengths = [
        31, 31, 31, 31, 31, 31,
        30, 30, 30, 30, 30, 29
    ]

    day += 1

    if day > month_lengths[month - 1]:
        month += 1
        day = 1

    return month, day


def fmt_date(md):
    if md is None:
        return "-"

    month, day = md

    return (
        f"{OPERATIONAL_YEAR}/"
        f"{month:02d}/"
        f"{day:02d}"
    )


def numeric_work_hours(value):
    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        value = float(value)

        if value >= 0:
            return value

    return None


def is_valid_service_mark(value):
    return clean(value) in VALID_SERVICE_MARKS


def is_blank(value):
    return clean(value) == ""


# ============================================================
# MAIN
# ============================================================

def main():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.fleet.air_filter.proposal_cli import run
    run(backtest=False)


if __name__ == '__main__':
    main()
