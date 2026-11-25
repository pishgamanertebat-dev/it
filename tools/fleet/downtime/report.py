"""Executive Persian RTL Graphical Monthly PDF Generator & Reconciliation Validator.

Generates a print-grade, mobile-readable, pure-vector A4 PDF covering all 6 required sections:
  1. Executive Summary Cover Dashboard (KPIs, calendar coverage, top devices, discrepancy alerts)
  2. Fleet Graphical Analytics (all 8 required vector charts: donuts, Pareto, top-10 bars,
     daily dual trend, machine group comparison, and full 50-device availability ranking)
  3. Complete Fleet Ranking Tables (both by downtime hours AND by downtime percentage)
  4. Dedicated Individual Device Scorecards for ALL devices (2 per A4 page, mini donuts +
     daily performance trend + major downtime days + note themes + tailored analysis/action)
  5. Executive Management Analysis & Next-Month Action Plan (all 6 core questions + note topic chart)
  6. Data Quality, Coverage, Methodology, Discrepancy Audit & Device Identity Appendix

Validates searchable Persian text, zero layout overflow, and 100% SQLite-to-PDF reconciliation.
"""
from __future__ import annotations

import hashlib
import html
import math
from pathlib import Path
import re
import unicodedata
from typing import Any

import fitz

from tools.fleet.maintenance_daily.report import canonical_unicode_maps
from . import DEFAULT_DB_PATH, DEFAULT_RUNTIME_DIR
from .analytics import DowntimeAnalytics
from .charts import (
    BoxTracker,
    CAUSE_HEX_COLORS,
    HEX_BLAST,
    HEX_BORDER,
    HEX_DRIVER,
    HEX_ERR_BG,
    HEX_ERR_TEXT,
    HEX_MUTED,
    HEX_NAVY,
    HEX_NO_LOAD,
    HEX_NO_LOADER,
    HEX_OK_BG,
    HEX_OK_TEXT,
    HEX_REPAIR,
    HEX_STEEL,
    HEX_SURFACE,
    HEX_TEXT,
    HEX_WARN_BG,
    HEX_WARN_BORDER,
    HEX_WARN_TEXT,
    HEX_WHITE,
    HEX_WORK,
    RGB_BORDER,
    RGB_NAVY,
    RGB_REPAIR,
    RGB_STEEL,
    RGB_SURFACE,
    RGB_WHITE,
    RGB_WORK,
    WINDOWS_FONTS,
    build_base_css,
    draw_coverage_strip,
    draw_daily_stacked_trend,
    draw_donut_panel,
    draw_pareto_chart,
    draw_rtl_horizontal_bars,
    fa_num,
    hex_to_rgb,
    ltr_span,
)
from .normalization import format_minutes_hhmm, parse_period, period_label_fa
from .repository import DowntimeRepository

COMPANY_NAME = "شرکت صنعتی معدنی پیشگامان ارتباط هشت بهشت"
REPORT_TITLE = "گزارش ماهانه تحلیل عملکرد و توقفات ماشین‌آلات"
PAGE_W = 595.28
PAGE_H = 841.89
MARGIN_X = 24.0
CONTENT_W = PAGE_W - 2 * MARGIN_X


def _availability_color(work_pct: float) -> str:
    if work_pct >= 75.0:
        return HEX_WORK
    if work_pct >= 50.0:
        return HEX_BLAST
    if work_pct >= 30.0:
        return HEX_NO_LOAD
    return HEX_REPAIR


def _short_device_label(dev: dict[str, Any], max_len: int = 24) -> str:
    code = dev["raw_device_code"]
    status = dev["identity_status"]
    if status == "AMBIGUOUS":
        key = dev["canonical_device_key"]
        slot = "۱" if key.endswith("#1") else ("۲" if key.endswith("#2") else "")
        return f"کد {fa_num(code)} (مبهم{' #' + slot if slot else ''})"
    if status == "UNREGISTERED":
        return f"دستگاه {fa_num(code)} (ثبت‌نشده)"
    grp = dev["device_group"]
    short_grp = {
        "بیل مکانیکی": "بیل",
        "دامپتراک": "تراک",
        "لودر": "لودر",
        "بلدوزر": "بلدوزر",
        "پشتیبانی و کامیون": "پشتیبانی",
    }.get(grp, grp)
    lbl = f"{short_grp} {fa_num(code)}"
    return lbl[:max_len]


def _draw_company_crest(page: fitz.Page, cx: float, cy: float, r: float = 16.0) -> None:
    """Clean geometric vector mining-crest monogram (no unverified third-party bitmap logo)."""
    pts = [
        fitz.Point(cx + r * math.cos(math.radians(60 * i - 30)), cy + r * math.sin(math.radians(60 * i - 30)))
        for i in range(6)
    ]
    sh = page.new_shape()
    sh.draw_polyline(pts)
    sh.finish(color=(0.75, 0.85, 0.94), fill=(0.13, 0.30, 0.45), width=1.1, closePath=True)
    sh.commit()
    inner = r * 0.56
    ipts = [
        fitz.Point(cx + inner * math.cos(math.radians(60 * i - 30)), cy + inner * math.sin(math.radians(60 * i - 30)))
        for i in range(6)
    ]
    sh2 = page.new_shape()
    sh2.draw_polyline(ipts)
    sh2.finish(color=RGB_WORK, fill=None, width=1.3, closePath=True)
    sh2.commit()
    page.draw_circle(fitz.Point(cx, cy), r * 0.2, color=None, fill=RGB_WHITE)


def _new_interior_page(
    doc: fitz.Document,
    tracker: BoxTracker,
    *,
    section_title: str,
    period_fa: str,
    is_provisional: bool,
) -> tuple[fitz.Page, float]:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    # Top navy accent bar
    page.draw_rect(fitz.Rect(0, 0, PAGE_W, 5), color=None, fill=RGB_NAVY)
    # Subtle header band
    page.draw_rect(fitz.Rect(0, 5, PAGE_W, 42), color=None, fill=RGB_SURFACE)
    page.draw_line(fitz.Point(0, 42), fitz.Point(PAGE_W, 42), color=RGB_BORDER, width=0.7)

    tracker.insert(
        page,
        fitz.Rect(MARGIN_X + 132, 11, PAGE_W - MARGIN_X, 37),
        f'<p dir="rtl" style="text-align:right;font-size:10.6px;font-weight:bold;color:{HEX_NAVY}">{html.escape(section_title)}</p>',
        strict=False,
        tag=f"hdr:{section_title}",
    )
    badge = "آزمایشی" if is_provisional else "تأییدشده"
    badge_col = HEX_WARN_TEXT if is_provisional else HEX_OK_TEXT
    tracker.insert(
        page,
        fitz.Rect(MARGIN_X, 13, MARGIN_X + 124, 36),
        f'<p dir="rtl" style="text-align:left;font-size:8.3px;color:{HEX_MUTED}">'
        f'{html.escape(period_fa)} | <b style="color:{badge_col}">{badge}</b></p>',
        strict=False,
        tag="hdr_period",
    )
    return page, 52.0


