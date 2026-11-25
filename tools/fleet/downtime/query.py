"""Fast, bounded, pure-stdlib Query API powering KomatsoAI's Fleet Downtime AI tools.

Every function returns deterministic, source-attributed JSON-serializable dicts directly from
SQLite precomputed summaries/indexes in milliseconds without touching Excel or running an LLM.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import time
from typing import Any

from . import DEFAULT_DB_PATH, DEFAULT_RUNTIME_DIR
from .analytics import DowntimeAnalytics
from .normalization import (
    CAUSE_FIELDS,
    CAUSE_LABEL_BY_KEY,
    JALALI_MONTH_ALIASES,
    format_minutes_hhmm,
    normalize_persian,
    parse_jalali_date,
    parse_period,
    period_label_fa,
)
from .registry import DEFAULT_FLEET_DB, DEFAULT_MAINT_LAYOUT, FleetDeviceRegistry
from .repository import DowntimeRepository

DEFAULT_SOURCE_UNIT_NOTE = "واحد زمان: ساعت:دقیقه (HH:MM) — مجموع ماهانه بدون محدودیت ۲۴ ساعته محاسبه شده است."

CAUSE_QUERY_ALIASES: dict[str, str] = {
    "repair": "repair_minutes",
    "repair_minutes": "repair_minutes",
    "تعمیرات": "repair_minutes",
    "تعمیراتی": "repair_minutes",
    "خرابی": "repair_minutes",
    "no_load": "no_load_minutes",
    "no_load_minutes": "no_load_minutes",
    "بیباری": "no_load_minutes",
    "بی باری": "no_load_minutes",
    "اتمام بار": "no_load_minutes",
    "blast": "blast_delay_minutes",
    "blast_delay": "blast_delay_minutes",
    "blast_delay_minutes": "blast_delay_minutes",
    "انفجار": "blast_delay_minutes",
    "تاخیر انفجار": "blast_delay_minutes",
    "تأخیر انفجار": "blast_delay_minutes",
    "no_loader": "no_loader_minutes",
    "no_loader_minutes": "no_loader_minutes",
    "بارگیر": "no_loader_minutes",
    "نبود بارگیر": "no_loader_minutes",
    "driver": "driver_delay_minutes",
    "driver_delay": "driver_delay_minutes",
    "driver_delay_minutes": "driver_delay_minutes",
    "راننده": "driver_delay_minutes",
    "تاخیر راننده": "driver_delay_minutes",
    "تأخیر راننده": "driver_delay_minutes",
    "نبود راننده": "driver_delay_minutes",
}


class DowntimeQueryService:
    """Bounded, read-only query facade for all 9 Hermes Fleet Downtime tools."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        *,
        fleet_db_path: Path | str = DEFAULT_FLEET_DB,
        runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
    ) -> None:
        self.db_path = Path(db_path)
        self.runtime_dir = Path(runtime_dir)
        self.repo = DowntimeRepository(self.db_path)
        self.analytics = DowntimeAnalytics(self.repo)
        self._fleet_db_path = Path(fleet_db_path)
        self._registry: FleetDeviceRegistry | None = None

    @property
    def registry(self) -> FleetDeviceRegistry:
        if self._registry is None:
            self._registry = FleetDeviceRegistry(self._fleet_db_path, DEFAULT_MAINT_LAYOUT)
        return self._registry

    def _resolve_period_arg(self, period: str | None) -> tuple[str | None, str | None]:
        """Resolve an optional period arg; supports 'آبان', 'آبان ۱۴۰۵', '1405-08', or latest active period."""
        periods = self.repo.list_periods()
        if not period or not str(period).strip():
            if not periods:
                return None, "هنوز هیچ دوره ماهانه‌ای از توقفات ماشین‌آلات در سامانه وارد نشده است."
            return periods[0]["period"], None

        raw = normalize_persian(period)
        # Try full period first (YYYY-MM or Month YYYY)
        try:
            return parse_period(raw), None
        except ValueError:
            pass

        # Month-only reference (e.g., 'آبان' or 'ابان' or 'مهر'): infer active year if unambiguous
        plain = raw.replace("آ", "ا")
        found_month = None
        for alias, mnum in sorted(JALALI_MONTH_ALIASES.items(), key=lambda kv: -len(kv[0])):
            if alias.replace("آ", "ا") in plain:
                found_month = mnum
                break
        if found_month is not None:
            years = sorted({int(p["period"].split("-")[0]) for p in periods}, reverse=True)
            year = years[0] if years else 1405
            return f"{year:04d}-{found_month:02d}", None

        return None, f"فرمت دوره «{period}» قابل تشخیص نیست. لطفاً به‌صورت مثلاً «1405-08» یا «آبان ۱۴۰۵» وارد کنید."

    def _default_year(self) -> int:
        periods = self.repo.list_periods()
        if periods:
            return int(periods[0]["period"].split("-")[0])
        return 1405

    def _resolve_device_in_period(self, period: str, device_code: str) -> dict[str, Any]:
        """Resolve user device query strictly against Registry and active period tracks."""
        norm_p = parse_period(period)
        ver = self.repo.get_active_version(norm_p)
        if not ver:
            return {
                "status": "period_missing",
                "message": f"اطلاعات دوره {period_label_fa(norm_p)} ({norm_p}) هنوز وارد نشده است.",
            }
        vid = int(ver["version_id"])

        # Direct canonical key check (e.g., if user explicitly asks AMBIG:601#1 or UNREG:717)
        raw_clean = normalize_persian(device_code).strip()
        with closing(self.repo._connect(write=False)) as conn:
            direct = conn.execute(
                "SELECT canonical_device_key, raw_device_code, device_name, device_group, identity_status, "
                "ambiguous_candidates_json FROM summary_device_monthly "
                "WHERE version_id = ? AND UPPER(canonical_device_key) = UPPER(?)",
                (vid, raw_clean),
            ).fetchone()
            if direct:
                return {
                    "status": "resolved",
                    "canonical_device_key": direct["canonical_device_key"],
                    "row": dict(direct),
                    "version": ver,
                }

        res = self.registry.resolve_user_query_device(device_code)
        if res["status"] == "ambiguous":
            # Look up any ambiguous tracks in this period for that raw_code to inform disambiguation
            raw_c = res["raw_code"]
            with closing(self.repo._connect(write=False)) as conn:
                tracks = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT canonical_device_key, device_name, row_count, base_minutes, "
                        "reported_net_work_minutes, reported_total_downtime_minutes "
                        "FROM summary_device_monthly WHERE version_id = ? AND raw_device_code = ? "
                        "ORDER BY canonical_device_key",
                        (vid, raw_c),
                    ).fetchall()
                ]
            cand_labels = " یا ".join(
                f"{c['canonical_code']} ({c['device_name']})" for c in res["candidates"]
            )
            return {
                "status": "ambiguous",
                "raw_code": raw_c,
                "candidates": res["candidates"],
                "period_tracks": [
                    {
                        "canonical_device_key": t["canonical_device_key"],
                        "device_name": t["device_name"],
                        "base_hhmm": format_minutes_hhmm(t["base_minutes"]),
                        "net_work_hhmm": format_minutes_hhmm(t["reported_net_work_minutes"]),
                        "downtime_hhmm": format_minutes_hhmm(t["reported_total_downtime_minutes"]),
                    }
                    for t in tracks
                ],
                "message": (
                    f"کد دستگاه «{raw_c}» مبهم است و در Registry ناوگان به بیش از یک دستگاه اشاره دارد: {cand_labels}. "
                    f"همچنین در فایل منبع دوره {period_label_fa(norm_p)} ستون نوع دستگاه برای کد {raw_c} درج نشده است. "
                    f"لطفاً مشخص کنید کدام دستگاه (یا کدام ردیف/کد مرجع دقیق مانند {tracks[0]['canonical_device_key'] if tracks else raw_c}) مدنظر است."
                ),
            }

        if res["status"] == "resolved_from_type_qualifier":
            # User specified type (e.g., 'بیل 601' or 'لودر 601'), check if period has confirmed key or only AMBIG tracks
            key = res["canonical_device_key"]
            raw_c = res["raw_code"]
            with closing(self.repo._connect(write=False)) as conn:
                exact = conn.execute(
                    "SELECT * FROM summary_device_monthly WHERE version_id = ? AND canonical_device_key = ?",
                    (vid, key),
                ).fetchone()
                if exact:
                    return {"status": "resolved", "canonical_device_key": key, "row": dict(exact), "version": ver}
                tracks = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT canonical_device_key, device_name, base_minutes, reported_net_work_minutes, "
                        "reported_total_downtime_minutes FROM summary_device_monthly "
                        "WHERE version_id = ? AND raw_device_code = ? ORDER BY canonical_device_key",
                        (vid, raw_c),
                    ).fetchall()
                ]
            return {
                "status": "ambiguous_in_source",
                "requested_canonical": key,
                "raw_code": raw_c,
                "period_tracks": [
                    {
                        "canonical_device_key": t["canonical_device_key"],
                        "device_name": t["device_name"],
                        "base_hhmm": format_minutes_hhmm(t["base_minutes"]),
                        "net_work_hhmm": format_minutes_hhmm(t["reported_net_work_minutes"]),
                        "downtime_hhmm": format_minutes_hhmm(t["reported_total_downtime_minutes"]),
                    }
                    for t in tracks
                ],
                "message": res["note"] + " جهت جلوگیری از نگاشت حدسی، آمار کد مبهم به‌صورت مستقل علامت‌گذاری شده است.",
            }

        if res["status"] in ("resolved", "unregistered"):
            key = res["canonical_device_key"]
            with closing(self.repo._connect(write=False)) as conn:
                row = conn.execute(
                    "SELECT * FROM summary_device_monthly WHERE version_id = ? AND canonical_device_key = ?",
                    (vid, key),
                ).fetchone()
                if row:
                    return {
                        "status": "resolved",
                        "canonical_device_key": key,
                        "row": dict(row),
                        "version": ver,
                        "alias_applied": res.get("alias_applied"),
                    }
            return {
                "status": "not_in_period",
                "canonical_device_key": key,
                "message": f"برای دستگاه «{device_code}» ({key}) رکوردی در دوره {period_label_fa(norm_p)} ثبت نشده است.",
            }

        return {
            "status": "not_found",
            "message": f"کد دستگاه «{device_code}» در Registry ناوگان و داده‌های دوره {period_label_fa(norm_p)} شناسایی نشد.",
        }

    # --- 9 Public Tool Methods ---

    def list_downtime_periods(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        periods = self.analytics.list_periods()
        return {
            "ok": True,
            "periods_count": len(periods),
            "periods": periods,
            "unit_note": DEFAULT_SOURCE_UNIT_NOTE,
            "message": (
                f"تعداد {len(periods)} دوره ماهانه در سامانه ثبت شده است."
                if periods
                else "هنوز هیچ دوره ماهانه‌ای از توقفات ماشین‌آلات وارد نشده است."
            ),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    def get_daily_downtime(
        self,
        date: str,
        *,
        device_code: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            detail = self.analytics.daily_detail(date, default_year=self._default_year())
        except ValueError as exc:
            return {
                "ok": False,
                "error": f"تاریخ نامعتبر است: {exc}",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }
        if not detail["found"]:
            return {
                "ok": True,
                "found": False,
                "status": detail["status"],
                "period": detail.get("period"),
                "jalali_date": detail.get("jalali_date"),
                "message": detail["message"],
                "unit_note": DEFAULT_SOURCE_UNIT_NOTE,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        if device_code:
            dev_res = self._resolve_device_in_period(detail["period"], device_code)
            if dev_res["status"] != "resolved":
                return {
                    "ok": True,
                    "found": False,
                    "status": dev_res["status"],
                    "period": detail["period"],
                    "jalali_date": detail["jalali_date"],
                    "message": dev_res["message"],
                    "candidates": dev_res.get("candidates"),
                    "period_tracks": dev_res.get("period_tracks"),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                }
            dkey = dev_res["canonical_device_key"]
            matched = [r for r in detail["records"] if r["canonical_device_key"] == dkey]
            return {
                "ok": True,
                "found": bool(matched),
                "period": detail["period"],
                "period_label_fa": detail["period_label_fa"],
                "jalali_date": detail["jalali_date"],
                "source_filename": detail["source_filename"],
                "version_number": detail["version_number"],
                "device_key": dkey,
                "records": matched,
                "unit_note": DEFAULT_SOURCE_UNIT_NOTE,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        lim = max(1, min(int(limit), 60))
        all_recs = detail["records"]
        nonzero_down = [r for r in all_recs if r["reported_total_downtime_minutes"] > 0 or r["computed_causes_minutes"] > 0]
        return {
            "ok": True,
            "found": True,
            "period": detail["period"],
            "period_label_fa": detail["period_label_fa"],
            "jalali_date": detail["jalali_date"],
            "source_filename": detail["source_filename"],
            "version_number": detail["version_number"],
            "summary": detail["summary"],
            "causes": detail["causes"],
            "devices_with_downtime_count": len({r["canonical_device_key"] for r in nonzero_down}),
            "top_downtime_rows": nonzero_down[:lim],
            "total_rows_in_day": len(all_recs),
            "truncated": len(nonzero_down) > lim,
            "unit_note": DEFAULT_SOURCE_UNIT_NOTE,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    def get_monthly_downtime_summary(self, period: str | None = None) -> dict[str, Any]:
        t0 = time.perf_counter()
        resolved_p, err = self._resolve_period_arg(period)
        if err or not resolved_p:
            return {"ok": False, "error": err, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}
        summary = self.analytics.fleet_monthly_summary(resolved_p)
        summary["ok"] = True
        summary["unit_note"] = DEFAULT_SOURCE_UNIT_NOTE
        summary["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return summary

    def get_device_downtime(
        self,
        device_code: str,
        *,
        period: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        if date:
            res = self.get_daily_downtime(date, device_code=device_code)
            res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            return res

        resolved_p, err = self._resolve_period_arg(period)
        if err or not resolved_p:
            return {"ok": False, "error": err, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}

        dev_res = self._resolve_device_in_period(resolved_p, device_code)
        if dev_res["status"] != "resolved":
            return {
                "ok": True,
                "found": False,
                "status": dev_res["status"],
                "period": resolved_p,
                "period_label_fa": period_label_fa(resolved_p),
                "message": dev_res["message"],
                "candidates": dev_res.get("candidates"),
                "period_tracks": dev_res.get("period_tracks"),
                "unit_note": DEFAULT_SOURCE_UNIT_NOTE,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        detail = self.analytics.device_detail(resolved_p, dev_res["canonical_device_key"])
        if detail["found"]:
            # Bound daily_series for compact tool output while preserving all key metrics
            dev = dict(detail["device"])
            dev["daily_series_count"] = len(dev.get("daily_series") or [])
            dev["daily_series"] = [
                {
                    "jalali_date": d["jalali_date"],
                    "base_hhmm": format_minutes_hhmm(d["base_minutes"]),
                    "net_work_hhmm": format_minutes_hhmm(d["reported_net_work_minutes"]),
                    "downtime_hhmm": format_minutes_hhmm(d["reported_total_downtime_minutes"]),
                    "repair_hhmm": format_minutes_hhmm(d["repair_minutes"]),
                    "work_pct": d["work_pct"],
                }
                for d in (dev.get("daily_series") or [])
            ]
            detail["device"] = dev
        detail["ok"] = True
        detail["alias_applied"] = dev_res.get("alias_applied")
        detail["unit_note"] = DEFAULT_SOURCE_UNIT_NOTE
        detail["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return detail

    def rank_devices_by_downtime(
        self,
        *,
        period: str | None = None,
        metric: str = "downtime_hours",
        order: str = "desc",
        limit: int = 10,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        resolved_p, err = self._resolve_period_arg(period)
        if err or not resolved_p:
            return {"ok": False, "error": err, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}
        try:
            res = self.analytics.rank_devices(resolved_p, metric=metric, order=order, limit=limit)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}
        res["ok"] = True
        res["unit_note"] = DEFAULT_SOURCE_UNIT_NOTE
        res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return res

    def get_downtime_causes(
        self,
        *,
        period: str | None = None,
        device_code: str | None = None,
        cause: str | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        resolved_p, err = self._resolve_period_arg(period)
        if err or not resolved_p:
            return {"ok": False, "error": err, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}

        target_cause_key = None
        if cause:
            ck = normalize_persian(cause).casefold()
            target_cause_key = CAUSE_QUERY_ALIASES.get(ck) or CAUSE_QUERY_ALIASES.get(ck.replace(" ", ""))
            if not target_cause_key:
                for alias, mapped in CAUSE_QUERY_ALIASES.items():
                    if alias in ck:
                        target_cause_key = mapped
                        break

        if device_code:
            dev_res = self._resolve_device_in_period(resolved_p, device_code)
            if dev_res["status"] != "resolved":
                return {
                    "ok": True,
                    "found": False,
                    "status": dev_res["status"],
                    "period": resolved_p,
                    "message": dev_res["message"],
                    "candidates": dev_res.get("candidates"),
                    "period_tracks": dev_res.get("period_tracks"),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                }
            detail = self.analytics.device_detail(resolved_p, dev_res["canonical_device_key"])
            causes = detail["device"]["cause_breakdown"]
            if target_cause_key:
                causes = [c for c in causes if c["cause_key"] == target_cause_key]
            return {
                "ok": True,
                "found": True,
                "scope": "device",
                "period": resolved_p,
                "period_label_fa": period_label_fa(resolved_p),
                "source_filename": detail["source_filename"],
                "device_key": dev_res["canonical_device_key"],
                "device_name": detail["device"]["device_name"],
                "causes": causes,
                "unit_note": DEFAULT_SOURCE_UNIT_NOTE,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        summary = self.analytics.fleet_monthly_summary(resolved_p)
        if not summary["found"]:
            summary["ok"] = True
            summary["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            return summary

        causes = summary["causes"]
        top_devices_for_cause = None
        if target_cause_key:
            causes = [c for c in causes if c["cause_key"] == target_cause_key]
            vid = summary["version"]["version_id"]
            with closing(self.repo._connect(write=False)) as conn:
                rows = conn.execute(
                    f"SELECT canonical_device_key, raw_device_code, device_name, {target_cause_key} AS cause_minutes, "
                    "base_minutes FROM summary_device_monthly WHERE version_id = ? AND "
                    f"{target_cause_key} > 0 ORDER BY {target_cause_key} DESC LIMIT 10",
                    (vid,),
                ).fetchall()
                top_devices_for_cause = [
                    {
                        "canonical_device_key": r["canonical_device_key"],
                        "raw_device_code": r["raw_device_code"],
                        "device_name": r["device_name"],
                        "cause_minutes": r["cause_minutes"],
                        "cause_hhmm": format_minutes_hhmm(r["cause_minutes"]),
                    }
                    for r in rows
                ]

        return {
            "ok": True,
            "found": True,
            "scope": "fleet",
            "period": resolved_p,
            "period_label_fa": summary["period_label_fa"],
            "source_filename": summary["version"]["source_filename"],
            "version_number": summary["version"]["version_number"],
            "maintenance_vs_operational": {
                "maintenance_hhmm": summary["fleet"]["maintenance_downtime_hhmm"],
                "maintenance_pct_of_causes": summary["fleet"]["maintenance_pct_of_causes"],
                "maintenance_pct_of_base": summary["fleet"]["maintenance_pct_of_base"],
                "operational_hhmm": summary["fleet"]["operational_downtime_hhmm"],
                "operational_pct_of_causes": summary["fleet"]["operational_pct_of_causes"],
                "operational_pct_of_base": summary["fleet"]["operational_pct_of_base"],
            },
            "causes": causes,
            "top_devices_for_cause": top_devices_for_cause,
            "unit_note": DEFAULT_SOURCE_UNIT_NOTE,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    def get_downtime_notes(
        self,
        *,
        period: str | None = None,
        device_code: str | None = None,
        date: str | None = None,
        keyword: str | None = None,
        limit: int = 15,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        jdate_filter = None
        if date:
            try:
                jdate_filter, _ = parse_jalali_date(date, default_year=self._default_year())
            except ValueError as exc:
                return {"ok": False, "error": f"تاریخ نامعتبر است: {exc}", "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}
            period = f"{jdate_filter[:4]}-{jdate_filter[5:7]}"

        resolved_p, err = self._resolve_period_arg(period)
        if err or not resolved_p:
            return {"ok": False, "error": err, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}

        ver = self.repo.get_active_version(resolved_p)
        if not ver:
            return {
                "ok": True,
                "found": False,
                "period": resolved_p,
                "message": f"اطلاعات دوره {period_label_fa(resolved_p)} ({resolved_p}) هنوز وارد نشده است.",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }
        vid = int(ver["version_id"])

        dev_key = None
        if device_code:
            dev_res = self._resolve_device_in_period(resolved_p, device_code)
            if dev_res["status"] != "resolved":
                return {
                    "ok": True,
                    "found": False,
                    "status": dev_res["status"],
                    "period": resolved_p,
                    "message": dev_res["message"],
                    "candidates": dev_res.get("candidates"),
                    "period_tracks": dev_res.get("period_tracks"),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                }
            dev_key = dev_res["canonical_device_key"]

        lim = max(1, min(int(limit), 50))
        where = ["version_id = ?", "notes_normalized != ''"]
        params: list[Any] = [vid]
        if dev_key:
            where.append("canonical_device_key = ?")
            params.append(dev_key)
        if jdate_filter:
            where.append("jalali_date = ?")
            params.append(jdate_filter)
        if keyword:
            kw = normalize_persian(keyword)
            where.append("(notes_normalized LIKE ? OR note_topics_json LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%"])

        with closing(self.repo._connect(write=False)) as conn:
            total_matches = conn.execute(
                f"SELECT COUNT(*) FROM downtime_records WHERE {' AND '.join(where)}", params
            ).fetchone()[0]
            rows = [
                dict(r)
                for r in conn.execute(
                    f"""
                    SELECT jalali_date, sheet_name, excel_row_num, canonical_device_key, raw_device_code,
                           device_name, shift, reported_total_downtime_minutes, repair_minutes,
                           notes_raw, note_topics_json, validation_status
                    FROM downtime_records
                    WHERE {' AND '.join(where)}
                    ORDER BY iso_date DESC, reported_total_downtime_minutes DESC
                    LIMIT ?
                    """,
                    [*params, lim],
                ).fetchall()
            ]
            # Topic summary (filtered by device if given)
            if dev_key:
                topics = [
                    dict(r)
                    for r in conn.execute(
                        """
                        SELECT topic_key, topic_label, COUNT(*) AS mention_rows
                        FROM record_note_topics
                        WHERE version_id = ? AND canonical_device_key = ?
                        GROUP BY topic_key, topic_label
                        ORDER BY mention_rows DESC LIMIT 10
                        """,
                        (vid, dev_key),
                    ).fetchall()
                ]
            else:
                topics = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT topic_key, topic_label, mention_rows, affected_devices "
                        "FROM summary_note_topics_monthly WHERE version_id = ? "
                        "ORDER BY mention_rows DESC LIMIT 12",
                        (vid,),
                    ).fetchall()
                ]

        out_rows = []
        for r in rows:
            out_rows.append(
                {
                    "jalali_date": r["jalali_date"],
                    "sheet_name": r["sheet_name"],
                    "excel_row_num": r["excel_row_num"],
                    "canonical_device_key": r["canonical_device_key"],
                    "raw_device_code": r["raw_device_code"],
                    "device_name": r["device_name"],
                    "shift": r["shift"],
                    "downtime_hhmm": format_minutes_hhmm(r["reported_total_downtime_minutes"]),
                    "repair_hhmm": format_minutes_hhmm(r["repair_minutes"]),
                    "notes_raw": r["notes_raw"],
                    "topics": [t["topic_label"] for t in json.loads(r["note_topics_json"])],
                }
            )
        return {
            "ok": True,
            "found": total_matches > 0,
            "period": resolved_p,
            "period_label_fa": period_label_fa(resolved_p),
            "source_filename": ver["source_filename"],
            "total_matching_rows": total_matches,
            "returned_rows": len(out_rows),
            "top_topics": topics,
            "notes": out_rows,
            "qualitative_disclaimer": "موضوعات استخراج‌شده از ستون ملاحظات تحلیل کیفی بوده و جایگزین طبقه‌بندی عددی علل توقف نیستند.",
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    def compare_device_performance(
        self,
        device_a: str,
        device_b: str,
        *,
        period: str | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        resolved_p, err = self._resolve_period_arg(period)
        if err or not resolved_p:
            return {"ok": False, "error": err, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}

        res_a = self._resolve_device_in_period(resolved_p, device_a)
        res_b = self._resolve_device_in_period(resolved_p, device_b)
        issues = []
        for label, r in (("دستگاه اول", res_a), ("دستگاه دوم", res_b)):
            if r["status"] != "resolved":
                issues.append({"slot": label, "status": r["status"], "message": r["message"], "candidates": r.get("candidates")})
        if issues:
            return {
                "ok": True,
                "found": False,
                "period": resolved_p,
                "period_label_fa": period_label_fa(resolved_p),
                "issues": issues,
                "message": " | ".join(x["message"] for x in issues),
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        det_a = self.analytics.device_detail(resolved_p, res_a["canonical_device_key"])["device"]
        det_b = self.analytics.device_detail(resolved_p, res_b["canonical_device_key"])["device"]

        def compact(d: dict[str, Any]) -> dict[str, Any]:
            return {
                "canonical_device_key": d["canonical_device_key"],
                "raw_device_code": d["raw_device_code"],
                "device_name": d["device_name"],
                "device_group": d["device_group"],
                "identity_status": d["identity_status"],
                "days_with_data": d["days_with_data"],
                "row_count": d["row_count"],
                "night_shift_rows": d["night_shift_rows"],
                "base_minutes": d["base_minutes"],
                "base_hhmm": d["base_hhmm"],
                "net_work_minutes": d["reported_net_work_minutes"],
                "net_work_hhmm": d["reported_net_work_hhmm"],
                "downtime_minutes": d["reported_total_downtime_minutes"],
                "downtime_hhmm": d["reported_total_downtime_hhmm"],
                "repair_minutes": d["repair_minutes"],
                "repair_hhmm": d["repair_hhmm"],
                "work_pct": d["work_pct"],
                "downtime_pct": d["downtime_pct"],
                "repair_pct_of_base": d["repair_pct_of_base"],
                "days_zero_work": d["days_zero_work"],
                "top_cause_label": d["top_cause_label"],
                "cause_breakdown": d["cause_breakdown"],
                "discrepancy_count": d["discrepancy_count"],
            }

        ca, cb = compact(det_a), compact(det_b)
        diff = {
            "base_diff_minutes": ca["base_minutes"] - cb["base_minutes"],
            "base_diff_hhmm": format_minutes_hhmm(ca["base_minutes"] - cb["base_minutes"]),
            "net_work_diff_minutes": ca["net_work_minutes"] - cb["net_work_minutes"],
            "net_work_diff_hhmm": format_minutes_hhmm(ca["net_work_minutes"] - cb["net_work_minutes"]),
            "downtime_diff_minutes": ca["downtime_minutes"] - cb["downtime_minutes"],
            "downtime_diff_hhmm": format_minutes_hhmm(ca["downtime_minutes"] - cb["downtime_minutes"]),
            "repair_diff_minutes": ca["repair_minutes"] - cb["repair_minutes"],
            "repair_diff_hhmm": format_minutes_hhmm(ca["repair_minutes"] - cb["repair_minutes"]),
            "work_pct_diff": round(ca["work_pct"] - cb["work_pct"], 2),
            "downtime_pct_diff": round(ca["downtime_pct"] - cb["downtime_pct"], 2),
            "higher_work_pct_device": ca["canonical_device_key"] if ca["work_pct"] >= cb["work_pct"] else cb["canonical_device_key"],
            "lower_downtime_device": ca["canonical_device_key"] if ca["downtime_minutes"] <= cb["downtime_minutes"] else cb["canonical_device_key"],
        }
        return {
            "ok": True,
            "found": True,
            "period": resolved_p,
            "period_label_fa": period_label_fa(resolved_p),
            "source_filename": res_a["version"]["source_filename"],
            "device_a": ca,
            "device_b": cb,
            "comparison_delta_a_minus_b": diff,
            "unit_note": DEFAULT_SOURCE_UNIT_NOTE,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    def get_monthly_downtime_report(self, period: str | None = None) -> dict[str, Any]:
        """Return cached monthly PDF metadata & executive summary (never regenerates PDF during chat)."""
        t0 = time.perf_counter()
        resolved_p, err = self._resolve_period_arg(period)
        if err or not resolved_p:
            return {"ok": False, "error": err, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}

        ver = self.repo.get_active_version(resolved_p)
        if not ver:
            return {
                "ok": True,
                "found": False,
                "period": resolved_p,
                "message": f"گزارش دوره {period_label_fa(resolved_p)} ({resolved_p}) موجود نیست زیرا داده این ماه هنوز وارد نشده است.",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        artifact = self.repo.get_latest_report_artifact(resolved_p, version_id=int(ver["version_id"]))
        pdf_exists = bool(artifact and Path(artifact["pdf_path"]).is_file())
        summary = self.analytics.fleet_monthly_summary(resolved_p)
        insights = self.analytics.executive_insights(resolved_p)

        return {
            "ok": True,
            "found": True,
            "period": resolved_p,
            "period_label_fa": period_label_fa(resolved_p),
            "version_number": ver["version_number"],
            "source_filename": ver["source_filename"],
            "data_quality_status": ver["data_quality_status"],
            "pdf_ready": pdf_exists,
            "pdf_path": artifact["pdf_path"] if pdf_exists else None,
            "pdf_sha256": artifact["pdf_sha256"] if pdf_exists else None,
            "page_count": artifact["page_count"] if pdf_exists else None,
            "is_provisional": bool(artifact["is_provisional"]) if pdf_exists else True,
            "fleet_summary": summary["fleet"],
            "coverage": summary["coverage"],
            "top_causes": summary["causes"][:5],
            "executive_highlights": insights["questions"],
            "unit_note": DEFAULT_SOURCE_UNIT_NOTE,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
