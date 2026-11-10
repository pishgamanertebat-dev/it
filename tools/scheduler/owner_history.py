"""Owner decision of 2026-11-10: data 15/16 Aban is outside operational follow-up.

This presentation/alert policy never edits runs, holds, receipts or source files.
It cannot authorize a replay, correction or backfill.
"""
from datetime import datetime

OWNER_CLOSED_DATES = frozenset({'1405/08/15', '1405/08/16'})


def closed_by_owner(due):
    from .tasks import overflow_report_date
    return overflow_report_date(datetime.fromisoformat(due)) in OWNER_CLOSED_DATES