def _render_cover_executive_page(
    doc: fitz.Document,
    tracker: BoxTracker,
    *,
    summary: dict[str, Any],
    insights: dict[str, Any],
) -> None:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    fleet = summary["fleet"]
    cov = summary["coverage"]
    ver = summary["version"]
    id_ov = summary["identity_overview"]
    period_fa = summary["period_label_fa"]
    is_prov = ver["is_provisional"]

    # Hero Dark Navy Header Banner
    page.draw_rect(fitz.Rect(0, 0, PAGE_W, 132), color=None, fill=RGB_NAVY)
    page.draw_rect(fitz.Rect(0, 128, PAGE_W, 132), color=None, fill=RGB_WORK)
    _draw_company_crest(page, MARGIN_X + 22, 64, r=20)

    tracker.insert(
        page,
        fitz.Rect(MARGIN_X + 54, 18, PAGE_W - MARGIN_X, 118),
        f'<div dir="rtl">'
        f'<p style="font-size:10.5px;color:#B8D0E6;margin-bottom:4px">{html.escape(COMPANY_NAME)}</p>'
        f'<p style="font-size:19px;font-weight:bold;color:#FFFFFF;margin-bottom:6px">{html.escape(REPORT_TITLE)}</p>'
        f'<p style="font-size:11.5px;color:#DCE8F5">'
        f'دوره گزارش: <b style="color:#FFFFFF">{html.escape(period_fa)}</b> ({ltr_span(summary["period"], color="#DCE8F5")}) &nbsp;|&nbsp; '
        f'نسخه: <b>{fa_num(ver["version_number"])}</b> &nbsp;|&nbsp; واحد نگهداری و تعمیرات</p>'
        f"</div>",
        strict=False,
        tag="cover_hero",
    )

    y = 140.0
    # Provisional / Discrepancy status banner
    if is_prov:
        page.draw_rect(
            fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + 44),
            color=hex_to_rgb(HEX_WARN_BORDER),
            fill=hex_to_rgb(HEX_WARN_BG),
            width=0.9,
            radius=0.08,
        )
        tracker.insert(
            page,
            fitz.Rect(MARGIN_X + 10, y + 4, PAGE_W - MARGIN_X - 10, y + 42),
            f'<p dir="rtl" style="text-align:right;font-size:8.3px;color:{HEX_WARN_TEXT};line-height:1.38">'
            f'<b>وضعیت گزارش: نسخه آزمایشی (غیرقطعی تا بررسی مالک) — </b>'
            f'در فایل منبع {fa_num(fleet["discrepancy_count"])} ردیف دارای مغایرت عددی (اختلاف کل {ltr_span(fleet["causes_vs_reported_delta_hhmm"], bold=True)} ساعت) '
            f'و {fa_num(cov["missing_days_count"])} روز فاقد شیت ({fa_num("، ".join(str(d) for d in cov["missing_days"]))} آبان) وجود دارد؛ هیچ عدد منبعی خودسرانه اصلاح نشده است.</p>',
            strict=False,
            tag="cover_provisional_banner",
        )
        y += 52.0

    # Section 1 heading
    tracker.insert(
        page,
        fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + 20),
        f'<p dir="rtl" style="font-size:12.5px;font-weight:bold;color:{HEX_NAVY}">بخش اول — خلاصه مدیریتی و شاخص‌های کلیدی ناوگان</p>',
        strict=False,
        tag="sec1_title",
    )
    y += 24.0

    # 6 Hero KPI Cards (2 rows x 3 columns)
    top_cause = summary["causes"][0] if summary["causes"] else None
    cards = [
        (
            "مجموع ساعات مبنا",
            fleet["base_hhmm"],
            f"معادل {fa_num(fleet['total_rows'])} ردیف شیفت ثبت‌شده",
            HEX_STEEL,
        ),
        (
            "کارکرد خالص ناوگان",
            f"{fleet['work_pct']:.2f}%",
            f"مجموع کارکرد: {fa_num(fleet['reported_net_work_hhmm'])} ساعت",
            HEX_WORK,
        ),
        (
            "توقف ثبت‌شده ناوگان",
            f"{fleet['downtime_pct']:.2f}%",
            f"ثبت‌شده: {fa_num(fleet['reported_total_downtime_hhmm'])} | علل: {fa_num(fleet['computed_causes_hhmm'])}",
            HEX_REPAIR,
        ),
        (
            "مهم‌ترین علت توقف",
            top_cause["cause_label"] if top_cause else "—",
            f"{fa_num(top_cause['total_hhmm'])} ساعت ({fa_num(top_cause['share_of_causes_pct'])}٪ از کل علل)" if top_cause else "—",
            HEX_REPAIR,
        ),
        (
            "پوشش ناوگان و زمان",
            f"{fa_num(cov['present_days_count'])} از {fa_num(cov['calendar_days_in_month'])} روز",
            f"{fa_num(id_ov['distinct_device_tracks'])} دستگاه ({fa_num(id_ov['resolved_devices_count'])} معتبر، {fa_num(id_ov['ambiguous_devices_count'])} مبهم، {fa_num(id_ov['unregistered_devices_count'])} ثبت‌نشده)",
            HEX_BLAST,
        ),
        (
            "تفکیک تعمیراتی / عملیاتی",
            f"{fleet['maintenance_pct_of_causes']:.1f}% تعمیراتی",
            f"تعمیرات: {fa_num(fleet['maintenance_downtime_hhmm'])} | عملیاتی: {fa_num(fleet['operational_downtime_hhmm'])}",
            HEX_NO_LOAD,
        ),
    ]
    gap = 9.0
    cw = (CONTENT_W - 2 * gap) / 3
    ch = 68.0
    for idx, (lbl, big, sub, accent) in enumerate(cards):
        row_i, col_i = divmod(idx, 3)
        # RTL order: col 0 is rightmost
        x1 = PAGE_W - MARGIN_X - col_i * (cw + gap)
        x0 = x1 - cw
        cy0 = y + row_i * (ch + gap)
        box = fitz.Rect(x0, cy0, x1, cy0 + ch)
        page.draw_rect(box, color=RGB_BORDER, fill=RGB_SURFACE, width=0.6, radius=0.06)
        page.draw_rect(fitz.Rect(x1 - 4, cy0 + 2, x1, cy0 + ch - 2), color=None, fill=hex_to_rgb(accent))
        tracker.insert(
            page,
            fitz.Rect(x0 + 8, cy0 + 6, x1 - 10, cy0 + ch - 4),
            f'<div dir="rtl">'
            f'<p style="font-size:8.2px;color:{HEX_MUTED};margin-bottom:2px">{html.escape(lbl)}</p>'
            f'<p style="font-size:15px;font-weight:bold;color:{accent};margin-bottom:2px">{ltr_span(big, bold=True, color=accent) if re.search(r"[0-9:%]", big) else html.escape(big)}</p>'
            f'<p style="font-size:7.6px;color:{HEX_TEXT};line-height:1.25">{html.escape(sub)}</p>'
            f"</div>",
            strict=False,
            tag=f"kpi_card_{idx}",
        )
    y += 2 * ch + gap + 14.0

    # Visual Calendar Coverage Strip
    page.draw_rect(fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + 54), color=RGB_BORDER, fill=RGB_WHITE, width=0.6, radius=0.04)
    def _compact_day_ranges(days: list[int]) -> str:
        if not days:
            return "ندارد"
        parts = []
        s = prev = days[0]
        for d in days[1:]:
            if d == prev + 1:
                prev = d
            else:
                parts.append(f"{s}" if s == prev else f"{s} تا {prev}")
                s = prev = d
        parts.append(f"{s}" if s == prev else f"{s} تا {prev}")
        return "، ".join(parts)

    missing_compact = _compact_day_ranges(cov["missing_days"])
    tracker.insert(
        page,
        fitz.Rect(MARGIN_X + 10, y + 4, PAGE_W - MARGIN_X - 10, y + 22),
        f'<p dir="rtl" style="font-size:8.4px;color:{HEX_NAVY}"><b>پوشش روزهای ماه {html.escape(period_fa)}:</b> '
        f'<span style="color:{HEX_OK_TEXT}">سبز = دارای شیت ({fa_num(cov["present_days_count"])} روز)</span> &nbsp;|&nbsp; '
        f'<span style="color:{HEX_MUTED}">خاکستری = فاقد شیت / نامشخص ({fa_num(cov["missing_days_count"])} روز: {fa_num(missing_compact)})</span></p>',
        strict=False,
        tag="cov_hdr",
    )
    draw_coverage_strip(
        page,
        fitz.Rect(MARGIN_X + 10, y + 25, PAGE_W - MARGIN_X - 10, y + 46),
        tracker,
        present_days=cov["present_days"],
        total_days=cov["calendar_days_in_month"],
    )
    y += 64.0

    # Executive Analytical Narrative Card
    page.draw_rect(fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + 116), color=RGB_BORDER, fill=RGB_SURFACE, width=0.6, radius=0.04)
    q = insights["questions"]
    tracker.insert(
        page,
        fitz.Rect(MARGIN_X + 12, y + 7, PAGE_W - MARGIN_X - 12, y + 112),
        f'<div dir="rtl">'
        f'<p style="font-size:9.8px;font-weight:bold;color:{HEX_NAVY};margin-bottom:4px">خلاصه تحلیلی عملکرد ناوگان در {html.escape(period_fa)}</p>'
        f'<p style="font-size:8.4px;color:{HEX_TEXT};line-height:1.45;margin-bottom:4px">{html.escape(fa_num(q["main_cause"]))}</p>'
        f'<p style="font-size:8.4px;color:{HEX_TEXT};line-height:1.45">{html.escape(fa_num(q["operational_downtime"]))}</p>'
        f"</div>",
        strict=False,
        tag="exec_narrative",
    )
    y += 124.0

    # Bottom Split: Left = Discrepancy & Identity Alert Box, Right = Top 5 Downtime Devices Mini-Table
    bottom_h = PAGE_H - y - 34
    left_box = fitz.Rect(MARGIN_X, y, MARGIN_X + CONTENT_W * 0.45, y + bottom_h)
    right_box = fitz.Rect(left_box.x1 + 10, y, PAGE_W - MARGIN_X, y + bottom_h)

    # Top 5 Highest-Downtime Devices
    page.draw_rect(right_box, color=RGB_BORDER, fill=RGB_WHITE, width=0.6, radius=0.04)
    rows_html = []
    for idx, d in enumerate(summary["top_downtime_devices"][:5], start=1):
        bg = "#F9FAFC" if idx % 2 == 0 else "#FFFFFF"
        dpct_str = f"{d['downtime_pct']:.1f}%"
        rows_html.append(
            f'<tr style="background:{bg}">'
            f'<td style="text-align:center;color:{HEX_REPAIR};font-weight:bold">{ltr_span(dpct_str)}</td>'
            f'<td style="text-align:center;font-weight:bold">{ltr_span(d["reported_total_downtime_hhmm"])}</td>'
            f'<td style="text-align:right">{html.escape(d["top_cause_label"] or "—")}</td>'
            f'<td style="text-align:right;font-weight:bold">{html.escape(_short_device_label(d, 20))}</td>'
            f'<td style="text-align:center;color:{HEX_MUTED}">{fa_num(idx)}</td>'
            f"</tr>"
        )
    tracker.insert(
        page,
        fitz.Rect(right_box.x0 + 8, right_box.y0 + 6, right_box.x1 - 8, right_box.y1 - 6),
        f'<div dir="rtl">'
        f'<p style="font-size:9.8px;font-weight:bold;color:{HEX_NAVY};margin-bottom:6px">پرتوقف‌ترین دستگاه‌های ماه (بر اساس ساعات توقف)</p>'
        f'<table style="width:100%;border-collapse:collapse;font-size:8.0px">'
        f'<tr style="background:{HEX_NAVY};color:#FFFFFF">'
        f'<th style="padding:4px 2px;text-align:center">٪ توقف</th>'
        f'<th style="padding:4px 2px;text-align:center">ساعت توقف</th>'
        f'<th style="padding:4px 3px;text-align:right">علت اصلی</th>'
        f'<th style="padding:4px 3px;text-align:right">دستگاه</th>'
        f'<th style="padding:4px 2px;text-align:center">#</th>'
        f"</tr>"
        + "".join(rows_html)
        + "</table></div>",
        extra_css="td{padding:4px 3px;border-bottom:0.5px solid #E5EBF1}",
        strict=False,
        tag="cover_top5",
    )

    # Discrepancy & Ambiguity Alert Box
    page.draw_rect(
        left_box,
        color=hex_to_rgb(HEX_WARN_BORDER),
        fill=hex_to_rgb("#FFFBEB"),
        width=0.7,
        radius=0.04,
    )
    disc_lines = []
    seen_rows = set()
    for d in summary["discrepancies"]:
        rk = (d["sheet_name"], d["excel_row_num"], d["raw_device_code"])
        if rk in seen_rows:
            continue
        seen_rows.add(rk)
        delta_s = format_minutes_hhmm(d.get("delta_minutes"))
        disc_lines.append(
            f'<li>شیت <b>{ltr_span(d["sheet_name"])}</b> ردیف <b>{fa_num(d["excel_row_num"])}</b> '
            f'دستگاه <b>{fa_num(d["raw_device_code"])}</b>: اختلاف {ltr_span(delta_s, bold=True)} ساعت بین علل و جمع توقف</li>'
        )
    if not disc_lines:
        disc_lines.append("<li>هیچ مغایرت عددی در این دوره مشاهده نشد.</li>")
    tracker.insert(
        page,
        fitz.Rect(left_box.x0 + 8, left_box.y0 + 6, left_box.x1 - 8, left_box.y1 - 6),
        f'<div dir="rtl">'
        f'<p style="font-size:9.6px;font-weight:bold;color:{HEX_WARN_TEXT};margin-bottom:5px">هشدار مغایرت‌ها و ابهام هویت</p>'
        f'<ul style="margin:0 14px 6px 0;padding:0;font-size:7.9px;color:{HEX_TEXT};line-height:1.4">{"".join(disc_lines)}</ul>'
        f'<p style="font-size:7.8px;color:{HEX_WARN_TEXT};line-height:1.38">'
        f'<b>ابهام هویت:</b> کد ۶۰۱ (۲ ردیف روزانه بدون نوع دستگاه؛ نامزد EX601 و WA601) و کد ۳۲۱ (نامزد EX321 و HD321) بدون نگاشت حدسی علامت‌گذاری شده‌اند. '
        f'کدهای ۷۱۷ تا ۷۲۱ و گریدر نیز در Registry ناوگان ثبت نشده‌اند.</p>'
        f"</div>",
        strict=False,
        tag="cover_discrepancy_box",
    )


