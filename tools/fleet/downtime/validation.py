"""Validation and data-quality auditing for Fleet Downtime workbooks.

Preserves original reported values alongside computed values — never silently alters source numbers.
Produces structured discrepancy items and complete validation evidence JSON.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .normalization import CAUSE_FIELDS, format_minutes_hhmm, jalali_month_days, period_label_fa


@dataclass
class RowValidationResult:
    validation_status: str  # VALID | DISCREPANCY | INVALID
    validation_flags: list[str] = field(default_factory=list)
    computed_causes_minutes: int | None = None
    computed_net_work_minutes: int | None = None
    discrepancies: list[dict[str, Any]] = field(default_factory=list)


def validate_record_row(
    *,
    period: str,
    sheet_name: str,
    excel_row_num: int,
    raw_device_code: str,
    canonical_device_key: str,
    expected_jalali_date: str,
    parsed_jalali_date: str | None,
    raw_date_value: Any,
    base_minutes: int | None,
    reported_net_work_minutes: int | None,
    reported_total_downtime_minutes: int | None,
    cause_minutes: dict[str, int | None],
    empty_fields: list[str],
    formula_metadata: dict[str, Any],
    parse_errors: list[str],
) -> RowValidationResult:
    """Validate arithmetic consistency, duration bounds, formulas, and date alignment for one row."""
    flags: list[str] = []
    discrepancies: list[dict[str, Any]] = []
    is_invalid = False
    is_discrepancy = False

    def add_issue(
        issue_type: str,
        severity: str,
        message: str,
        *,
        reported_value: Any = None,
        computed_value: Any = None,
        delta_minutes: int | None = None,
    ) -> None:
        nonlocal is_invalid, is_discrepancy
        flags.append(issue_type)
        if severity == "ERROR":
            is_invalid = True
        elif severity == "DISCREPANCY":
            is_discrepancy = True
        discrepancies.append(
            {
                "sheet_name": sheet_name,
                "excel_row_num": excel_row_num,
                "jalali_date": parsed_jalali_date or expected_jalali_date,
                "raw_device_code": raw_device_code,
                "canonical_device_key": canonical_device_key,
                "issue_type": issue_type,
                "severity": severity,
                "message": message,
                "reported_value": reported_value,
                "computed_value": computed_value,
                "delta_minutes": delta_minutes,
            }
        )

    # 1. Parse errors in any time column
    for err in parse_errors:
        add_issue("INVALID_TIME_VALUE", "ERROR", f"مقدار زمانی نامعتبر در ردیف: {err}")

    # 2. Date alignment with sheet and target period
    if parsed_jalali_date is None:
        add_issue(
            "DATE_MISSING_OR_INVALID",
            "DISCREPANCY",
            f"تاریخ ردیف نامعتبر یا خالی است (مقدار خام: {raw_date_value!r}؛ تاریخ مورد انتظار شیت: {expected_jalali_date}).",
            reported_value=str(raw_date_value),
            computed_value=expected_jalali_date,
        )
    else:
        if parsed_jalali_date != expected_jalali_date:
            add_issue(
                "DATE_SHEET_MISMATCH",
                "DISCREPANCY",
                f"تاریخ ردیف ({parsed_jalali_date}) با تاریخ شیت ({expected_jalali_date}) همخوان نیست.",
                reported_value=parsed_jalali_date,
                computed_value=expected_jalali_date,
            )
        row_period = f"{parsed_jalali_date[:4]}-{parsed_jalali_date[5:7]}"
        if row_period != period:
            add_issue(
                "DATE_PERIOD_MISMATCH",
                "ERROR",
                f"دوره تاریخ ردیف ({row_period}) با دوره انتخابی ({period}) همخوان نیست.",
                reported_value=row_period,
                computed_value=period,
            )

    # 3. Numeric duration bounds & negativity checks
    numeric_fields: list[tuple[str, int | None]] = [
        ("base_minutes", base_minutes),
        ("reported_net_work_minutes", reported_net_work_minutes),
        ("reported_total_downtime_minutes", reported_total_downtime_minutes),
        *[(k, cause_minutes.get(k)) for k, _ in CAUSE_FIELDS],
    ]
    for fname, val in numeric_fields:
        if val is None:
            continue
        if val < 0:
            add_issue(
                "NEGATIVE_DURATION",
                "ERROR",
                f"مقدار منفی ({val} دقیقه) در فیلد {fname}.",
                reported_value=val,
                computed_value=0,
            )
        elif val > 24 * 60:
            add_issue(
                "SINGLE_ROW_EXCEEDS_24H",
                "DISCREPANCY",
                f"مقدار یک ردیف روزانه در {fname} بیش از ۲۴ ساعت است ({format_minutes_hhmm(val)}).",
                reported_value=val,
            )

    if base_minutes is None or base_minutes <= 0:
        add_issue(
            "BASE_HOURS_MISSING_OR_ZERO",
            "ERROR",
            f"ساعات مبنای ردیف خالی یا صفر است ({base_minutes}).",
            reported_value=base_minutes,
        )

    # 4. Arithmetic checks (treat blank cause cells as 0 ONLY for sum comparison, while preserving empty_fields!)
    effective_causes = {k: (0 if cause_minutes.get(k) is None else cause_minutes[k]) for k, _ in CAUSE_FIELDS}
    computed_causes = sum(effective_causes.values()) if all(v is not None for v in effective_causes.values()) else None
    computed_net = (base_minutes - computed_causes) if (base_minutes is not None and computed_causes is not None) else None

    if reported_total_downtime_minutes is not None and computed_causes is not None:
        if computed_causes != reported_total_downtime_minutes:
            delta = computed_causes - reported_total_downtime_minutes
            add_issue(
                "CAUSES_SUM_MISMATCH",
                "DISCREPANCY",
                (
                    f"جمع اجزای توقف ({format_minutes_hhmm(computed_causes)} = {computed_causes} دقیقه) "
                    f"با جمع ساعت توقف ثبت‌شده ({format_minutes_hhmm(reported_total_downtime_minutes)} = "
                    f"{reported_total_downtime_minutes} دقیقه) برابر نیست (اختلاف: {format_minutes_hhmm(delta)})."
                ),
                reported_value=reported_total_downtime_minutes,
                computed_value=computed_causes,
                delta_minutes=delta,
            )

    if base_minutes is not None and reported_net_work_minutes is not None and reported_total_downtime_minutes is not None:
        if reported_net_work_minutes + reported_total_downtime_minutes != base_minutes:
            delta = (reported_net_work_minutes + reported_total_downtime_minutes) - base_minutes
            add_issue(
                "NET_PLUS_REPORTED_DOWNTIME_MISMATCH",
                "DISCREPANCY",
                (
                    f"کارکرد خالص ثبت‌شده ({format_minutes_hhmm(reported_net_work_minutes)}) + "
                    f"توقف کل ثبت‌شده ({format_minutes_hhmm(reported_total_downtime_minutes)}) "
                    f"با ساعات مبنا ({format_minutes_hhmm(base_minutes)}) برابر نیست."
                ),
                reported_value=reported_net_work_minutes + reported_total_downtime_minutes,
                computed_value=base_minutes,
                delta_minutes=delta,
            )

    if base_minutes is not None and reported_net_work_minutes is not None and computed_causes is not None:
        if reported_net_work_minutes + computed_causes != base_minutes:
            delta = (reported_net_work_minutes + computed_causes) - base_minutes
            add_issue(
                "NET_PLUS_CAUSES_MISMATCH",
                "DISCREPANCY",
                (
                    f"کارکرد خالص ثبت‌شده ({format_minutes_hhmm(reported_net_work_minutes)}) + "
                    f"مجموع علل توقف ({format_minutes_hhmm(computed_causes)}) "
                    f"با ساعات مبنا ({format_minutes_hhmm(base_minutes)}) برابر نیست (اختلاف: {format_minutes_hhmm(delta)})."
                ),
                reported_value=reported_net_work_minutes,
                computed_value=computed_net,
                delta_minutes=delta,
            )

    # 5. Formula audit on total_downtime / net_work (detect literal overwrite of sheet formulas)
    td_meta = formula_metadata.get("total_downtime") or {}
    if td_meta.get("is_formula") is False and td_meta.get("sheet_mostly_formula"):
        if is_discrepancy:
            flags.append("FORMULA_OVERWRITTEN_WITH_LITERAL")
        else:
            # Numerically consistent, but still note in audit table as WARNING (not discrepancy)
            flags.append("FORMULA_MISSING_NUMERICALLY_OK")
            discrepancies.append(
                {
                    "sheet_name": sheet_name,
                    "excel_row_num": excel_row_num,
                    "jalali_date": parsed_jalali_date or expected_jalali_date,
                    "raw_device_code": raw_device_code,
                    "canonical_device_key": canonical_device_key,
                    "issue_type": "FORMULA_MISSING_NUMERICALLY_OK",
                    "severity": "WARNING",
                    "message": (
                        "فرمول جمع ساعت توقف در این ردیف با مقدار ثابت جایگزین شده است، اما با مجموع علل "
                        "توقف (۰:۰۰) همخوانی عددی دارد."
                    ),
                    "reported_value": reported_total_downtime_minutes,
                    "computed_value": computed_causes,
                    "delta_minutes": 0,
                }
            )

    status = "INVALID" if is_invalid else ("DISCREPANCY" if is_discrepancy else "VALID")
    return RowValidationResult(
        validation_status=status,
        validation_flags=flags,
        computed_causes_minutes=computed_causes,
        computed_net_work_minutes=computed_net,
        discrepancies=discrepancies,
    )


def compute_month_day_coverage(period: str, present_days: set[int]) -> dict[str, Any]:
    """Compute exact present and missing calendar days for a Jalali month."""
    year_s, month_s = period.split("-")
    year, month = int(year_s), int(month_s)
    total_days = jalali_month_days(year, month)
    present_sorted = sorted(d for d in present_days if 1 <= d <= total_days)
    missing_sorted = [d for d in range(1, total_days + 1) if d not in present_days]
    return {
        "period": period,
        "period_label_fa": period_label_fa(period),
        "calendar_days_in_month": total_days,
        "present_days": present_sorted,
        "present_days_count": len(present_sorted),
        "present_dates": [f"{year:04d}/{month:02d}/{d:02d}" for d in present_sorted],
        "missing_days": missing_sorted,
        "missing_days_count": len(missing_sorted),
        "missing_dates": [f"{year:04d}/{month:02d}/{d:02d}" for d in missing_sorted],
        "missing_days_policy": "MISSING_SHEET_UNKNOWN (عدم وجود شیت به معنی صفر بودن کارکرد یا توقف نیست)",
    }
