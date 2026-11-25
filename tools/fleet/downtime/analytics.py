"""Deterministic Fleet Downtime analytics engine backed purely by SQLite (stdlib only).

All numeric metrics, weighted percentages, rankings, Pareto curves, maintenance vs operational
splits, and evidence-backed Persian management commentary are computed here deterministically.
"""
from __future__ import annotations

from contextlib import closing
import json
from typing import Any

from .normalization import (
    CAUSE_FIELDS,
    CAUSE_LABEL_BY_KEY,
    format_minutes_hhmm,
    format_minutes_hours_decimal,
    parse_jalali_date,
    parse_period,
    period_label_fa,
)
from .repository import DowntimeRepository, safe_pct

OPERATIONAL_CAUSE_KEYS = (
    "no_load_minutes",
    "blast_delay_minutes",
    "no_loader_minutes",
    "driver_delay_minutes",
)


def _decorate_minutes(d: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    out = dict(d)
    for k in keys:
        if k in out and out[k] is not None:
            base_name = k[:-8] if k.endswith("_minutes") else k
            out[f"{base_name}_hhmm"] = format_minutes_hhmm(out[k])
            out[f"{base_name}_hours"] = format_minutes_hours_decimal(out[k], 2)
    return out


class DowntimeAnalytics:
    """Read-only analytical query layer over DowntimeRepository."""

    def __init__(self, repo: DowntimeRepository | None = None) -> None:
        self.repo = repo or DowntimeRepository()

    def _active_version_or_missing(self, period: str) -> tuple[str, dict[str, Any] | None]:
        norm_p = parse_period(period)
        ver = self.repo.get_active_version(norm_p)
        return norm_p, ver

    def list_periods(self) -> list[dict[str, Any]]:
        periods = self.repo.list_periods()
        result = []
        for p in periods:
            item = dict(p)
            item["period_label_fa"] = period_label_fa(p["period"])
            item["base_hhmm"] = format_minutes_hhmm(p.get("base_minutes"))
            item["net_work_hhmm"] = format_minutes_hhmm(p.get("reported_net_work_minutes"))
            item["downtime_hhmm"] = format_minutes_hhmm(p.get("reported_total_downtime_minutes"))
            result.append(item)
        return result

    def fleet_monthly_summary(self, period: str) -> dict[str, Any]:
        norm_p, ver = self._active_version_or_missing(period)
        if not ver:
            return {
                "found": False,
                "period": norm_p,
                "period_label_fa": period_label_fa(norm_p),
                "message": f"اطلاعات دوره {period_label_fa(norm_p)} ({norm_p}) هنوز در سامانه وارد نشده است.",
            }
        vid = int(ver["version_id"])
        with closing(self.repo._connect(write=False)) as conn:
            fleet = dict(
                conn.execute("SELECT * FROM summary_fleet_monthly WHERE version_id = ?", (vid,)).fetchone()
            )
            causes = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM summary_causes_monthly WHERE version_id = ? ORDER BY total_minutes DESC, cause_key",
                    (vid,),
                ).fetchall()
            ]
            groups = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM summary_group_monthly WHERE version_id = ? "
                    "ORDER BY is_confirmed_group DESC, base_minutes DESC",
                    (vid,),
                ).fetchall()
            ]
            discrepancies = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM discrepancies WHERE version_id = ? AND severity IN ('ERROR', 'DISCREPANCY') "
                    "ORDER BY jalali_date, excel_row_num",
                    (vid,),
                ).fetchall()
            ]
            warnings = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM discrepancies WHERE version_id = ? AND severity = 'WARNING' "
                    "ORDER BY jalali_date, excel_row_num",
                    (vid,),
                ).fetchall()
            ]
            top_down = [
                dict(r)
                for r in conn.execute(
                    "SELECT canonical_device_key, raw_device_code, device_name, device_group, identity_status, "
                    "base_minutes, reported_net_work_minutes, reported_total_downtime_minutes, "
                    "computed_causes_minutes, repair_minutes, work_pct, downtime_pct, top_cause_label "
                    "FROM summary_device_monthly WHERE version_id = ? "
                    "ORDER BY reported_total_downtime_minutes DESC, downtime_pct DESC LIMIT 10",
                    (vid,),
                ).fetchall()
            ]
            top_repair = [
                dict(r)
                for r in conn.execute(
                    "SELECT canonical_device_key, raw_device_code, device_name, device_group, identity_status, "
                    "base_minutes, repair_minutes, repair_pct_of_base, repair_pct_of_downtime, "
                    "reported_total_downtime_minutes, work_pct "
                    "FROM summary_device_monthly WHERE version_id = ? "
                    "ORDER BY repair_minutes DESC, repair_pct_of_base DESC LIMIT 10",
                    (vid,),
                ).fetchall()
            ]
            topics = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM summary_note_topics_monthly WHERE version_id = ? "
                    "ORDER BY mention_rows DESC, affected_devices DESC LIMIT 15",
                    (vid,),
                ).fetchall()
            ]

        op_minutes = sum(fleet[k] for k in OPERATIONAL_CAUSE_KEYS)
        maint_minutes = fleet["repair_minutes"]
        total_causes = fleet["computed_causes_minutes"]

        # Add Pareto cumulative shares to causes
        cum = 0
        causes_out = []
        for c in causes:
            cum += c["total_minutes"]
            item = _decorate_minutes(c, ("total_minutes",))
            item["cumulative_minutes"] = cum
            item["cumulative_pct"] = safe_pct(cum, total_causes)
            item["category"] = "تعمیراتی" if c["cause_key"] == "repair_minutes" else "عملیاتی"
            causes_out.append(item)

        fleet_dec = _decorate_minutes(
            fleet,
            (
                "base_minutes",
                "reported_net_work_minutes",
                "reported_total_downtime_minutes",
                "computed_causes_minutes",
                "causes_vs_reported_delta_minutes",
                "repair_minutes",
                "no_load_minutes",
                "blast_delay_minutes",
                "no_loader_minutes",
                "driver_delay_minutes",
            ),
        )
        fleet_dec["operational_downtime_minutes"] = op_minutes
        fleet_dec["operational_downtime_hhmm"] = format_minutes_hhmm(op_minutes)
        fleet_dec["operational_pct_of_base"] = safe_pct(op_minutes, fleet["base_minutes"])
        fleet_dec["operational_pct_of_causes"] = safe_pct(op_minutes, total_causes)
        fleet_dec["maintenance_downtime_minutes"] = maint_minutes
        fleet_dec["maintenance_downtime_hhmm"] = format_minutes_hhmm(maint_minutes)
        fleet_dec["maintenance_pct_of_base"] = safe_pct(maint_minutes, fleet["base_minutes"])
        fleet_dec["maintenance_pct_of_causes"] = safe_pct(maint_minutes, total_causes)

        return {
            "found": True,
            "period": norm_p,
            "period_label_fa": period_label_fa(norm_p),
            "version": {
                "version_id": vid,
                "version_number": ver["version_number"],
                "source_filename": ver["source_filename"],
                "source_sha256": ver["source_sha256"],
                "data_quality_status": ver["data_quality_status"],
                "is_provisional": ver["discrepancy_row_count"] > 0 or ver["missing_days_count"] > 0,
                "imported_at": ver["imported_at"],
                "total_sheets": ver["total_sheets"],
                "period_sheets_count": ver["period_sheets_count"],
                "excluded_sheets_count": ver["excluded_sheets_count"],
            },
            "coverage": {
                "calendar_days_in_month": ver["calendar_days_in_month"],
                "present_days_count": ver["present_days_count"],
                "missing_days_count": ver["missing_days_count"],
                "present_days": json.loads(ver["present_days_json"]),
                "missing_days": json.loads(ver["missing_days_json"]),
                "missing_days_note": "عدم وجود شیت روزانه به معنی صفر بودن کارکرد یا توقف نیست؛ وضعیت روزهای غایب نامشخص است.",
            },
            "identity_overview": {
                "distinct_raw_codes": ver["distinct_raw_codes"],
                "distinct_device_tracks": ver["distinct_device_tracks"],
                "resolved_devices_count": ver["resolved_devices_count"],
                "ambiguous_devices_count": ver["ambiguous_devices_count"],
                "unregistered_devices_count": ver["unregistered_devices_count"],
            },
            "fleet": fleet_dec,
            "causes": causes_out,
            "groups": [
                _decorate_minutes(
                    g,
                    (
                        "base_minutes",
                        "reported_net_work_minutes",
                        "reported_total_downtime_minutes",
                        "computed_causes_minutes",
                        "repair_minutes",
                        "no_load_minutes",
                        "blast_delay_minutes",
                        "no_loader_minutes",
                        "driver_delay_minutes",
                    ),
                )
                for g in groups
            ],
            "top_downtime_devices": [
                _decorate_minutes(
                    d,
                    (
                        "base_minutes",
                        "reported_net_work_minutes",
                        "reported_total_downtime_minutes",
                        "computed_causes_minutes",
                        "repair_minutes",
                    ),
                )
                for d in top_down
            ],
            "top_repair_devices": [
                _decorate_minutes(
                    d, ("base_minutes", "repair_minutes", "reported_total_downtime_minutes")
                )
                for d in top_repair
            ],
            "top_note_topics": topics,
            "discrepancies": discrepancies,
            "warnings": warnings,
        }

    def fleet_daily_trend(self, period: str) -> dict[str, Any]:
        norm_p, ver = self._active_version_or_missing(period)
        if not ver:
            return {
                "found": False,
                "period": norm_p,
                "period_label_fa": period_label_fa(norm_p),
                "message": f"اطلاعات دوره {period_label_fa(norm_p)} ({norm_p}) در سامانه موجود نیست.",
            }
        vid = int(ver["version_id"])
        with closing(self.repo._connect(write=False)) as conn:
            cal_days = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM period_days WHERE version_id = ? ORDER BY day_of_month", (vid,)
                ).fetchall()
            ]
            daily_map = {
                r["jalali_date"]: dict(r)
                for r in conn.execute(
                    "SELECT * FROM summary_fleet_daily WHERE version_id = ? ORDER BY iso_date", (vid,)
                ).fetchall()
            }
        series = []
        for cd in cal_days:
            jdate = cd["jalali_date"]
            if cd["coverage_status"] == "PRESENT" and jdate in daily_map:
                d = _decorate_minutes(
                    daily_map[jdate],
                    (
                        "base_minutes",
                        "reported_net_work_minutes",
                        "reported_total_downtime_minutes",
                        "computed_causes_minutes",
                        "repair_minutes",
                        "no_load_minutes",
                        "blast_delay_minutes",
                        "no_loader_minutes",
                        "driver_delay_minutes",
                    ),
                )
                d["day_of_month"] = cd["day_of_month"]
                d["coverage_status"] = "PRESENT"
                series.append(d)
            else:
                series.append(
                    {
                        "day_of_month": cd["day_of_month"],
                        "jalali_date": jdate,
                        "iso_date": cd["iso_date"],
                        "coverage_status": "MISSING_SHEET_UNKNOWN",
                        "sheet_name": None,
                        "row_count": 0,
                    }
                )
        return {
            "found": True,
            "period": norm_p,
            "period_label_fa": period_label_fa(norm_p),
            "version_number": ver["version_number"],
            "days": series,
        }

    def daily_detail(self, jalali_date_input: str, *, default_year: int | None = None) -> dict[str, Any]:
        jdate, iso = parse_jalali_date(jalali_date_input, default_year=default_year)
        period = f"{jdate[:4]}-{jdate[5:7]}"
        ver = self.repo.get_active_version(period)
        if not ver:
            return {
                "found": False,
                "status": "PERIOD_NOT_IMPORTED",
                "period": period,
                "jalali_date": jdate,
                "message": f"اطلاعات دوره {period_label_fa(period)} ({period}) هنوز در سامانه وارد نشده است.",
            }
        vid = int(ver["version_id"])
        day_num = int(jdate.split("/")[2])
        with closing(self.repo._connect(write=False)) as conn:
            day_cov = conn.execute(
                "SELECT * FROM period_days WHERE version_id = ? AND day_of_month = ?", (vid, day_num)
            ).fetchone()
            if not day_cov or day_cov["coverage_status"] != "PRESENT":
                return {
                    "found": False,
                    "status": "MISSING_SHEET_UNKNOWN",
                    "period": period,
                    "period_label_fa": period_label_fa(period),
                    "jalali_date": jdate,
                    "source_filename": ver["source_filename"],
                    "message": (
                        f"داده روز {jdate} در فایل منبع ({ver['source_filename']}) موجود نیست "
                        f"(شیت این روز در فایل وجود ندارد؛ عدم وجود شیت به معنی صفر بودن کارکرد یا توقف نیست)."
                    ),
                }
            summary = dict(
                conn.execute(
                    "SELECT * FROM summary_fleet_daily WHERE version_id = ? AND jalali_date = ?",
                    (vid, jdate),
                ).fetchone()
            )
            records = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT canonical_device_key, raw_device_code, device_name, device_group, identity_status,
                           sheet_name, excel_row_num, shift,
                           base_minutes, reported_net_work_minutes, reported_total_downtime_minutes,
                           computed_causes_minutes, repair_minutes, no_load_minutes,
                           blast_delay_minutes, no_loader_minutes, driver_delay_minutes,
                           driver_raw, notes_raw, validation_status
                    FROM downtime_records
                    WHERE version_id = ? AND jalali_date = ?
                    ORDER BY reported_total_downtime_minutes DESC, excel_row_num ASC
                    """,
                    (vid, jdate),
                ).fetchall()
            ]
        summary_dec = _decorate_minutes(
            summary,
            (
                "base_minutes",
                "reported_net_work_minutes",
                "reported_total_downtime_minutes",
                "computed_causes_minutes",
                "repair_minutes",
                "no_load_minutes",
                "blast_delay_minutes",
                "no_loader_minutes",
                "driver_delay_minutes",
            ),
        )
        causes = [
            {
                "cause_key": ck,
                "cause_label": cl,
                "minutes": summary[ck],
                "hhmm": format_minutes_hhmm(summary[ck]),
                "share_of_causes_pct": safe_pct(summary[ck], summary["computed_causes_minutes"]),
            }
            for ck, cl in CAUSE_FIELDS
        ]
        causes.sort(key=lambda x: -x["minutes"])
        decorated_rows = [
            _decorate_minutes(
                r,
                (
                    "base_minutes",
                    "reported_net_work_minutes",
                    "reported_total_downtime_minutes",
                    "computed_causes_minutes",
                    "repair_minutes",
                    "no_load_minutes",
                    "blast_delay_minutes",
                    "no_loader_minutes",
                    "driver_delay_minutes",
                ),
            )
            for r in records
        ]
        return {
            "found": True,
            "status": "PRESENT",
            "period": period,
            "period_label_fa": period_label_fa(period),
            "jalali_date": jdate,
            "iso_date": iso,
            "source_filename": ver["source_filename"],
            "version_number": ver["version_number"],
            "summary": summary_dec,
            "causes": causes,
            "records": decorated_rows,
        }

    def all_devices_monthly(self, period: str, *, include_daily: bool = False) -> dict[str, Any]:
        norm_p, ver = self._active_version_or_missing(period)
        if not ver:
            return {
                "found": False,
                "period": norm_p,
                "message": f"اطلاعات دوره {period_label_fa(norm_p)} ({norm_p}) موجود نیست.",
            }
        vid = int(ver["version_id"])
        with closing(self.repo._connect(write=False)) as conn:
            devices = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT * FROM summary_device_monthly
                    WHERE version_id = ?
                    ORDER BY reported_total_downtime_minutes DESC, downtime_pct DESC, canonical_device_key ASC
                    """,
                    (vid,),
                ).fetchall()
            ]
            daily_by_dev: dict[str, list[dict[str, Any]]] = {}
            top_days_by_dev: dict[str, list[dict[str, Any]]] = {}
            discrep_by_dev: dict[str, list[dict[str, Any]]] = {}
            if include_daily:
                for r in conn.execute(
                    "SELECT * FROM summary_device_daily WHERE version_id = ? ORDER BY iso_date ASC", (vid,)
                ).fetchall():
                    d = dict(r)
                    daily_by_dev.setdefault(d["canonical_device_key"], []).append(d)
                for r in conn.execute(
                    """
                    SELECT canonical_device_key, jalali_date, shift, reported_total_downtime_minutes,
                           computed_causes_minutes, repair_minutes, no_load_minutes,
                           blast_delay_minutes, no_loader_minutes, driver_delay_minutes,
                           notes_raw, validation_status
                    FROM downtime_records
                    WHERE version_id = ? AND (reported_total_downtime_minutes > 0 OR computed_causes_minutes > 0)
                    ORDER BY MAX(reported_total_downtime_minutes, computed_causes_minutes) DESC, jalali_date ASC
                    """,
                    (vid,),
                ).fetchall():
                    d = dict(r)
                    lst = top_days_by_dev.setdefault(d["canonical_device_key"], [])
                    if len(lst) < 5:
                        lst.append(d)
                for r in conn.execute(
                    "SELECT * FROM discrepancies WHERE version_id = ? AND severity IN ('ERROR', 'DISCREPANCY')",
                    (vid,),
                ).fetchall():
                    d = dict(r)
                    discrep_by_dev.setdefault(d["canonical_device_key"], []).append(d)

        out_devices = []
        # Compute ranks by total downtime hours and by downtime percentage
        by_hours_order = {
            d["canonical_device_key"]: idx + 1
            for idx, d in enumerate(
                sorted(
                    devices,
                    key=lambda x: (-x["reported_total_downtime_minutes"], -x["downtime_pct"], x["canonical_device_key"]),
                )
            )
        }
        by_pct_order = {
            d["canonical_device_key"]: idx + 1
            for idx, d in enumerate(
                sorted(
                    devices,
                    key=lambda x: (-x["downtime_pct"], -x["reported_total_downtime_minutes"], x["canonical_device_key"]),
                )
            )
        }
        by_repair_order = {
            d["canonical_device_key"]: idx + 1
            for idx, d in enumerate(
                sorted(
                    devices,
                    key=lambda x: (-x["repair_minutes"], -x["repair_pct_of_base"], x["canonical_device_key"]),
                )
            )
        }

        for d in devices:
            item = _decorate_minutes(
                d,
                (
                    "base_minutes",
                    "reported_net_work_minutes",
                    "reported_total_downtime_minutes",
                    "computed_causes_minutes",
                    "repair_minutes",
                    "no_load_minutes",
                    "blast_delay_minutes",
                    "no_loader_minutes",
                    "driver_delay_minutes",
                    "max_daily_downtime_minutes",
                ),
            )
            item["ambiguous_candidates"] = json.loads(d["ambiguous_candidates_json"])
            item["top_notes"] = json.loads(d["top_notes_json"])
            item["top_topics"] = json.loads(d["top_topics_json"])
            item["rank_by_downtime_hours"] = by_hours_order[d["canonical_device_key"]]
            item["rank_by_downtime_pct"] = by_pct_order[d["canonical_device_key"]]
            item["rank_by_repair_hours"] = by_repair_order[d["canonical_device_key"]]
            # Cause shares for this device
            denom = d["computed_causes_minutes"]
            item["cause_breakdown"] = [
                {
                    "cause_key": ck,
                    "cause_label": cl,
                    "minutes": d[ck],
                    "hhmm": format_minutes_hhmm(d[ck]),
                    "share_pct": safe_pct(d[ck], denom),
                }
                for ck, cl in CAUSE_FIELDS
            ]
            if include_daily:
                item["daily_series"] = daily_by_dev.get(d["canonical_device_key"], [])
                item["major_downtime_days"] = top_days_by_dev.get(d["canonical_device_key"], [])
                item["discrepancies"] = discrep_by_dev.get(d["canonical_device_key"], [])
            item["narrative"] = build_device_narrative(item)
            out_devices.append(item)

        return {
            "found": True,
            "period": norm_p,
            "period_label_fa": period_label_fa(norm_p),
            "version_number": ver["version_number"],
            "source_filename": ver["source_filename"],
            "devices": out_devices,
        }

    def device_detail(self, period: str, canonical_device_key: str) -> dict[str, Any]:
        """Fast single-device detail lookup directly by indexed canonical_device_key."""
        norm_p, ver = self._active_version_or_missing(period)
        if not ver:
            return {
                "found": False,
                "period": norm_p,
                "message": f"اطلاعات دوره {period_label_fa(norm_p)} ({norm_p}) موجود نیست.",
            }
        vid = int(ver["version_id"])
        with closing(self.repo._connect(write=False)) as conn:
            row = conn.execute(
                "SELECT * FROM summary_device_monthly WHERE version_id = ? AND canonical_device_key = ?",
                (vid, canonical_device_key),
            ).fetchone()
            if not row:
                return {
                    "found": False,
                    "period": norm_p,
                    "canonical_device_key": canonical_device_key,
                    "message": f"رکوردی برای دستگاه {canonical_device_key} در دوره {period_label_fa(norm_p)} یافت نشد.",
                }
            d = dict(row)
            # Compute deterministic ranks directly via lightweight SQL counts
            rank_hours = 1 + conn.execute(
                "SELECT COUNT(*) FROM summary_device_monthly WHERE version_id = ? AND ("
                "reported_total_downtime_minutes > ? OR ("
                "reported_total_downtime_minutes = ? AND (downtime_pct > ? OR "
                "(downtime_pct = ? AND canonical_device_key < ?))))",
                (vid, d["reported_total_downtime_minutes"], d["reported_total_downtime_minutes"],
                 d["downtime_pct"], d["downtime_pct"], canonical_device_key),
            ).fetchone()[0]
            rank_pct = 1 + conn.execute(
                "SELECT COUNT(*) FROM summary_device_monthly WHERE version_id = ? AND ("
                "downtime_pct > ? OR (downtime_pct = ? AND ("
                "reported_total_downtime_minutes > ? OR ("
                "reported_total_downtime_minutes = ? AND canonical_device_key < ?))))",
                (vid, d["downtime_pct"], d["downtime_pct"],
                 d["reported_total_downtime_minutes"], d["reported_total_downtime_minutes"],
                 canonical_device_key),
            ).fetchone()[0]
            rank_repair = 1 + conn.execute(
                "SELECT COUNT(*) FROM summary_device_monthly WHERE version_id = ? AND ("
                "repair_minutes > ? OR (repair_minutes = ? AND ("
                "repair_pct_of_base > ? OR (repair_pct_of_base = ? AND canonical_device_key < ?))))",
                (vid, d["repair_minutes"], d["repair_minutes"],
                 d["repair_pct_of_base"], d["repair_pct_of_base"], canonical_device_key),
            ).fetchone()[0]
            daily_series = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM summary_device_daily WHERE version_id = ? AND canonical_device_key = ? "
                    "ORDER BY iso_date ASC",
                    (vid, canonical_device_key),
                ).fetchall()
            ]
            major_days = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT canonical_device_key, jalali_date, shift, reported_total_downtime_minutes,
                           computed_causes_minutes, repair_minutes, no_load_minutes,
                           blast_delay_minutes, no_loader_minutes, driver_delay_minutes,
                           notes_raw, validation_status
                    FROM downtime_records
                    WHERE version_id = ? AND canonical_device_key = ?
                      AND (reported_total_downtime_minutes > 0 OR computed_causes_minutes > 0)
                    ORDER BY MAX(reported_total_downtime_minutes, computed_causes_minutes) DESC, jalali_date ASC
                    LIMIT 5
                    """,
                    (vid, canonical_device_key),
                ).fetchall()
            ]
            discreps = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM discrepancies WHERE version_id = ? AND canonical_device_key = ? "
                    "AND severity IN ('ERROR', 'DISCREPANCY')",
                    (vid, canonical_device_key),
                ).fetchall()
            ]

        item = _decorate_minutes(
            d,
            (
                "base_minutes",
                "reported_net_work_minutes",
                "reported_total_downtime_minutes",
                "computed_causes_minutes",
                "repair_minutes",
                "no_load_minutes",
                "blast_delay_minutes",
                "no_loader_minutes",
                "driver_delay_minutes",
                "max_daily_downtime_minutes",
            ),
        )
        item["ambiguous_candidates"] = json.loads(d["ambiguous_candidates_json"])
        item["top_notes"] = json.loads(d["top_notes_json"])
        item["top_topics"] = json.loads(d["top_topics_json"])
        item["rank_by_downtime_hours"] = rank_hours
        item["rank_by_downtime_pct"] = rank_pct
        item["rank_by_repair_hours"] = rank_repair
        denom = d["computed_causes_minutes"]
        item["cause_breakdown"] = [
            {
                "cause_key": ck,
                "cause_label": cl,
                "minutes": d[ck],
                "hhmm": format_minutes_hhmm(d[ck]),
                "share_pct": safe_pct(d[ck], denom),
            }
            for ck, cl in CAUSE_FIELDS
        ]
        item["daily_series"] = daily_series
        item["major_downtime_days"] = major_days
        item["discrepancies"] = discreps
        item["narrative"] = build_device_narrative(item)
        return {
            "found": True,
            "period": norm_p,
            "period_label_fa": period_label_fa(norm_p),
            "version_number": ver["version_number"],
            "source_filename": ver["source_filename"],
            "device": item,
        }

    def rank_devices(
        self,
        period: str,
        *,
        metric: str = "downtime_minutes",
        order: str = "desc",
        limit: int = 10,
    ) -> dict[str, Any]:
        metric_map = {
            "downtime_minutes": "reported_total_downtime_minutes",
            "downtime_hours": "reported_total_downtime_minutes",
            "downtime_pct": "downtime_pct",
            "repair_minutes": "repair_minutes",
            "repair_hours": "repair_minutes",
            "repair_pct": "repair_pct_of_base",
            "work_minutes": "reported_net_work_minutes",
            "work_hours": "reported_net_work_minutes",
            "work_pct": "work_pct",
            "days_zero_work": "days_zero_work",
            "max_consecutive_zero_work_days": "max_consecutive_zero_work_days",
        }
        col = metric_map.get(metric)
        if not col:
            raise ValueError(f"Unsupported ranking metric: {metric}")
        direction = "ASC" if str(order).lower() == "asc" else "DESC"
        norm_p, ver = self._active_version_or_missing(period)
        if not ver:
            return {
                "found": False,
                "period": norm_p,
                "message": f"اطلاعات دوره {period_label_fa(norm_p)} ({norm_p}) موجود نیست.",
            }
        vid = int(ver["version_id"])
        lim = max(1, min(int(limit), 100))
        with closing(self.repo._connect(write=False)) as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    f"""
                    SELECT canonical_device_key, raw_device_code, device_name, device_group, identity_status,
                           days_with_data, days_with_work, days_zero_work, max_consecutive_zero_work_days,
                           base_minutes, reported_net_work_minutes, reported_total_downtime_minutes,
                           computed_causes_minutes, repair_minutes, work_pct, downtime_pct,
                           repair_pct_of_base, top_cause_label, discrepancy_count
                    FROM summary_device_monthly
                    WHERE version_id = ?
                    ORDER BY {col} {direction}, reported_total_downtime_minutes DESC, canonical_device_key ASC
                    LIMIT ?
                    """,
                    (vid, lim),
                ).fetchall()
            ]
        items = []
        for idx, r in enumerate(rows, start=1):
            d = _decorate_minutes(
                r,
                (
                    "base_minutes",
                    "reported_net_work_minutes",
                    "reported_total_downtime_minutes",
                    "computed_causes_minutes",
                    "repair_minutes",
                ),
            )
            d["rank"] = idx
            items.append(d)
        return {
            "found": True,
            "period": norm_p,
            "period_label_fa": period_label_fa(norm_p),
            "version_number": ver["version_number"],
            "source_filename": ver["source_filename"],
            "metric": metric,
            "order": direction.lower(),
            "items": items,
        }

    def executive_insights(self, period: str) -> dict[str, Any]:
        """Generate deterministic, strictly evidence-based Persian executive findings & recommendations."""
        summary = self.fleet_monthly_summary(period)
        if not summary["found"]:
            return summary
        all_devs = self.all_devices_monthly(period, include_daily=False)["devices"]
        fleet = summary["fleet"]
        causes = summary["causes"]
        top_cause = causes[0] if causes else None
        second_cause = causes[1] if len(causes) > 1 else None

        # High-priority devices: high downtime % (>=35%) or high repair hours (>=50h = 3000m) or >=5 zero-work days
        attention_devs = [
            d for d in all_devs
            if d["downtime_pct"] >= 35.0 or d["repair_minutes"] >= 3000 or d["days_zero_work"] >= 5
        ]
        attention_devs.sort(key=lambda x: (-x["repair_minutes"], -x["downtime_pct"]))

        high_repair_devs = sorted(
            [d for d in all_devs if d["repair_minutes"] > 0],
            key=lambda x: (-x["repair_minutes"], -x["repair_pct_of_base"]),
        )[:8]

        operational_causes = [c for c in causes if c["category"] == "عملیاتی" and c["total_minutes"] > 0]
        top_topics = summary["top_note_topics"][:8]
        discrepancies = summary["discrepancies"]
        id_ov = summary["identity_overview"]
        cov = summary["coverage"]

        # Q1: Main cause of fleet work reduction
        q1 = (
            f"در دوره {summary['period_label_fa']} (با پوشش {cov['present_days_count']} روز دارای شیت از "
            f"{cov['calendar_days_in_month']} روز ماه)، از مجموع {fleet['base_hhmm']} ساعت مبنا، "
            f"{fleet['reported_net_work_hhmm']} ساعت کارکرد خالص ({fleet['work_pct']}٪) و "
            f"{fleet['reported_total_downtime_hhmm']} ساعت توقف ثبت‌شده ({fleet['downtime_pct']}٪) به ثبت رسیده است. "
        )
        if top_cause:
            q1 += (
                f"مهم‌ترین علت توقف کل ناوگان «{top_cause['cause_label']}» با "
                f"{top_cause['total_hhmm']} ساعت ({top_cause['share_of_causes_pct']}٪ از مجموع علل توقف و "
                f"{top_cause['share_of_base_pct']}٪ از کل ساعات مبنا) بوده است"
            )
            if second_cause and second_cause["total_minutes"] > 0:
                q1 += (
                    f" و پس از آن «{second_cause['cause_label']}» با {second_cause['total_hhmm']} ساعت "
                    f"({second_cause['share_of_causes_pct']}٪ از مجموع علل توقف) قرار دارد."
                )
            else:
                q1 += "."

        # Q2: Devices needing closer review
        top_att_names = "، ".join(
            f"{d['raw_device_code']} ({d['downtime_pct']}٪ توقف، {d['reported_total_downtime_hhmm']} ساعت)"
            for d in attention_devs[:6]
        )
        q2 = (
            f"تعداد {len(attention_devs)} دستگاه دارای درصد توقف بالا (≥۳۵٪)، توقف تعمیراتی سنگین (≥۵۰ ساعت) "
            f"یا روزهای متوالی بدون کارکرد بوده‌اند؛ شاخص‌ترین موارد شامل: {top_att_names}."
            if attention_devs
            else "هیچ دستگاهی از آستانه هشدار توقف عبور نکرده است."
        )

        # Q3: High maintenance downtime devices
        rep_names = "، ".join(
            f"{d['raw_device_code']} ({d['repair_hhmm']} ساعت / {d['repair_pct_of_base']}٪ از مبنا)"
            for d in high_repair_devs[:6]
        )
        q3 = (
            f"مجموع توقف تعمیراتی ناوگان {fleet['repair_hhmm']} ساعت ({fleet['maintenance_pct_of_base']}٪ از ساعات مبنا "
            f"و {fleet['maintenance_pct_of_causes']}٪ از مجموع علل توقف) است. بیشترین توقف تعمیراتی در دستگاه‌های "
            f"{rep_names} ثبت شده که نیازمند بررسی پرونده تعمیرات، تأمین قطعه و برنامه‌ریزی PM پیشگیرانه هستند."
        )

        # Q4: Operational downtimes
        op_parts = "، ".join(
            f"{c['cause_label']} ({c['total_hhmm']} ساعت / {c['share_of_causes_pct']}٪)"
            for c in operational_causes
        )
        q4 = (
            f"مجموع توقفات با منشأ عملیاتی (غیرتعمیراتی) {fleet['operational_downtime_hhmm']} ساعت "
            f"({fleet['operational_pct_of_causes']}٪ از مجموع علل توقف و {fleet['operational_pct_of_base']}٪ از ساعات مبنا) "
            f"را تشکیل می‌دهد که تفکیک آن عبارت است از: {op_parts}. تفکیک توقف عملیاتی از تعمیراتی برای ارزیابی "
            f"عادلانه واحد نت و عملیات ضروری است."
        )

        # Q5: Repeated themes in Persian notes
        topic_parts = "، ".join(
            f"«{t['topic_label']}» ({t['mention_rows']} ردیف در {t['affected_devices']} دستگاه)"
            for t in top_topics[:6]
        )
        q5 = (
            f"تحلیل کیفی ستون ملاحظات نشان می‌دهد پرتکرارترین موضوعات ثبت‌شده عبارت‌اند از: {topic_parts}. "
            f"لازم به ذکر است برچسب‌های استخراج‌شده از متن ملاحظات، تحلیل کیفی بوده و جایگزین طبقه‌بندی عددی پنج‌گانه توقف نیستند."
            if top_topics
            else "موضوع پرتکرار شاخصی در متن ملاحظات ثبت نشده است."
        )

        # Q6: Actionable next-month recommendations (evidence-based, non-blaming)
        recommendations = [
            (
                f"بررسی فنی و تأمین قطعه برای دستگاه‌های دارای بیشترین توقف تعمیراتی ("
                + "، ".join(d["raw_device_code"] for d in high_repair_devs[:5])
                + ") با اولویت موضوعات پرتکرار تعمیراتی مانند تعمیر موتور، جوشکاری دامپ/شاسی و نشتی روغن."
            ),
            (
                f"بازبینی هماهنگی برنامه آتش‌باری/انفجار و تخصیص بارگیر جهت کاهش سهم توقفات عملیاتی "
                f"({fleet['operational_downtime_hhmm']} ساعت؛ شامل {fleet['blast_delay_hhmm']} ساعت تأخیر انفجار)."
            ),
            (
                f"بررسی الگوی تأخیر و کمبود راننده ({fleet['driver_delay_hhmm']} ساعت) در شیفت‌های روز و شب "
                f"برای برنامه‌ریزی نیروی جایگزین در دستگاه‌های بدون راننده."
            ),
            (
                "اصلاح ساختار قالب فایل Excel ماهانه: افزودن ستون «نوع دستگاه» (به‌ویژه جهت تفکیک قطعی بیل ۶۰۱ و لودر ۶۰۱ و کد ۳۲۱)، "
                "ثبت کدهای جدید (۷۱۷ تا ۷۲۱ و گریدر) در Registry ناوگان و قفل کردن سلول‌های فرمولی جمع توقف جهت جلوگیری از "
                "بازنویسی دستی مشابه ردیف‌های مغایرت آبان (دستگاه ۷۱۴ در ۱۴ آبان و ۷۱۵ در ۱۲ آبان)."
            ),
            (
                f"تکمیل شیت‌های روزهای فاقد اطلاعات ({cov['missing_days_count']} روز در {summary['period_label_fa']}) "
                f"در صورت وجود آمار عملیاتی، تا پوشش ماهانه به ۱۰۰٪ برسد."
            ),
        ]

        return {
            "found": True,
            "period": summary["period"],
            "period_label_fa": summary["period_label_fa"],
            "questions": {
                "main_cause": q1,
                "devices_needing_review": q2,
                "high_repair_devices": q3,
                "operational_downtime": q4,
                "repeated_note_themes": q5,
                "next_month_actions": recommendations,
            },
            "attention_device_keys": [d["canonical_device_key"] for d in attention_devs],
            "discrepancy_count": len(discrepancies),
            "ambiguous_devices_count": id_ov["ambiguous_devices_count"],
            "unregistered_devices_count": id_ov["unregistered_devices_count"],
        }


def build_device_narrative(dev: dict[str, Any]) -> dict[str, str]:
    """Build concise, strictly evidence-based Persian analysis & recommendation for one device scorecard."""
    days = dev["days_with_data"]
    if days <= 0 or dev["base_minutes"] <= 0:
        return {
            "analysis": "اطلاعات کافی برای تحلیل عملکرد این دستگاه در این دوره ثبت نشده است.",
            "recommendation": "بررسی تکمیل ثبت داده‌های روزانه دستگاه در دوره بعدی.",
        }

    code = dev["raw_device_code"]
    work_pct = dev["work_pct"]
    down_pct = dev["downtime_pct"]
    down_m = dev["reported_total_downtime_minutes"]
    causes_m = dev["computed_causes_minutes"]
    rep_m = dev["repair_minutes"]
    top_cause = dev.get("top_cause_label")
    topics = dev.get("top_topics") or []

    parts = [
        f"دستگاه در {days} روز دارای اطلاعات ({dev['row_count']} ردیف شیفت) با "
        f"{dev['base_hhmm']} ساعت مبنا، {dev['reported_net_work_hhmm']} ساعت کارکرد ({work_pct}٪) "
        f"و {dev['reported_total_downtime_hhmm']} ساعت توقف ثبت‌شده ({down_pct}٪) داشته است."
    ]
    if down_m == 0 and causes_m == 0:
        parts.append("در روزهای دارای اطلاعات هیچ توقفی برای این دستگاه ثبت نشده است.")
    else:
        if top_cause:
            top_item = max(dev["cause_breakdown"], key=lambda x: x["minutes"])
            parts.append(
                f"علت اصلی توقف «{top_cause}» با {top_item['hhmm']} ساعت ({top_item['share_pct']}٪ از علل توقف) بوده است."
            )
        if dev["days_zero_work"] > 0:
            parts.append(
                f"در {dev['days_zero_work']} روز ثبت‌شده بدون کارکرد بوده (بیشترین توالی ثبت‌شده: "
                f"{dev['max_consecutive_zero_work_days']} روز)."
            )
    if dev["discrepancy_count"] > 0:
        delta = causes_m - down_m
        parts.append(
            f"هشدار کیفیت داده: در {dev['discrepancy_count']} ردیف اختلاف {format_minutes_hhmm(delta)} "
            f"بین مجموع علل توقف و جمع توقف ثبت‌شده وجود دارد."
        )

    # Action recommendation tailored strictly to evidence
    if dev["identity_status"] == "AMBIGUOUS":
        rec = (
            f"تعیین تکلیف هویت دقیق کد {code} با افزودن ستون نوع دستگاه در فایل ماهانه؛ "
            + ("بررسی پرونده تعمیرات دستگاه به‌دلیل سهم قابل‌توجه توقف تعمیراتی." if rep_m >= 1200 else "پایش تداوم عملکرد پس از تثبیت هویت.")
        )
    elif dev["discrepancy_count"] > 0:
        rec = (
            "بررسی و اصلاح فرمول جمع توقف در ردیف دارای مغایرت فایل منبع؛ "
            + ("همچنین پایش توقفات تعمیراتی و برنامه‌ریزی سرویس پیشگیرانه." if rep_m >= 1200 else "پایش علل بی‌باری و هماهنگی شیفت شب.")
        )
    elif rep_m >= 2400 or dev["repair_pct_of_base"] >= 25.0:
        topic_hint = f" (موضوعات پرتکرار: {'، '.join(t['topic_label'] for t in topics[:2])})" if topics else ""
        rec = f"اولویت بررسی فنی و پرونده تعمیرات جهت کاهش توقف تعمیراتی{topic_hint} و کنترل تأمین قطعه."
    elif dev["driver_delay_minutes"] >= 1800:
        rec = "بررسی برنامه تخصیص راننده و پوشش شیفت جهت کاهش توقف ناشی از نبود یا تأخیر راننده."
    elif dev["blast_delay_minutes"] >= 600 and rep_m < 600:
        rec = "عملکرد فنی پایدار است؛ عمده توقف منشأ عملیاتی (تأخیر انفجار) داشته و نیازمند هماهنگی عملیاتی است."
    elif down_m == 0 and causes_m == 0:
        rec = "تداوم برنامه نگهداری پیشگیرانه (PM) جهت حفظ کارکرد بدون توقف."
    else:
        rec = "پایش مستمر شاخص‌های کارکرد و ثبت دقیق‌تر جزئیات ملاحظات در دوره بعدی."

    if dev["identity_status"] == "UNREGISTERED":
        rec += " همچنین ثبت رسمی این دستگاه در Registry ناوگان (fleet_ops.db) پیشنهاد می‌شود."

    return {"analysis": " ".join(parts), "recommendation": rec}