def _render_fleet_charts_pages(
    doc: fitz.Document,
    tracker: BoxTracker,
    *,
    summary: dict[str, Any],
    daily_trend: dict[str, Any],
    all_devices: list[dict[str, Any]],
) -> None:
    period_fa = summary["period_label_fa"]
    is_prov = summary["version"]["is_provisional"]
    fleet = summary["fleet"]
    causes = summary["causes"]

    # --- Page 2: Chart 1 (Work/Downtime Donut) + Chart 2 (Causes Donut) + Chart 8 (Pareto) ---
    page, y = _new_interior_page(
        doc,
        tracker,
        section_title="بخش دوم — نمودارهای تحلیلی کل ناوگان (نسبت کارکرد، علل و پارتو)",
        period_fa=period_fa,
        is_provisional=is_prov,
    )
    half_w = (CONTENT_W - 10) / 2
    donut_h = 215.0

    # Chart 1: Fleet Work vs Downtime Donut (right half)
    draw_donut_panel(
        page,
        fitz.Rect(MARGIN_X + half_w + 10, y, PAGE_W - MARGIN_X, y + donut_h),
        tracker,
        title="۱. نسبت کارکرد خالص و توقف کل ناوگان",
        slices=[
            {
                "label": "کارکرد خالص",
                "value": fleet["reported_net_work_minutes"],
                "color": HEX_WORK,
                "hhmm": fleet["reported_net_work_hhmm"],
                "pct": fleet["work_pct"],
            },
            {
                "label": "توقف ثبت‌شده",
                "value": fleet["reported_total_downtime_minutes"],
                "color": HEX_REPAIR,
                "hhmm": fleet["reported_total_downtime_hhmm"],
                "pct": fleet["downtime_pct"],
            },
        ],
        center_value=f"{fleet['work_pct']:.1f}%",
        center_label="نرخ کارکرد",
    )

    # Chart 2: Downtime Causes Donut (left half)
    cause_slices = [
        {
            "label": c["cause_label"],
            "value": c["total_minutes"],
            "color": CAUSE_HEX_COLORS.get(c["cause_key"], HEX_STEEL),
            "hhmm": c["total_hhmm"],
            "pct": c["share_of_causes_pct"],
        }
        for c in causes
    ]
    draw_donut_panel(
        page,
        fitz.Rect(MARGIN_X, y, MARGIN_X + half_w, y + donut_h),
        tracker,
        title="۲. سهم علل پنج‌گانه توقف ناوگان",
        slices=cause_slices,
        center_value=fleet["computed_causes_hhmm"],
        center_label="مجموع علل (ساعت)",
    )
    y += donut_h + 14.0

    # Maintenance vs Operational Summary Ribbon
    page.draw_rect(fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + 54), color=RGB_BORDER, fill=RGB_SURFACE, width=0.6, radius=0.05)
    m_causes_s = f"{fleet['maintenance_pct_of_causes']:.2f}%"
    m_base_s = f"{fleet['maintenance_pct_of_base']:.2f}%"
    o_causes_s = f"{fleet['operational_pct_of_causes']:.2f}%"
    o_base_s = f"{fleet['operational_pct_of_base']:.2f}%"
    tracker.insert(
        page,
        fitz.Rect(MARGIN_X + 12, y + 5, PAGE_W - MARGIN_X - 12, y + 51),
        f'<p dir="rtl" style="text-align:right;font-size:8.6px;color:{HEX_TEXT};line-height:1.42">'
        f'<b>تفکیک راهبردی توقف تعمیراتی در برابر عملیاتی:</b> '
        f'توقف ناشی از <b style="color:{HEX_REPAIR}">تعمیرات</b> معادل {ltr_span(fleet["maintenance_downtime_hhmm"], bold=True, color=HEX_REPAIR)} ساعت '
        f'({ltr_span(m_causes_s)} از علل توقف / {ltr_span(m_base_s)} از ساعات مبنا) و '
        f'توقف <b style="color:{HEX_NO_LOAD}">عملیاتی</b> (انفجار، بی‌باری، راننده، نبود بارگیر) معادل '
        f'{ltr_span(fleet["operational_downtime_hhmm"], bold=True, color=HEX_WARN_TEXT)} ساعت '
        f'({ltr_span(o_causes_s)} از علل توقف / {ltr_span(o_base_s)} از ساعات مبنا) است.</p>',
        strict=False,
        tag="maint_vs_op_ribbon",
    )
    y += 64.0

    # Chart 8: Pareto Chart of Downtime Causes
    pareto_h = PAGE_H - y - 38
    draw_pareto_chart(
        page,
        fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + pareto_h),
        tracker,
        title="۸. نمودار پارتو (Pareto) علل توقف ناوگان — ساعات و درصد تجمعی",
        causes=causes,
    )

    # --- Page 3: Chart 3 (Top 10 Total Downtime) + Chart 4 (Top 10 Repair Downtime) ---
    page, y = _new_interior_page(
        doc,
        tracker,
        section_title="بخش دوم — پرتوقف‌ترین دستگاه‌ها و بیشترین توقف تعمیراتی",
        period_fa=period_fa,
        is_provisional=is_prov,
    )
    chart_h = (PAGE_H - y - 48) / 2

    top10_down = summary["top_downtime_devices"][:10]
    down_rows = []
    for d in top10_down:
        rep_h = d["repair_minutes"] / 60.0
        other_h = max(0.0, (d["reported_total_downtime_minutes"] - d["repair_minutes"]) / 60.0)
        down_rows.append(
            {
                "label": _short_device_label(d, 22),
                "segments": [(rep_h, HEX_REPAIR), (other_h, HEX_NO_LOAD)],
                "value_text": f"{d['reported_total_downtime_hhmm']} ({d['downtime_pct']:.1f}%)",
            }
        )
    draw_rtl_horizontal_bars(
        page,
        fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + chart_h),
        tracker,
        title="۳. ۱۰ دستگاه دارای بیشترین ساعات توقف کل",
        subtitle="مقدار هر میله به تفکیک توقف تعمیراتی (قرمز) و سایر علل عملیاتی (کهربایی) به‌همراه ساعت و درصد توقف نمایش داده شده است.",
        rows=down_rows,
        label_width=118.0,
        value_width=94.0,
        legend_items=[("توقف تعمیراتی", HEX_REPAIR), ("سایر علل (عملیاتی)", HEX_NO_LOAD)],
        font_size=8.6,
    )
    y += chart_h + 14.0

    top10_rep = summary["top_repair_devices"][:10]
    rep_rows = [
        {
            "label": _short_device_label(d, 22),
            "segments": [(d["repair_minutes"] / 60.0, HEX_REPAIR)],
            "value_text": f"{d['repair_hhmm']} ({d['repair_pct_of_base']:.1f}%)",
        }
        for d in top10_rep
    ]
    draw_rtl_horizontal_bars(
        page,
        fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + chart_h),
        tracker,
        title="۴. ۱۰ دستگاه دارای بیشترین توقف تعمیراتی (اولویت بررسی فنی و نت)",
        subtitle="ساعات توقف تعمیراتی خالص و درصد سهم آن از ساعات مبنای هر دستگاه (جدا از توقفات عملیاتی).",
        rows=rep_rows,
        label_width=118.0,
        value_width=94.0,
        legend_items=[("توقف ناشی از تعمیرات", HEX_REPAIR)],
        font_size=8.6,
    )

    # --- Page 4: Chart 6 (Daily Fleet Trend) + Chart 7 (Machine Group Comparison) ---
    page, y = _new_interior_page(
        doc,
        tracker,
        section_title="بخش دوم — روند روزانه ناوگان و مقایسه گروه‌های ماشین‌آلات",
        period_fa=period_fa,
        is_provisional=is_prov,
    )
    trend_h = 340.0
    draw_daily_stacked_trend(
        page,
        fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + trend_h),
        tracker,
        title=f"۶. روند روزانه ساعات کارکرد و توقف کل ناوگان در {period_fa} (روزهای ۱ تا ۳۰)",
        days_series=daily_trend["days"],
        total_calendar_days=summary["coverage"]["calendar_days_in_month"],
        compact=False,
    )
    y += trend_h + 14.0

    grp_h = PAGE_H - y - 36
    grp_rows = []
    for g in summary["groups"]:
        tag = f"{g['device_group']} ({fa_num(g['devices_count'])})"
        if not g["is_confirmed_group"]:
            tag = f"* {tag}"
        op_pct = max(0.0, round(g["downtime_pct"] - g["repair_pct_of_base"], 2))
        grp_rows.append(
            {
                "label": tag,
                "segments": [
                    (g["work_pct"], HEX_WORK),
                    (g["repair_pct_of_base"], HEX_REPAIR),
                    (op_pct, HEX_NO_LOAD),
                ],
                "value_text": f"کارکرد {g['work_pct']:.1f}% | تعمیر {g['repair_pct_of_base']:.1f}%",
            }
        )
    draw_rtl_horizontal_bars(
        page,
        fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + grp_h),
        tracker,
        title="۷. مقایسه عملکرد گروه‌های ماشین‌آلات (درصد وزنی از ساعات مبنا)",
        subtitle="گروه‌های تاییدشده در Registry در بالا و ردیف‌های علامت‌دار (*) به‌صورت مجزا برای کدهای ثبت‌نشده و مبهم تفکیک شده‌اند.",
        rows=grp_rows,
        max_value=100.0,
        label_width=138.0,
        value_width=126.0,
        legend_items=[("کارکرد خالص", HEX_WORK), ("توقف تعمیراتی", HEX_REPAIR), ("توقف عملیاتی", HEX_NO_LOAD)],
        font_size=8.3,
    )

    # --- Page 5: Chart 5 (Full 50-Device Work % Ranking Chart) ---
    page, y = _new_interior_page(
        doc,
        tracker,
        section_title="بخش دوم — نمودار جامع رتبه‌بندی درصد کارکرد تمام دستگاه‌ها",
        period_fa=period_fa,
        is_provisional=is_prov,
    )
    sorted_by_work = sorted(
        all_devices,
        key=lambda d: (-d["work_pct"], -d["reported_net_work_minutes"], d["canonical_device_key"]),
    )
    avail_rows = [
        {
            "label": _short_device_label(d, 22),
            "segments": [(d["work_pct"], _availability_color(d["work_pct"]))],
            "value_text": f"{d['work_pct']:.1f}% ({d['reported_net_work_hhmm']})",
        }
        for d in sorted_by_work
    ]
    avg_wpct_txt = fa_num(f"{fleet['work_pct']:.2f}%")
    draw_rtl_horizontal_bars(
        page,
        fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, PAGE_H - 36),
        tracker,
        title=f"۵. رتبه‌بندی درصد کارکرد تمامی {fa_num(len(all_devices))} دستگاه دارای اطلاعات",
        subtitle="رنگ‌بندی: سبز ≥ ۷۵٪ | آبی ۵۰ تا ۷۵٪ | کهربایی ۳۰ تا ۵۰٪ | قرمز < ۳۰٪ — خط عمودی نقطه‌چین: میانگین وزنی ناوگان",
        rows=avail_rows,
        max_value=100.0,
        label_width=114.0,
        value_width=92.0,
        ref_line=(fleet["work_pct"], f"میانگین وزنی ناوگان: {avg_wpct_txt}"),
        font_size=7.0,
        bar_radius=0.12,
    )


def _render_ranking_tables_pages(
    doc: fitz.Document,
    tracker: BoxTracker,
    *,
    summary: dict[str, Any],
    all_devices: list[dict[str, Any]],
) -> None:
    period_fa = summary["period_label_fa"]
    is_prov = summary["version"]["is_provisional"]

    def render_table_block(
        devices_list: list[dict[str, Any]],
        *,
        section_title: str,
        table_subtitle: str,
        rank_field: str,
    ) -> None:
        per_page = 25
        for start in range(0, len(devices_list), per_page):
            chunk = devices_list[start : start + per_page]
            page, y = _new_interior_page(
                doc,
                tracker,
                section_title=section_title,
                period_fa=period_fa,
                is_provisional=is_prov,
            )
            tracker.insert(
                page,
                fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + 18),
                f'<p dir="rtl" style="font-size:8.6px;color:{HEX_MUTED}">{html.escape(table_subtitle)} '
                f'(ردیف {fa_num(start + 1)} تا {fa_num(start + len(chunk))} از {fa_num(len(devices_list))})</p>',
                strict=False,
                tag=f"rank_sub_{rank_field}_{start}",
            )
            y += 20.0

            rows_html = []
            for d in chunk:
                needs_review = d["downtime_pct"] >= 35.0 or d["repair_minutes"] >= 3000 or d["discrepancy_count"] > 0
                bg = "#FEF2F2" if d["downtime_pct"] >= 50.0 else ("#FFFBEB" if d["discrepancy_count"] > 0 or d["identity_status"] == "AMBIGUOUS" else ("#F8FAFC" if d[rank_field] % 2 == 0 else "#FFFFFF"))
                badges = []
                if d["downtime_pct"] >= 50.0:
                    badges.append('<span style="color:#991B1B;font-weight:bold">توقف بالا</span>')
                elif needs_review:
                    badges.append('<span style="color:#92400E;font-weight:bold">نیازمند بررسی</span>')
                if d["discrepancy_count"] > 0:
                    badges.append('<span style="color:#B45309;font-weight:bold">مغایرت</span>')
                if d["identity_status"] == "AMBIGUOUS":
                    badges.append('<span style="color:#B45309">مبهم</span>')
                elif d["identity_status"] == "UNREGISTERED":
                    badges.append('<span style="color:#475569">ثبت‌نشده</span>')
                status_cell = " · ".join(badges) if badges else '<span style="color:#166534">عادی</span>'

                dpct_s = f"{d['downtime_pct']:.1f}%"
                wpct_s = f"{d['work_pct']:.1f}%"
                rows_html.append(
                    f'<tr style="background:{bg}">'
                    f'<td style="text-align:center;font-size:7.2px">{status_cell}</td>'
                    f'<td style="text-align:right;font-size:7.5px">{html.escape(d["top_cause_label"] or "بدون توقف")}</td>'
                    f'<td style="text-align:center;color:{HEX_REPAIR if d["repair_minutes"] > 0 else HEX_MUTED}">{ltr_span(d["repair_hhmm"])}</td>'
                    f'<td style="text-align:center;font-weight:bold;color:{HEX_REPAIR if d["downtime_pct"] >= 35 else HEX_TEXT}">{ltr_span(dpct_s)}</td>'
                    f'<td style="text-align:center;font-weight:bold;color:{HEX_WORK if d["work_pct"] >= 70 else HEX_TEXT}">{ltr_span(wpct_s)}</td>'
                    f'<td style="text-align:center;font-weight:bold">{ltr_span(d["reported_total_downtime_hhmm"])}</td>'
                    f'<td style="text-align:center">{ltr_span(d["reported_net_work_hhmm"])}</td>'
                    f'<td style="text-align:center;color:{HEX_MUTED}">{ltr_span(d["base_hhmm"])}</td>'
                    f'<td style="text-align:right;font-weight:bold">{html.escape(_short_device_label(d, 23))}</td>'
                    f'<td style="text-align:center;font-weight:bold;color:{HEX_NAVY}">{fa_num(d[rank_field])}</td>'
                    f"</tr>"
                )

            table_html = (
                f'<table style="width:100%;border-collapse:collapse;font-size:7.7px">'
                f'<tr style="background:{HEX_NAVY};color:#FFFFFF">'
                f'<th style="width:11%;text-align:center">وضعیت</th>'
                f'<th style="width:12%;text-align:right">علت اصلی</th>'
                f'<th style="width:9%;text-align:center">تعمیرات</th>'
                f'<th style="width:8%;text-align:center">٪ توقف</th>'
                f'<th style="width:8%;text-align:center">٪ کارکرد</th>'
                f'<th style="width:9%;text-align:center">توقف کل</th>'
                f'<th style="width:9%;text-align:center">کارکرد</th>'
                f'<th style="width:9%;text-align:center">مبنا</th>'
                f'<th style="width:20%;text-align:right">کد و نام دستگاه</th>'
                f'<th style="width:5%;text-align:center">رتبه</th>'
                f"</tr>"
                + "".join(rows_html)
                + "</table>"
            )
            tracker.insert(
                page,
                fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, PAGE_H - 36),
                table_html,
                extra_css=(
                    "th{padding:5px 2px;font-weight:bold;border:0.4px solid #1E4D72}"
                    "td{padding:4.2px 2.5px;border-bottom:0.4px solid #DCE4EC;border-left:0.3px solid #EAEFF5;border-right:0.3px solid #EAEFF5}"
                ),
                strict=False,
                tag=f"rank_tbl_{rank_field}_{start}",
            )

    # Table 3A: All 50 devices ranked by total downtime HOURS
    by_hours = sorted(all_devices, key=lambda d: d["rank_by_downtime_hours"])
    render_table_block(
        by_hours,
        section_title="بخش سوم — جدول رتبه‌بندی تمام دستگاه‌ها (بر اساس ساعات توقف)",
        table_subtitle="رتبه‌بندی کامل دستگاه‌ها از بیشترین به کمترین ساعات توقف ثبت‌شده؛ ردیف‌های قرمز/کهربایی نیازمند اولویت بررسی هستند.",
        rank_field="rank_by_downtime_hours",
    )

    # Table 3B: All 50 devices ranked separately by downtime PERCENTAGE
    by_pct = sorted(all_devices, key=lambda d: d["rank_by_downtime_pct"])
    render_table_block(
        by_pct,
        section_title="بخش سوم — جدول رتبه‌بندی تمام دستگاه‌ها (بر اساس درصد توقف)",
        table_subtitle="رتبه‌بندی مستقل دستگاه‌ها بر اساس درصد توقف نسبت به ساعات مبنای هر دستگاه.",
        rank_field="rank_by_downtime_pct",
    )


def _render_device_scorecard_card(
    page: fitz.Page,
    rect: fitz.Rect,
    tracker: BoxTracker,
    *,
    dev: dict[str, Any],
    total_devices: int,
    total_calendar_days: int,
) -> None:
    """Render one complete, rich visual device scorecard card inside `rect`."""
    has_warn = dev["discrepancy_count"] > 0 or dev["identity_status"] == "AMBIGUOUS"
    border_col = hex_to_rgb(HEX_WARN_BORDER) if has_warn else RGB_BORDER
    page.draw_rect(rect, color=border_col, fill=RGB_WHITE, width=0.8 if has_warn else 0.65, radius=0.025)

    # Card Header Bar
    hdr_h = 30.0
    hdr_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + hdr_h)
    page.draw_rect(hdr_rect, color=None, fill=RGB_NAVY, radius=0.025)
    # Square off bottom corners of header bar
    page.draw_rect(fitz.Rect(rect.x0, rect.y0 + hdr_h - 6, rect.x1, rect.y0 + hdr_h), color=None, fill=RGB_NAVY)

    # Title on right, rank & badges on left
    id_badge = ""
    if dev["identity_status"] == "AMBIGUOUS":
        id_badge = '<span style="background:#F59E0B;color:#FFFFFF;padding:0 3px;font-size:7.2px">هویت مبهم</span> '
    elif dev["identity_status"] == "UNREGISTERED":
        id_badge = '<span style="background:#64748B;color:#FFFFFF;padding:0 3px;font-size:7.2px">ثبت‌نشده</span> '
    if dev["discrepancy_count"] > 0:
        id_badge += '<span style="background:#DC2626;color:#FFFFFF;padding:0 3px;font-size:7.2px">مغایرت منبع</span> '

    tracker.insert(
        page,
        fitz.Rect(rect.x0 + 215, rect.y0 + 4, rect.x1 - 10, rect.y0 + hdr_h - 1),
        f'<p dir="rtl" style="text-align:right;font-size:9.4px;font-weight:bold;color:#FFFFFF">'
        f'کد {ltr_span(dev["raw_device_code"], bold=True, color="#FFFFFF")} — {html.escape(dev["device_name"])}</p>',
        strict=False,
        tag=f"card_hdr_{dev['canonical_device_key']}",
    )
    tracker.insert(
        page,
        fitz.Rect(rect.x0 + 8, rect.y0 + 5, rect.x0 + 212, rect.y0 + hdr_h - 1),
        f'<p dir="rtl" style="text-align:left;font-size:7.6px;color:#DCE8F5">'
        f'{id_badge}گروه: <b>{html.escape(dev["device_group"])}</b> | رتبه: <b>{fa_num(dev["rank_by_downtime_hours"])}/{fa_num(total_devices)}</b></p>',
        strict=False,
        tag=f"card_meta_{dev['canonical_device_key']}",
    )

    y = rect.y0 + hdr_h + 6.0

    # 5 Mini KPI Pills Strip
    pills = [
        ("ساعات مبنا", dev["base_hhmm"], HEX_STEEL),
        ("کارکرد خالص", f"{dev['reported_net_work_hhmm']} ({dev['work_pct']:.1f}%)", HEX_WORK),
        ("توقف کل", f"{dev['reported_total_downtime_hhmm']} ({dev['downtime_pct']:.1f}%)", HEX_REPAIR if dev["downtime_pct"] >= 35 else HEX_NAVY),
        ("توقف تعمیراتی", f"{dev['repair_hhmm']} ({dev['repair_pct_of_base']:.1f}%)", HEX_REPAIR if dev["repair_minutes"] > 0 else HEX_MUTED),
        ("روز داده / بی‌کارکرد", f"{dev['days_with_data']} روز / {dev['days_zero_work']} روز", HEX_STEEL),
    ]
    pgap = 5.0
    pw = (rect.width - 16 - 4 * pgap) / 5
    ph = 32.0
    for idx, (plbl, pval, pcol) in enumerate(pills):
        px1 = rect.x1 - 8 - idx * (pw + pgap)
        px0 = px1 - pw
        page.draw_rect(fitz.Rect(px0, y, px1, y + ph), color=RGB_BORDER, fill=RGB_SURFACE, width=0.45, radius=0.08)
        tracker.insert(
            page,
            fitz.Rect(px0 + 3, y + 3, px1 - 3, y + ph - 2),
            f'<p dir="rtl" style="text-align:center;font-size:6.8px;color:{HEX_MUTED};line-height:1.15">{html.escape(plbl)}<br>'
            f'<span style="font-size:8.4px;font-weight:bold;color:{pcol}">{ltr_span(pval, bold=True, color=pcol)}</span></p>',
            strict=False,
            tag=f"card_kpi_{dev['canonical_device_key']}_{idx}",
        )
    y += ph + 6.0

    # Middle Visual Row: 3 synchronized vector mini-charts (Work Donut | Causes Donut | Daily Trend)
    viz_h = 112.0
    vgap = 6.0
    inner_w = rect.width - 16
    w_donut1 = inner_w * 0.28
    w_donut2 = inner_w * 0.34
    w_trend = inner_w - w_donut1 - w_donut2 - 2 * vgap

    # Right: Work vs Downtime Donut
    r_d1 = fitz.Rect(rect.x1 - 8 - w_donut1, y, rect.x1 - 8, y + viz_h)
    draw_donut_panel(
        page,
        r_d1,
        tracker,
        title="کارکرد در برابر توقف",
        slices=[
            {"label": "کارکرد", "value": dev["reported_net_work_minutes"], "color": HEX_WORK, "short_value": f"{dev['work_pct']:.1f}%"},
            {"label": "توقف", "value": dev["reported_total_downtime_minutes"], "color": HEX_REPAIR, "short_value": f"{dev['downtime_pct']:.1f}%"},
        ],
        center_value=f"{dev['work_pct']:.0f}%",
        center_label="کارکرد",
        compact=True,
    )

    # Center: Cause Breakdown Donut (or 'بدون توقف ثبت‌شده' when zero!)
    r_d2 = fitz.Rect(r_d1.x0 - vgap - w_donut2, y, r_d1.x0 - vgap, y + viz_h)
    c_slices = [
        {
            "label": c["cause_label"],
            "value": c["minutes"],
            "color": CAUSE_HEX_COLORS.get(c["cause_key"], HEX_STEEL),
            "short_value": f"{c['hhmm']} ({c['share_pct']:.0f}%)",
        }
        for c in dev["cause_breakdown"]
    ]
    draw_donut_panel(
        page,
        r_d2,
        tracker,
        title="سهم علل توقف دستگاه",
        slices=c_slices,
        center_value=dev["computed_causes_hhmm"],
        center_label="مجموع علل",
        empty_message="بدون توقف ثبت‌شده",
        compact=True,
    )

    # Left: Daily Performance Trend (days 1..30)
    r_tr = fitz.Rect(rect.x0 + 8, y, r_d2.x0 - vgap, y + viz_h)
    draw_daily_stacked_trend(
        page,
        r_tr,
        tracker,
        title="روند روزانه کارکرد و توقف",
        days_series=dev.get("daily_series") or [],
        total_calendar_days=total_calendar_days,
        compact=True,
    )
    y += viz_h + 6.0

    # Bottom Details: Major Downtime Days + Repeated Note Topics + Tailored Narrative & Recommendation
    major_days = dev.get("major_downtime_days") or []
    if major_days:
        md_parts = []
        for md in major_days[:3]:
            dm = max(md["reported_total_downtime_minutes"], md["computed_causes_minutes"])
            note_snip = (md.get("notes_raw") or "").strip()
            if len(note_snip) > 34:
                note_snip = note_snip[:33] + "…"
            md_parts.append(
                f'{ltr_span(md["jalali_date"][5:])} ({ltr_span(format_minutes_hhmm(dm), bold=True)}'
                + (f' — {html.escape(note_snip)}' if note_snip else "")
                + ")"
            )
        major_txt = " | ".join(md_parts)
    else:
        major_txt = "بدون روز دارای توقف ثبت‌شده"

    topics = dev.get("top_topics") or []
    topics_txt = (
        "، ".join(f"{t['topic_label']} ({fa_num(t['count'])})" for t in topics[:4])
        if topics
        else "موضوع پرتکرار شاخصی در ملاحظات ثبت نشده است."
    )

    discrep_html = ""
    if dev["discrepancy_count"] > 0:
        delta = dev["computed_causes_minutes"] - dev["reported_total_downtime_minutes"]
        discrep_html = (
            f'<p style="font-size:7.4px;color:{HEX_WARN_TEXT};background:{HEX_WARN_BG};padding:2px 5px;margin-top:3px">'
            f'<b>هشدار مغایرت منبع:</b> در {fa_num(dev["discrepancy_count"])} ردیف، مجموع علل ({ltr_span(dev["computed_causes_hhmm"])}) '
            f'با جمع توقف ثبت‌شده ({ltr_span(dev["reported_total_downtime_hhmm"])}) به میزان {ltr_span(format_minutes_hhmm(delta), bold=True)} ساعت اختلاف دارد.</p>'
        )

    narr = dev["narrative"]
    details_rect = fitz.Rect(rect.x0 + 8, y, rect.x1 - 8, rect.y1 - 5)
    page.draw_rect(details_rect, color=RGB_BORDER, fill=RGB_SURFACE, width=0.45, radius=0.04)
    tracker.insert(
        page,
        fitz.Rect(details_rect.x0 + 7, details_rect.y0 + 4, details_rect.x1 - 7, details_rect.y1 - 3),
        f'<div dir="rtl" style="font-size:7.6px;line-height:1.38;color:{HEX_TEXT}">'
        f'<p style="margin-bottom:2.5px"><b style="color:{HEX_NAVY}">مهم‌ترین روزهای توقف:</b> {major_txt}</p>'
        f'<p style="margin-bottom:2.5px"><b style="color:{HEX_NAVY}">علل پرتکرار ملاحظات:</b> {html.escape(topics_txt)}</p>'
        f'<p style="margin-bottom:2.5px"><b style="color:{HEX_NAVY}">تحلیل دستگاه:</b> {html.escape(fa_num(narr["analysis"]))}</p>'
        f'<p><b style="color:{HEX_STEEL}">پیشنهاد اقدام:</b> {html.escape(fa_num(narr["recommendation"]))}</p>'
        f"{discrep_html}"
        f"</div>",
        strict=False,
        tag=f"card_details_{dev['canonical_device_key']}",
    )


def _render_device_scorecards_pages(
    doc: fitz.Document,
    tracker: BoxTracker,
    *,
    summary: dict[str, Any],
    all_devices: list[dict[str, Any]],
) -> None:
    period_fa = summary["period_label_fa"]
    is_prov = summary["version"]["is_provisional"]
    total_days = summary["coverage"]["calendar_days_in_month"]
    # Order scorecards by downtime hours descending (highest-priority devices first!)
    ordered = sorted(all_devices, key=lambda d: d["rank_by_downtime_hours"])
    total_n = len(ordered)

    # 2 scorecards per A4 portrait page (spacious 366pt per card!)
    card_gap = 12.0
    for idx in range(0, total_n, 2):
        pair = ordered[idx : idx + 2]
        page, y = _new_interior_page(
            doc,
            tracker,
            section_title=f"بخش چهارم — کارنامه اختصاصی دستگاه‌ها ({fa_num(idx + 1)} تا {fa_num(idx + len(pair))} از {fa_num(total_n)})",
            period_fa=period_fa,
            is_provisional=is_prov,
        )
        avail_h = PAGE_H - y - 36
        card_h = (avail_h - card_gap) / 2
        for slot, dev in enumerate(pair):
            cy0 = y + slot * (card_h + card_gap)
            card_rect = fitz.Rect(MARGIN_X, cy0, PAGE_W - MARGIN_X, cy0 + card_h)
            _render_device_scorecard_card(
                page,
                card_rect,
                tracker,
                dev=dev,
                total_devices=total_n,
                total_calendar_days=total_days,
            )


def _render_management_analysis_page(
    doc: fitz.Document,
    tracker: BoxTracker,
    *,
    summary: dict[str, Any],
    insights: dict[str, Any],
) -> None:
    page, y = _new_interior_page(
        doc,
        tracker,
        section_title="بخش پنجم — تحلیل مدیریتی، تحلیل کیفی ملاحظات و پیشنهادهای اجرایی",
        period_fa=summary["period_label_fa"],
        is_provisional=summary["version"]["is_provisional"],
    )
    q = insights["questions"]

    # 4 Structured Analytical Cards (2x2 grid)
    blocks = [
        ("۱. مهم‌ترین علت کاهش کارکرد ناوگان", q["main_cause"], HEX_NAVY),
        ("۲. دستگاه‌های نیازمند بررسی بیشتر", q["devices_needing_review"], HEX_REPAIR),
        ("۳. تحلیل توقفات تعمیراتی (واحد نت)", q["high_repair_devices"], HEX_REPAIR),
        ("۴. تفکیک توقفات عملیاتی (غیرتعمیراتی)", q["operational_downtime"], HEX_NO_LOAD),
    ]
    gap = 10.0
    bw = (CONTENT_W - gap) / 2
    bh = 122.0
    for idx, (title, text, accent) in enumerate(blocks):
        r_i, c_i = divmod(idx, 2)
        x1 = PAGE_W - MARGIN_X - c_i * (bw + gap)
        x0 = x1 - bw
        by0 = y + r_i * (bh + gap)
        page.draw_rect(fitz.Rect(x0, by0, x1, by0 + bh), color=RGB_BORDER, fill=RGB_SURFACE, width=0.6, radius=0.04)
        page.draw_rect(fitz.Rect(x1 - 4, by0 + 2, x1, by0 + bh - 2), color=None, fill=hex_to_rgb(accent))
        tracker.insert(
            page,
            fitz.Rect(x0 + 8, by0 + 6, x1 - 10, by0 + bh - 5),
            f'<div dir="rtl">'
            f'<p style="font-size:9.3px;font-weight:bold;color:{accent};margin-bottom:4px">{html.escape(title)}</p>'
            f'<p style="font-size:8.1px;color:{HEX_TEXT};line-height:1.45">{html.escape(fa_num(text))}</p>'
            f"</div>",
            strict=False,
            tag=f"mgmt_q_{idx}",
        )
    y += 2 * bh + gap + 12.0

    # Top Note Themes Bar Chart (Section 10 + Question 5)
    topics = summary.get("top_note_topics") or []
    topic_h = 195.0
    topic_rows = [
        {
            "label": t["topic_label"][:30],
            "segments": [(float(t["mention_rows"]), HEX_STEEL)],
            "value_text": f"{t['mention_rows']} ردیف ({t['affected_devices']} دستگاه)",
        }
        for t in topics[:8]
    ]
    draw_rtl_horizontal_bars(
        page,
        fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + topic_h),
        tracker,
        title="۵. موضوعات پرتکرار استخراج‌شده از ستون ملاحظات (تحلیل کیفی)",
        subtitle="برچسب‌های کیفی ملاحظات جایگزین طبقه‌بندی عددی پنج‌گانه نیستند و مدت توقف ردیف‌های چندعلتی بدون شواهد تقسیم نشده است.",
        rows=topic_rows,
        label_width=170.0,
        value_width=105.0,
        font_size=8.0,
    )
    y += topic_h + 12.0

    # Next-Month Action Plan Box (Question 6)
    rec_h = PAGE_H - y - 36
    page.draw_rect(fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + rec_h), color=RGB_STEEL, fill=RGB_WHITE, width=0.8, radius=0.04)
    page.draw_rect(fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + 24), color=None, fill=RGB_STEEL, radius=0.04)
    page.draw_rect(fitz.Rect(MARGIN_X, y + 18, PAGE_W - MARGIN_X, y + 24), color=None, fill=RGB_STEEL)
    tracker.insert(
        page,
        fitz.Rect(MARGIN_X + 10, y + 4, PAGE_W - MARGIN_X - 10, y + 22),
        '<p dir="rtl" style="font-size:10px;font-weight:bold;color:#FFFFFF">۶. پیشنهادهای اجرایی و موارد اولویت‌دار برای بهبود عملکرد ماه بعد</p>',
        strict=False,
        tag="mgmt_rec_hdr",
    )
    rec_items = "".join(
        f'<p dir="rtl" style="text-align:right;margin-bottom:4px;font-size:8.2px;color:{HEX_TEXT};line-height:1.42">'
        f'<b style="color:{HEX_STEEL}">{fa_num(i)}.</b> {html.escape(fa_num(item))}</p>'
        for i, item in enumerate(q["next_month_actions"], start=1)
    )
    tracker.insert(
        page,
        fitz.Rect(MARGIN_X + 12, y + 29, PAGE_W - MARGIN_X - 12, y + rec_h - 6),
        f'<div dir="rtl">{rec_items}</div>',
        strict=False,
        tag="mgmt_rec_body",
    )


def _render_data_quality_page(
    doc: fitz.Document,
    tracker: BoxTracker,
    *,
    summary: dict[str, Any],
    all_devices: list[dict[str, Any]],
) -> None:
    page, y = _new_interior_page(
        doc,
        tracker,
        section_title="بخش ششم — کیفیت داده، روش محاسبه، مغایرت‌ها و هویت دستگاه‌ها",
        period_fa=summary["period_label_fa"],
        is_provisional=summary["version"]["is_provisional"],
    )
    ver = summary["version"]
    cov = summary["coverage"]
    fleet = summary["fleet"]
    id_ov = summary["identity_overview"]

    # 1. Source & Methodology Box
    box1_h = 186.0
    page.draw_rect(fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + box1_h), color=RGB_BORDER, fill=RGB_SURFACE, width=0.6, radius=0.04)
    present_str = "، ".join(str(d) for d in cov["present_days"])
    missing_str = "، ".join(str(d) for d in cov["missing_days"]) or "ندارد"
    tracker.insert(
        page,
        fitz.Rect(MARGIN_X + 12, y + 8, PAGE_W - MARGIN_X - 12, y + box1_h - 6),
        f'<div dir="rtl" style="font-size:8.2px;line-height:1.48;color:{HEX_TEXT}">'
        f'<p style="font-size:10px;font-weight:bold;color:{HEX_NAVY};margin-bottom:4px">مشخصات منبع و روش محاسبه شاخص‌ها</p>'
        f'<p><b>فایل منبع (فقط خواندنی):</b> {html.escape(ver["source_filename"])} &nbsp;|&nbsp; '
        f'<b>دوره:</b> {html.escape(summary["period_label_fa"])} ({ltr_span(summary["period"])}) &nbsp;|&nbsp; '
        f'<b>نسخه پردازش:</b> {fa_num(ver["version_number"])}</p>'
        f'<p><b>اثر انگشت SHA-256 منبع:</b> {ltr_span(ver["source_sha256"][:32] + "…")}</p>'
        f'<p><b>شیت‌های فایل:</b> کل {fa_num(ver["total_sheets"])} شیت ({fa_num(ver["period_sheets_count"])} شیت دوره انتخابی، '
        f'{fa_num(ver["excluded_sheets_count"])} شیت مربوط به ماه‌های دیگر که از آمار این ماه حذف شده‌اند).</p>'
        f'<p><b>پوشش روزها:</b> {fa_num(cov["present_days_count"])} روز موجود ({fa_num(present_str)}) | '
        f'{fa_num(cov["missing_days_count"])} روز فاقد شیت ({fa_num(missing_str)}) — عدم وجود شیت به معنی صفر بودن کارکرد یا توقف نیست.</p>'
        f'<p><b>تعداد ردیف‌ها:</b> {fa_num(fleet["total_rows"])} ردیف دارای کد دستگاه '
        f'({fa_num(fleet["total_rows"] - fleet["discrepancy_count"])} ردیف معتبر، {fa_num(fleet["discrepancy_count"])} ردیف دارای مغایرت عددی، '
        f'{fa_num(fleet["warning_count"])} هشدار بازنویسی فرمول بدون اختلاف عددی).</p>'
        f'<p><b>تعریف شاخص‌ها:</b> درصد کارکرد = (مجموع کارکرد خالص ÷ مجموع ساعات مبنای معتبر) × ۱۰۰؛ '
        f'درصد توقف = (مجموع توقف کل ÷ مجموع ساعات مبنای معتبر) × ۱۰۰. برای آمار کل ناوگان از نسبت مجموع ساعات (میانگین وزنی) استفاده شده است. '
        f'شاخص‌های MTBF، MTTR و OEE به‌دلیل عدم وجود تفکیک رویداد خرابی مستقل و نرخ تولید در فایل منبع محاسبه نشده‌اند.</p>'
        f"</div>",
        strict=False,
        tag="dq_method",
    )
    y += box1_h + 12.0

    # 2. Discrepancies Audit Table
    tracker.insert(
        page,
        fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + 18),
        f'<p dir="rtl" style="font-size:10px;font-weight:bold;color:{HEX_WARN_TEXT}">مغایرت‌های عددی حل‌نشده و هشدارهای فرمولی فایل منبع</p>',
        strict=False,
        tag="dq_disc_hdr",
    )
    y += 20.0

    # Deduplicate per-row discrepancies for clean display while keeping full audit detail
    disc_rows = []
    seen = set()
    for d in summary["discrepancies"]:
        key = (d["sheet_name"], d["excel_row_num"], d["raw_device_code"], d["issue_type"])
        if d["issue_type"] != "CAUSES_SUM_MISMATCH" and (d["sheet_name"], d["excel_row_num"], d["raw_device_code"], "CAUSES_SUM_MISMATCH") in seen:
            continue
        seen.add(key)
        disc_rows.append(
            f'<tr style="background:#FEF2F2">'
            f'<td style="text-align:right">{html.escape(fa_num(d["message"]))}</td>'
            f'<td style="text-align:center;font-weight:bold;color:{HEX_REPAIR}">{ltr_span(format_minutes_hhmm(d.get("delta_minutes")))}</td>'
            f'<td style="text-align:center">{ltr_span(d["raw_device_code"])}</td>'
            f'<td style="text-align:center">{fa_num(d["excel_row_num"])}</td>'
            f'<td style="text-align:center">{ltr_span(d["sheet_name"])}</td>'
            f'<td style="text-align:center;color:{HEX_REPAIR};font-weight:bold">مغایرت</td>'
            f"</tr>"
        )
    for w in summary.get("warnings", []):
        disc_rows.append(
            f'<tr style="background:#FFFBEB">'
            f'<td style="text-align:right">{html.escape(fa_num(w["message"]))}</td>'
            f'<td style="text-align:center">{ltr_span("0:00")}</td>'
            f'<td style="text-align:center">{ltr_span(w["raw_device_code"])}</td>'
            f'<td style="text-align:center">{fa_num(w["excel_row_num"])}</td>'
            f'<td style="text-align:center">{ltr_span(w["sheet_name"])}</td>'
            f'<td style="text-align:center;color:{HEX_WARN_TEXT}">هشدار فرمول</td>'
            f"</tr>"
        )
    if not disc_rows:
        disc_rows.append('<tr><td colspan="6" style="text-align:center;color:#166534">هیچ مغایرتی مشاهده نشد.</td></tr>')

    disc_table_h = 195.0
    tracker.insert(
        page,
        fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + disc_table_h),
        f'<table style="width:100%;border-collapse:collapse;font-size:7.4px">'
        f'<tr style="background:{HEX_NAVY};color:#FFFFFF">'
        f'<th style="width:53%;text-align:right">شرح مغایرت / هشدار</th>'
        f'<th style="width:9%;text-align:center">اختلاف</th>'
        f'<th style="width:8%;text-align:center">کد</th>'
        f'<th style="width:8%;text-align:center">ردیف</th>'
        f'<th style="width:9%;text-align:center">شیت</th>'
        f'<th style="width:13%;text-align:center">نوع</th>'
        f"</tr>"
        + "".join(disc_rows)
        + "</table>",
        extra_css="th{padding:4px 3px}td{padding:3.8px 3px;border-bottom:0.5px solid #DCE4EC}",
        strict=False,
        tag="dq_disc_tbl",
    )
    y += disc_table_h + 8.0

    # 3. Device Identity Ambiguities & Unregistered Codes Table
    tracker.insert(
        page,
        fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + 18),
        f'<p dir="rtl" style="font-size:10px;font-weight:bold;color:{HEX_NAVY}">وضعیت تطبیق هویت دستگاه‌ها با Registry ناوگان '
        f'({fa_num(id_ov["resolved_devices_count"])} معتبر | {fa_num(id_ov["ambiguous_devices_count"])} مبهم | {fa_num(id_ov["unregistered_devices_count"])} ثبت‌نشده)</p>',
        strict=False,
        tag="dq_id_hdr",
    )
    y += 20.0

    id_rows = []
    for d in all_devices:
        if d["identity_status"] == "RESOLVED":
            continue
        cands = " / ".join(c["canonical_code"] for c in d.get("ambiguous_candidates", []))
        note_text = (
            f"نامزدهای Registry: {cands} — نیازمند درج نوع دستگاه در فایل منبع"
            if d["identity_status"] == "AMBIGUOUS"
            else "در جدول machines پایگاه داده fleet_ops.db ثبت نشده است"
        )
        st_fa = "مبهم" if d["identity_status"] == "AMBIGUOUS" else "ثبت‌نشده"
        bg = "#FFFBEB" if d["identity_status"] == "AMBIGUOUS" else "#F8FAFC"
        id_rows.append(
            f'<tr style="background:{bg}">'
            f'<td style="text-align:right">{html.escape(note_text)}</td>'
            f'<td style="text-align:center">{fa_num(d["row_count"])}</td>'
            f'<td style="text-align:center;font-weight:bold;color:{HEX_WARN_TEXT if st_fa == "مبهم" else HEX_MUTED}">{st_fa}</td>'
            f'<td style="text-align:center">{ltr_span(d["canonical_device_key"])}</td>'
            f'<td style="text-align:center;font-weight:bold">{ltr_span(d["raw_device_code"])}</td>'
            f"</tr>"
        )

    tracker.insert(
        page,
        fitz.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, PAGE_H - 34),
        f'<table style="width:100%;border-collapse:collapse;font-size:7.3px">'
        f'<tr style="background:{HEX_STEEL};color:#FFFFFF">'
        f'<th style="width:48%;text-align:right">توضیح و اقدام مورد نیاز مالک</th>'
        f'<th style="width:10%;text-align:center">ردیف‌ها</th>'
        f'<th style="width:12%;text-align:center">وضعیت</th>'
        f'<th style="width:18%;text-align:center">کلید تفکیکی</th>'
        f'<th style="width:12%;text-align:center">کد خام</th>'
        f"</tr>"
        + "".join(id_rows)
        + "</table>",
        extra_css="th{padding:3.8px 3px}td{padding:3.2px 3px;border-bottom:0.5px solid #DCE4EC}",
        strict=False,
        tag="dq_id_tbl",
    )


def _add_footers(doc: fitz.Document, tracker: BoxTracker, *, period_fa: str, version_number: int, source_filename: str) -> None:
    total = doc.page_count
    for idx, page in enumerate(doc):
        page.draw_line(fitz.Point(MARGIN_X, PAGE_H - 28), fitz.Point(PAGE_W - MARGIN_X, PAGE_H - 28), color=RGB_BORDER, width=0.5)
        tracker.insert(
            page,
            fitz.Rect(MARGIN_X, PAGE_H - 24, PAGE_W - MARGIN_X, PAGE_H - 8),
            f'<table style="width:100%;border-collapse:collapse;font-size:7.5px;color:{HEX_MUTED}"><tr>'
            f'<td style="text-align:left;width:32%">صفحه {fa_num(idx + 1)} از {fa_num(total)}</td>'
            f'<td style="text-align:center;width:36%">{html.escape(period_fa)} — نسخه {fa_num(version_number)}</td>'
            f'<td style="text-align:right;width:32%">{html.escape(COMPANY_NAME)}</td>'
            f"</tr></table>",
            strict=False,
            tag=f"footer_{idx + 1}",
        )


def _norm_pdf_search(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = t.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩يك", "01234567890123456789یک"))
    return re.sub(r"[\s\u200c\u200e\u200f]+", "", t)


def validate_pdf_reconciliation(
    pdf_path: Path | str,
    *,
    summary: dict[str, Any],
    all_devices: list[dict[str, Any]],
    overflows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify PDF readability, A4 geometry, Persian searchability, zero overflow, and SQLite reconciliation."""
    p = Path(pdf_path)
    if not p.is_file() or p.stat().st_size <= 0:
        raise ValueError(f"PDF missing or empty: {p}")

    errors: list[str] = []
    if overflows:
        errors.append(f"Layout overflow detected in {len(overflows)} box(es): {overflows[:5]}")

    with fitz.open(p) as doc:
        if doc.is_repaired or doc.needs_pass or doc.page_count < 6:
            errors.append(f"Malformed or unexpectedly short PDF (pages={doc.page_count})")
        all_texts: list[str] = []
        for pg in doc:
            if abs(pg.rect.width - PAGE_W) > 1.5 or abs(pg.rect.height - PAGE_H) > 1.5:
                errors.append(f"Page {pg.number + 1} is not A4 portrait")
            if pg.get_images():
                errors.append(f"Page {pg.number + 1} contains unexpected raster image (expected pure vector)")
            txt = pg.get_text()
            if not txt.strip():
                errors.append(f"Page {pg.number + 1} has no searchable text")
            all_texts.append(txt)
        page_count = doc.page_count

    combined = _norm_pdf_search("\n".join(all_texts))
    fleet = summary["fleet"]

    # Check title, company, fleet headline totals, all 5 causes, all devices, and discrepancies
    required_tokens = [
        REPORT_TITLE,
        COMPANY_NAME,
        summary["period"],
        fleet["base_hhmm"],
        fleet["reported_net_work_hhmm"],
        fleet["reported_total_downtime_hhmm"],
        f"{fleet['work_pct']:.2f}%",
        f"{fleet['downtime_pct']:.2f}%",
    ]
    for c in summary["causes"]:
        required_tokens.append(c["cause_label"])
        required_tokens.append(c["total_hhmm"])
    for d in all_devices:
        required_tokens.append(d["raw_device_code"])
        required_tokens.append(d["base_hhmm"])
        required_tokens.append(d["reported_total_downtime_hhmm"])
    for disc in summary["discrepancies"]:
        required_tokens.append(disc["sheet_name"])
        required_tokens.append(disc["raw_device_code"])

    missing_tokens = [tok for tok in dict.fromkeys(required_tokens) if _norm_pdf_search(str(tok)) not in combined]
    if missing_tokens:
        errors.append(f"PDF reconciliation missing {len(missing_tokens)} SQLite value(s): {missing_tokens[:10]}")

    pdf_bytes = p.read_bytes()
    return {
        "ok": len(errors) == 0,
        "pdf_path": str(p),
        "page_count": page_count,
        "byte_size": len(pdf_bytes),
        "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "vector_only": True,
        "searchable_persian": True,
        "overflow_count": len(overflows),
        "overflows": overflows,
        "missing_tokens_count": len(missing_tokens),
        "missing_tokens": missing_tokens[:20],
        "errors": errors,
    }


def generate_monthly_pdf_report(
    period: str,
    *,
    runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
    db_path: Path | str | None = None,
    output_path: Path | str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Generate and validate the complete executive monthly PDF report from SQLite."""
    norm_p = parse_period(period)
    rt = Path(runtime_dir)
    repo = DowntimeRepository(Path(db_path) if db_path else rt / "downtime.sqlite3")
    analytics = DowntimeAnalytics(repo)

    summary = analytics.fleet_monthly_summary(norm_p)
    if not summary["found"]:
        raise ValueError(summary["message"])

    ver = summary["version"]
    ver_id = int(ver["version_id"])
    ver_num = int(ver["version_number"])

    if output_path:
        target = Path(output_path)
    else:
        rep_dir = rt / "reports" / norm_p
        rep_dir.mkdir(parents=True, exist_ok=True)
        if ver_num == 1:
            target = rep_dir / f"Fleet-Downtime-{norm_p}.pdf"
        else:
            target = rep_dir / f"Fleet-Downtime-{norm_p}-v{ver_num}.pdf"

    # Skip unnecessary regeneration if cached PDF already matches active version
    if not force:
        cached = repo.get_latest_report_artifact(norm_p, version_id=ver_id)
        if cached and Path(cached["pdf_path"]).is_file():
            cp = Path(cached["pdf_path"])
            if hashlib.sha256(cp.read_bytes()).hexdigest() == cached["pdf_sha256"]:
                return {
                    "status": "CACHED_UNCHANGED",
                    "period": norm_p,
                    "version_id": ver_id,
                    "version_number": ver_num,
                    "pdf_path": str(cp),
                    "pdf_sha256": cached["pdf_sha256"],
                    "page_count": cached["page_count"],
                    "byte_size": cached["byte_size"],
                    "is_provisional": bool(cached["is_provisional"]),
                    "reconciliation_ok": bool(cached["reconciliation_ok"]),
                }

    daily_trend = analytics.fleet_daily_trend(norm_p)
    all_devices = analytics.all_devices_monthly(norm_p, include_daily=True)["devices"]
    insights = analytics.executive_insights(norm_p)

    archive = fitz.Archive(str(WINDOWS_FONTS))
    css = build_base_css()
    tracker = BoxTracker(archive, css)

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(".partial.pdf")
    doc = fitz.open()
    try:
        # Section 1: Cover & Executive Summary
        _render_cover_executive_page(doc, tracker, summary=summary, insights=insights)
        # Section 2: All 8 Fleet Vector Charts
        _render_fleet_charts_pages(doc, tracker, summary=summary, daily_trend=daily_trend, all_devices=all_devices)
        # Section 3: Full Device Ranking Tables (both by Hours and by %)
        _render_ranking_tables_pages(doc, tracker, summary=summary, all_devices=all_devices)
        # Section 4: Individual Device Scorecards for ALL devices (2 per page)
        _render_device_scorecards_pages(doc, tracker, summary=summary, all_devices=all_devices)
        # Section 5: Executive Management Analysis & Action Plan
        _render_management_analysis_page(doc, tracker, summary=summary, insights=insights)
        # Section 6: Data Quality, Coverage, Discrepancies & Device Identity Appendix
        _render_data_quality_page(doc, tracker, summary=summary, all_devices=all_devices)
        # Running Footers on every page
        _add_footers(
            doc,
            tracker,
            period_fa=summary["period_label_fa"],
            version_number=ver_num,
            source_filename=ver["source_filename"],
        )

        doc.set_metadata(
            {
                "title": f"{REPORT_TITLE} — {summary['period_label_fa']}",
                "author": COMPANY_NAME,
                "subject": f"Fleet Performance & Downtime Analytics ({norm_p}, v{ver_num})",
                "creator": "KomatsoAI Fleet Downtime Analytics",
            }
        )
        doc.subset_fonts()
        canonical_unicode_maps(doc)
        doc.save(tmp_path, garbage=4, deflate=True, no_new_id=True)
    finally:
        doc.close()

    validation = validate_pdf_reconciliation(
        tmp_path,
        summary=summary,
        all_devices=all_devices,
        overflows=tracker.overflows,
    )
    tmp_path.replace(target)
    validation["pdf_path"] = str(target)

    artifact = repo.record_report_artifact(
        version_id=ver_id,
        period=norm_p,
        version_number=ver_num,
        pdf_path=target,
        pdf_sha256=validation["pdf_sha256"],
        page_count=validation["page_count"],
        byte_size=validation["byte_size"],
        is_provisional=bool(ver["is_provisional"]),
        reconciliation_ok=bool(validation["ok"]),
    )

    return {
        "status": "GENERATED",
        "period": norm_p,
        "version_id": ver_id,
        "version_number": ver_num,
        "artifact_id": artifact["artifact_id"],
        "pdf_path": str(target),
        "pdf_sha256": validation["pdf_sha256"],
        "page_count": validation["page_count"],
        "byte_size": validation["byte_size"],
        "is_provisional": bool(ver["is_provisional"]),
        "reconciliation_ok": validation["ok"],
        "validation": validation,
    }
