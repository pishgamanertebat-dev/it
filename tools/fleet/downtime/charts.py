"""Pure-vector executive charts rendered directly onto PyMuPDF pages with crisp Persian RTL typography.

Every chart uses fixed semantic colors, vector arcs/bars/lines (print-sharp at any DPI),
and HarfBuzz-shaped HTML boxes via PyMuPDF for zero Persian letter-joining artifacts.
"""
from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Any

import fitz

from .normalization import format_minutes_hhmm, to_persian_digits

# Fixed brand & semantic color palette (consistent across every page & chart)
HEX_NAVY = "#0F2940"
HEX_STEEL = "#1E4D72"
HEX_TEXT = "#162B3D"
HEX_MUTED = "#5A6D7E"
HEX_BORDER = "#D7E0E8"
HEX_SURFACE = "#F6F8FB"
HEX_WHITE = "#FFFFFF"

HEX_WORK = "#16A34A"       # کارکرد: سبز
HEX_REPAIR = "#DC2626"     # تعمیرات: قرمز
HEX_NO_LOAD = "#F59E0B"    # بی‌باری: زرد / کهربایی
HEX_BLAST = "#2563EB"      # تأخیر انفجار: آبی
HEX_NO_LOADER = "#7C3AED"  # نبود بارگیر: بنفش
HEX_DRIVER = "#64748B"     # تأخیر راننده: خاکستری متمایل به آبی
HEX_OPERATIONAL = "#F59E0B"
HEX_WARN_BG = "#FEF3C7"
HEX_WARN_BORDER = "#D97706"
HEX_WARN_TEXT = "#92400E"
HEX_ERR_BG = "#FEE2E2"
HEX_ERR_TEXT = "#991B1B"
HEX_OK_BG = "#DCFCE7"
HEX_OK_TEXT = "#166534"
HEX_MISSING_BG = "#ECEFF3"

CAUSE_HEX_COLORS: dict[str, str] = {
    "repair_minutes": HEX_REPAIR,
    "no_load_minutes": HEX_NO_LOAD,
    "blast_delay_minutes": HEX_BLAST,
    "no_loader_minutes": HEX_NO_LOADER,
    "driver_delay_minutes": HEX_DRIVER,
}


def hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


RGB_NAVY = hex_to_rgb(HEX_NAVY)
RGB_STEEL = hex_to_rgb(HEX_STEEL)
RGB_BORDER = hex_to_rgb(HEX_BORDER)
RGB_SURFACE = hex_to_rgb(HEX_SURFACE)
RGB_WHITE = (1.0, 1.0, 1.0)
RGB_WORK = hex_to_rgb(HEX_WORK)
RGB_REPAIR = hex_to_rgb(HEX_REPAIR)
RGB_NO_LOAD = hex_to_rgb(HEX_NO_LOAD)
RGB_BLAST = hex_to_rgb(HEX_BLAST)
RGB_NO_LOADER = hex_to_rgb(HEX_NO_LOADER)
RGB_DRIVER = hex_to_rgb(HEX_DRIVER)
RGB_MISSING = hex_to_rgb(HEX_MISSING_BG)
RGB_MUTED = hex_to_rgb(HEX_MUTED)

WINDOWS_FONTS = Path(r"C:\Windows\Fonts")


def resolve_font_pair() -> tuple[str, str]:
    """Select clean modern Persian font available on Windows (Dubai preferred, Tahoma fallback)."""
    if (WINDOWS_FONTS / "DUBAI-REGULAR.TTF").is_file() and (WINDOWS_FONTS / "DUBAI-BOLD.TTF").is_file():
        return "DUBAI-REGULAR.TTF", "DUBAI-BOLD.TTF"
    return "tahoma.ttf", "tahomabd.ttf"


def build_base_css() -> str:
    reg, bold = resolve_font_pair()
    return (
        f"@font-face{{font-family:ExecFa;src:url({reg})}}"
        f"@font-face{{font-family:ExecFa;src:url({bold});font-weight:bold}}"
        "body{font-family:ExecFa;color:#162B3D;margin:0;padding:0;direction:rtl;text-align:right;}"
        "p,div,span,td,th,li{font-family:ExecFa;}"
        "p,div,li{text-align:right;}"
        "p{margin:0;line-height:1.35;}"
    )


def fa_num(text: Any) -> str:
    return to_persian_digits(str(text))


def ltr_span(text: Any, *, bold: bool = False, color: str | None = None) -> str:
    styles = []
    if bold:
        styles.append("font-weight:bold")
    if color:
        styles.append(f"color:{color}")
    st_attr = f' style="{";".join(styles)}"' if styles else ""
    escaped = html.escape(fa_num(text))
    return f'<span dir="ltr"{st_attr}>\u200e{escaped}\u200e</span>'


class BoxTracker:
    """Tracks every htmlbox insertion to ensure ZERO silent overflow or text clipping."""

    def __init__(self, archive: fitz.Archive, css: str) -> None:
        self.archive = archive
        self.css = css
        self.overflows: list[dict[str, Any]] = []

    def insert(
        self,
        page: fitz.Page,
        rect: fitz.Rect,
        body_html: str,
        *,
        extra_css: str = "",
        strict: bool = True,
        min_scale: float = 0.88,
        tag: str = "",
    ) -> tuple[float, float]:
        full_css = self.css + extra_css
        spare, scale = page.insert_htmlbox(
            rect,
            body_html,
            css=full_css,
            archive=self.archive,
            scale_low=1.0 if strict else min_scale,
        )
        if spare < 0 or scale < (1.0 if strict else min_scale):
            self.overflows.append(
                {
                    "page": page.number + 1,
                    "tag": tag,
                    "rect": [round(rect.x0, 1), round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1)],
                    "spare": round(spare, 2),
                    "scale": round(scale, 3),
                }
            )
        return spare, scale


def _arc_points(cx: float, cy: float, r: float, start_deg: float, end_deg: float, steps: int = 40) -> list[fitz.Point]:
    pts = []
    for i in range(steps + 1):
        ang = math.radians(start_deg + (end_deg - start_deg) * (i / steps))
        pts.append(fitz.Point(cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def draw_donut_ring(
    page: fitz.Page,
    cx: float,
    cy: float,
    outer_r: float,
    inner_r: float,
    slices: list[tuple[float, str]],
    *,
    separator_color: tuple[float, float, float] = RGB_WHITE,
    separator_width: float = 1.0,
) -> None:
    """Draw a crisp pure-vector donut chart ring from (value, hex_color) pairs."""
    positive = [(v, c) for v, c in slices if v and v > 0]
    total = sum(v for v, _ in positive)
    if total <= 0:
        return
    if len(positive) == 1:
        # Full 360-degree ring
        rgb = hex_to_rgb(positive[0][1])
        page.draw_circle(fitz.Point(cx, cy), outer_r, color=None, fill=rgb)
        page.draw_circle(fitz.Point(cx, cy), inner_r, color=None, fill=RGB_WHITE)
        return

    angle = -90.0
    for val, col_hex in positive:
        sweep = 360.0 * (val / total)
        steps = max(12, int(abs(sweep) / 3.0))
        outer_pts = _arc_points(cx, cy, outer_r, angle, angle + sweep, steps=steps)
        inner_pts = _arc_points(cx, cy, inner_r, angle + sweep, angle, steps=steps)
        poly = outer_pts + inner_pts
        shape = page.new_shape()
        shape.draw_polyline(poly)
        shape.finish(
            color=separator_color,
            fill=hex_to_rgb(col_hex),
            width=separator_width,
            closePath=True,
        )
        shape.commit()
        angle += sweep


def draw_donut_panel(
    page: fitz.Page,
    rect: fitz.Rect,
    tracker: BoxTracker,
    *,
    title: str,
    slices: list[dict[str, Any]],
    center_value: str,
    center_label: str,
    empty_message: str = "بدون توقف ثبت‌شده",
    compact: bool = False,
) -> None:
    """Render a complete framed Donut Chart card with vector ring, centered KPI, and Persian legend."""
    # Soft panel border
    page.draw_rect(rect, color=RGB_BORDER, fill=RGB_WHITE, width=0.6, radius=0.04)

    title_h = 18 if compact else 24
    title_size = 9.0 if compact else 11.0
    tracker.insert(
        page,
        fitz.Rect(rect.x0 + 8, rect.y0 + 4, rect.x1 - 8, rect.y0 + title_h + 4),
        f'<p dir="rtl" style="font-size:{title_size}px;font-weight:bold;color:{HEX_NAVY}">{html.escape(title)}</p>',
        strict=False,
        tag=f"donut_title:{title}",
    )

    total = sum(float(s.get("value") or 0) for s in slices)
    body_top = rect.y0 + title_h + 4
    body_h = rect.y1 - body_top - 6

    if total <= 0:
        # Clean empty-state pill (never draw a broken/empty pie!)
        pill_w = min(rect.width - 20, 140)
        pill_h = 28
        px0 = rect.x0 + (rect.width - pill_w) / 2
        py0 = body_top + (body_h - pill_h) / 2
        pill = fitz.Rect(px0, py0, px0 + pill_w, py0 + pill_h)
        page.draw_rect(pill, color=RGB_WORK, fill=hex_to_rgb(HEX_OK_BG), width=0.8, radius=0.25)
        tracker.insert(
            page,
            fitz.Rect(pill.x0 + 4, pill.y0 + 5, pill.x1 - 4, pill.y1 - 3),
            f'<p dir="rtl" style="text-align:center;font-size:{9.0 if compact else 10.5}px;font-weight:bold;color:{HEX_OK_TEXT}">{html.escape(empty_message)}</p>',
            strict=False,
            tag=f"donut_empty:{title}",
        )
        return

    if compact:
        # Side-by-side compact layout: Donut on LEFT, Legend on RIGHT
        radius = min(body_h * 0.44, rect.width * 0.23, 38.0)
        cx = rect.x0 + radius + 10
        cy = body_top + body_h / 2
        inner_r = radius * 0.58
        draw_donut_ring(page, cx, cy, radius, inner_r, [(s["value"], s["color"]) for s in slices], separator_width=0.8)

        # Center KPI text
        cbox = fitz.Rect(cx - inner_r + 1, cy - 13, cx + inner_r - 1, cy + 14)
        tracker.insert(
            page,
            cbox,
            f'<p style="text-align:center;font-size:8.5px;font-weight:bold;color:{HEX_NAVY};line-height:1.1">'
            f'{ltr_span(center_value, bold=True)}<br>'
            f'<span style="font-size:6.5px;font-weight:normal;color:{HEX_MUTED}">{html.escape(center_label)}</span></p>',
            strict=False,
            tag=f"donut_center:{title}",
        )

        # Legend on right side of compact panel
        leg_x0 = cx + radius + 6
        leg_x1 = rect.x1 - 6
        active_slices = [s for s in slices if s["value"] > 0]
        row_h = min(15.5, (body_h - 2) / max(1, len(active_slices)))
        y = body_top + max(0.0, (body_h - row_h * len(active_slices)) / 2)
        for s in active_slices:
            # Color dot on far right
            dot = fitz.Rect(leg_x1 - 7, y + 3.5, leg_x1 - 1, y + 9.5)
            page.draw_rect(dot, color=None, fill=hex_to_rgb(s["color"]), radius=0.25)
            val_txt = s.get("short_value") or f"{s.get('pct', 0):.1f}%"
            tracker.insert(
                page,
                fitz.Rect(leg_x0, y, leg_x1 - 10, y + row_h + 1),
                f'<p dir="rtl" style="font-size:7.2px;line-height:1.15;color:{HEX_TEXT}">'
                f'<b>{html.escape(s["label"])}</b>: {ltr_span(val_txt, color=HEX_MUTED)}</p>',
                strict=False,
                tag=f"donut_leg:{s['label']}",
            )
            y += row_h
    else:
        # Executive full-size layout: Donut on LEFT, crisp full-text Legend on RIGHT
        radius = min(body_h * 0.42, rect.width * 0.21, 55.0)
        cx = rect.x0 + radius + 12
        cy = body_top + body_h / 2
        inner_r = radius * 0.58
        draw_donut_ring(page, cx, cy, radius, inner_r, [(s["value"], s["color"]) for s in slices], separator_width=1.2)

        cbox = fitz.Rect(cx - inner_r + 2, cy - 20, cx + inner_r - 2, cy + 20)
        tracker.insert(
            page,
            cbox,
            f'<p style="text-align:center;font-size:11.5px;font-weight:bold;color:{HEX_NAVY};line-height:1.15">'
            f'{ltr_span(center_value, bold=True)}<br>'
            f'<span style="font-size:7.6px;font-weight:normal;color:{HEX_MUTED}">{html.escape(center_label)}</span></p>',
            strict=False,
            tag=f"donut_center:{title}",
        )

        leg_x0 = cx + radius + 10
        leg_x1 = rect.x1 - 8
        row_h = min(28.0, (body_h - 4) / max(1, len(slices)))
        y = body_top + max(0.0, (body_h - row_h * len(slices)) / 2)
        for s in slices:
            dot = fitz.Rect(leg_x1 - 8, y + 7, leg_x1 - 1, y + 14)
            page.draw_rect(dot, color=None, fill=hex_to_rgb(s["color"]), radius=0.25)
            hhmm = s.get("hhmm", "")
            pct_str = f"{s.get('pct', 0):.1f}%"
            tracker.insert(
                page,
                fitz.Rect(leg_x0, y + 1, leg_x1 - 12, y + row_h + 1),
                f'<p dir="rtl" style="font-size:8.2px;line-height:1.25;color:{HEX_TEXT}">'
                f'<b>{html.escape(s["label"])}</b><br>'
                f'<span style="font-size:7.8px;color:{HEX_MUTED}">{ltr_span(hhmm, bold=True, color=HEX_NAVY)} ({ltr_span(pct_str)})</span></p>',
                strict=False,
                tag=f"donut_leg_full:{s['label']}",
            )
            y += row_h


def draw_rtl_horizontal_bars(
    page: fitz.Page,
    rect: fitz.Rect,
    tracker: BoxTracker,
    *,
    title: str,
    subtitle: str = "",
    rows: list[dict[str, Any]],
    max_value: float | None = None,
    label_width: float = 115.0,
    value_width: float = 88.0,
    ref_line: tuple[float, str] | None = None,
    legend_items: list[tuple[str, str]] | None = None,
    font_size: float = 8.4,
    bar_radius: float = 0.2,
) -> None:
    """Draw a pure-vector RTL horizontal bar chart (single or stacked segments).

    Each item in `rows` has:
      - `label`: Persian label (placed right-aligned on the right)
      - `segments`: list of (numeric_value, hex_color)
      - `value_text`: formatted string placed on the left of the bar
    """
    page.draw_rect(rect, color=RGB_BORDER, fill=RGB_WHITE, width=0.6, radius=0.03)

    top_y = rect.y0 + 6
    tracker.insert(
        page,
        fitz.Rect(rect.x0 + 10, top_y, rect.x1 - 10, top_y + 18),
        f'<p dir="rtl" style="font-size:11px;font-weight:bold;color:{HEX_NAVY}">{html.escape(title)}</p>',
        strict=False,
        tag=f"hbar_title:{title}",
    )
    top_y += 18
    if subtitle:
        tracker.insert(
            page,
            fitz.Rect(rect.x0 + 10, top_y, rect.x1 - 10, top_y + 13),
            f'<p dir="rtl" style="font-size:8px;color:{HEX_MUTED}">{html.escape(subtitle)}</p>',
            strict=False,
            tag=f"hbar_sub:{title}",
        )
        top_y += 14

    if legend_items:
        lx = rect.x1 - 10
        for lbl, col_hex in legend_items:
            lw = max(55.0, len(lbl) * 4.8 + 18)
            page.draw_rect(fitz.Rect(lx - 8, top_y + 2.5, lx - 1, top_y + 9.5), color=None, fill=hex_to_rgb(col_hex), radius=0.2)
            tracker.insert(
                page,
                fitz.Rect(lx - lw, top_y, lx - 11, top_y + 13),
                f'<p dir="rtl" style="font-size:7.6px;color:{HEX_MUTED}">{html.escape(lbl)}</p>',
                strict=False,
                tag=f"hbar_leg:{lbl}",
            )
            lx -= lw + 6
        top_y += 15

    if not rows:
        return

    chart_bottom = rect.y1 - 14
    chart_h = max(40.0, chart_bottom - top_y)
    bar_right = rect.x1 - label_width - 10
    bar_left = rect.x0 + value_width + 10
    bar_span = max(40.0, bar_right - bar_left)

    totals = [sum(v for v, _ in r["segments"]) for r in rows]
    mv = max_value if (max_value and max_value > 0) else max(totals + [1.0])
    if ref_line and ref_line[0] > mv:
        mv = ref_line[0]

    # Subtle vertical gridlines (0%, 25%, 50%, 75%, 100%)
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gx = bar_right - bar_span * frac
        page.draw_line(
            fitz.Point(gx, top_y),
            fitz.Point(gx, chart_bottom),
            color=RGB_BORDER,
            width=0.45 if frac > 0 else 0.8,
            dashes="[2 2]" if frac > 0 else None,
        )

    row_step = chart_h / len(rows)
    bar_h = max(4.5, min(row_step * 0.64, 16.0))

    for idx, r in enumerate(rows):
        ry = top_y + idx * row_step
        by0 = ry + (row_step - bar_h) / 2
        by1 = by0 + bar_h

        # Alternating very soft stripe for scanability on tall charts
        if idx % 2 == 1 and len(rows) > 12:
            page.draw_rect(
                fitz.Rect(rect.x0 + 4, ry, rect.x1 - 4, ry + row_step),
                color=None,
                fill=RGB_SURFACE,
            )

        # Draw bar segments growing right-to-left
        cur_x = bar_right
        for val, col_hex in r["segments"]:
            if val <= 0:
                continue
            seg_w = max(0.8, bar_span * (val / mv))
            if cur_x - seg_w < bar_left:
                seg_w = max(0.0, cur_x - bar_left)
            if seg_w > 0.2:
                rad = bar_radius if (len(r["segments"]) == 1 and bar_radius and bar_radius > 0) else None
                page.draw_rect(
                    fitz.Rect(cur_x - seg_w, by0, cur_x, by1),
                    color=None,
                    fill=hex_to_rgb(col_hex),
                    radius=rad,
                )
            cur_x -= seg_w

        # Right-side Persian label
        lbl_top = ry + max(0.0, (row_step - (font_size + 4)) / 2)
        tracker.insert(
            page,
            fitz.Rect(bar_right + 5, lbl_top - 1, rect.x1 - 6, ry + row_step + 1),
            f'<p dir="rtl" style="font-size:{font_size}px;line-height:1.05;color:{HEX_TEXT};white-space:nowrap">{html.escape(r["label"])}</p>',
            strict=False,
            tag=f"hbar_lbl:{r['label']}",
        )

        # Left-side exact numeric value text
        tracker.insert(
            page,
            fitz.Rect(rect.x0 + 6, lbl_top - 1, bar_left - 4, ry + row_step + 1),
            f'<p style="text-align:left;font-size:{max(6.8, font_size - 0.4)}px;line-height:1.05;color:{HEX_NAVY};white-space:nowrap">'
            f'{ltr_span(r["value_text"], bold=True)}</p>',
            strict=False,
            tag=f"hbar_val:{r['label']}",
        )

    # Optional reference line (e.g., Fleet Weighted Average)
    if ref_line is not None:
        ref_val, ref_label = ref_line
        rx = bar_right - bar_span * min(1.0, ref_val / mv)
        page.draw_line(
            fitz.Point(rx, top_y - 2),
            fitz.Point(rx, chart_bottom + 2),
            color=RGB_NAVY,
            width=1.1,
            dashes="[3 2]",
        )
        tracker.insert(
            page,
            fitz.Rect(max(rect.x0 + 8, rx - 75), chart_bottom + 1, min(rect.x1 - 8, rx + 75), rect.y1 - 1),
            f'<p dir="rtl" style="text-align:center;font-size:7.3px;font-weight:bold;color:{HEX_NAVY}">{html.escape(ref_label)}</p>',
            strict=False,
            tag="hbar_ref",
        )


def draw_pareto_chart(
    page: fitz.Page,
    rect: fitz.Rect,
    tracker: BoxTracker,
    *,
    title: str,
    causes: list[dict[str, Any]],
) -> None:
    """Draw a pure-vector Pareto chart: descending cause bars + cumulative % line + 80% threshold."""
    page.draw_rect(rect, color=RGB_BORDER, fill=RGB_WHITE, width=0.6, radius=0.03)
    tracker.insert(
        page,
        fitz.Rect(rect.x0 + 10, rect.y0 + 5, rect.x1 - 10, rect.y0 + 23),
        f'<p dir="rtl" style="font-size:11px;font-weight:bold;color:{HEX_NAVY}">{html.escape(title)}</p>',
        strict=False,
        tag="pareto_title",
    )
    if not causes:
        return

    plot = fitz.Rect(rect.x0 + 42, rect.y0 + 30, rect.x1 - 40, rect.y1 - 30)
    max_h = max((c["total_hours"] for c in causes), default=1.0)
    y_max = max(10.0, math.ceil(max_h / 500.0) * 500.0 if max_h > 500 else math.ceil(max_h / 10.0) * 10.0)

    # Horizontal gridlines + Left hours ticks + Right % ticks
    for i in range(5):
        frac = i / 4.0
        gy = plot.y1 - plot.height * frac
        page.draw_line(
            fitz.Point(plot.x0, gy),
            fitz.Point(plot.x1, gy),
            color=RGB_BORDER,
            width=0.45 if i > 0 else 0.85,
            dashes="[2 2]" if i > 0 else None,
        )
        # Left axis: hours
        hr_lbl = f"{int(round(y_max * frac))}"
        tracker.insert(
            page,
            fitz.Rect(rect.x0 + 4, gy - 6, plot.x0 - 4, gy + 7),
            f'<p style="text-align:right;font-size:7.4px;color:{HEX_MUTED}">{ltr_span(hr_lbl)}</p>',
            strict=False,
            tag=f"pareto_yl_{i}",
        )
        # Right axis: %
        pct_lbl = f"{int(frac * 100)}%"
        tracker.insert(
            page,
            fitz.Rect(plot.x1 + 4, gy - 6, rect.x1 - 4, gy + 7),
            f'<p style="text-align:left;font-size:7.4px;color:{HEX_MUTED}">{ltr_span(pct_lbl)}</p>',
            strict=False,
            tag=f"pareto_yr_{i}",
        )

    # 80% Pareto threshold line
    y80 = plot.y1 - plot.height * 0.80
    page.draw_line(fitz.Point(plot.x0, y80), fitz.Point(plot.x1, y80), color=RGB_REPAIR, width=0.7, dashes="[4 3]")

    n = len(causes)
    slot_w = plot.width / n
    bar_w = min(slot_w * 0.52, 46.0)
    line_pts: list[fitz.Point] = []

    for idx, c in enumerate(causes):
        cx = plot.x0 + slot_w * (idx + 0.5)
        bh = plot.height * (c["total_hours"] / y_max) if y_max > 0 else 0
        col_hex = CAUSE_HEX_COLORS.get(c["cause_key"], HEX_STEEL)
        bar_rect = fitz.Rect(cx - bar_w / 2, plot.y1 - bh, cx + bar_w / 2, plot.y1)
        if bh > 0.5:
            page.draw_rect(bar_rect, color=None, fill=hex_to_rgb(col_hex))

        # Hours label above bar
        bar_top_y = max(plot.y0 + 2, plot.y1 - bh - 13)
        tracker.insert(
            page,
            fitz.Rect(cx - slot_w * 0.48, bar_top_y, cx + slot_w * 0.48, bar_top_y + 12),
            f'<p style="text-align:center;font-size:7.6px;font-weight:bold;color:{HEX_TEXT}">{ltr_span(c["total_hhmm"])}</p>',
            strict=False,
            tag=f"pareto_bv_{idx}",
        )

        # X-axis category label
        tracker.insert(
            page,
            fitz.Rect(cx - slot_w * 0.49, plot.y1 + 3, cx + slot_w * 0.49, rect.y1 - 2),
            f'<p dir="rtl" style="text-align:center;font-size:8.0px;font-weight:bold;color:{HEX_TEXT}">{html.escape(c["cause_label"])}</p>',
            strict=False,
            tag=f"pareto_xl_{idx}",
        )

        py = plot.y1 - plot.height * (min(100.0, c["cumulative_pct"]) / 100.0)
        line_pts.append(fitz.Point(cx, py))

    # Cumulative Pareto line (individual segments so PyMuPDF never closes a chord) + dots + callouts
    for i in range(len(line_pts) - 1):
        page.draw_line(line_pts[i], line_pts[i + 1], color=RGB_NAVY, width=1.6)
    for idx, (pt, c) in enumerate(zip(line_pts, causes)):
        page.draw_circle(pt, 3.2, color=RGB_NAVY, fill=RGB_WHITE, width=1.4)
        ly = pt.y + 6 if idx == len(line_pts) - 1 else max(plot.y0 - 2, pt.y - 16)
        if idx == 0 and abs(ly - (plot.y1 - plot.height * (c["total_hours"] / y_max) - 13)) < 14:
            ly = max(plot.y0 - 2, pt.y + 6)
        cum_pct_txt = f"{c['cumulative_pct']:.1f}%"
        tracker.insert(
            page,
            fitz.Rect(pt.x - 28, ly, pt.x + 28, ly + 14),
            f'<p style="text-align:center;font-size:7.6px;font-weight:bold;color:{HEX_NAVY}">{ltr_span(cum_pct_txt)}</p>',
            strict=False,
            tag=f"pareto_cv_{idx}",
        )


def draw_daily_stacked_trend(
    page: fitz.Page,
    rect: fitz.Rect,
    tracker: BoxTracker,
    *,
    title: str,
    days_series: list[dict[str, Any]],
    total_calendar_days: int = 30,
    compact: bool = False,
) -> None:
    """Draw a pure-vector daily work/downtime stacked bar chart across all days 1..N.

    Explicitly renders missing-sheet days as pale-gray 'فاقد شیت' gaps (NEVER as zero!).
    """
    page.draw_rect(rect, color=RGB_BORDER, fill=RGB_WHITE, width=0.6, radius=0.03)
    title_h = 16 if compact else 24
    tracker.insert(
        page,
        fitz.Rect(rect.x0 + 8, rect.y0 + 3, rect.x1 - 8, rect.y0 + title_h + 2),
        f'<p dir="rtl" style="font-size:{8.8 if compact else 11.0}px;font-weight:bold;color:{HEX_NAVY}">{html.escape(title)}</p>',
        strict=False,
        tag=f"daily_title:{title}",
    )

    # Legend strip for full-size chart
    top_pad = title_h + 4
    if not compact:
        legend = [
            ("کارکرد خالص", HEX_WORK),
            ("توقف تعمیراتی", HEX_REPAIR),
            ("توقف عملیاتی", HEX_NO_LOAD),
            ("روز فاقد شیت (نامشخص)", HEX_MISSING_BG),
        ]
        lx = rect.x1 - 10
        for lbl, col in legend:
            lw = len(lbl) * 4.5 + 18
            page.draw_rect(
                fitz.Rect(lx - 8, rect.y0 + top_pad + 1, lx - 1, rect.y0 + top_pad + 8),
                color=RGB_BORDER if col == HEX_MISSING_BG else None,
                fill=hex_to_rgb(col),
                width=0.5,
                radius=0.15,
            )
            tracker.insert(
                page,
                fitz.Rect(lx - lw, rect.y0 + top_pad - 1, lx - 11, rect.y0 + top_pad + 11),
                f'<p dir="rtl" style="font-size:7.6px;color:{HEX_MUTED}">{html.escape(lbl)}</p>',
                strict=False,
                tag=f"daily_leg:{lbl}",
            )
            lx -= lw + 6
        top_pad += 15

    left_pad = 24 if compact else 34
    bottom_pad = 15 if compact else 20
    plot = fitz.Rect(rect.x0 + left_pad, rect.y0 + top_pad, rect.x1 - 8, rect.y1 - bottom_pad)

    # Map day_of_month -> record
    by_day: dict[int, dict[str, Any]] = {}
    for item in days_series:
        dnum = item.get("day_of_month")
        if dnum is None and item.get("jalali_date"):
            dnum = int(str(item["jalali_date"]).split("/")[2])
        if dnum is not None:
            by_day[int(dnum)] = item

    max_base = 0.0
    for item in by_day.values():
        if item.get("coverage_status", "PRESENT") == "PRESENT":
            b = float(item.get("base_minutes", 0)) / 60.0
            if b > max_base:
                max_base = b
    if max_base <= 0:
        max_base = 12.0 if compact else 700.0
    y_max = (
        math.ceil(max_base / 4.0) * 4.0
        if max_base <= 24
        else math.ceil(max_base / 100.0) * 100.0
    )

    # Horizontal gridlines & Y ticks
    ticks = 3 if compact else 4
    for i in range(ticks + 1):
        frac = i / ticks
        gy = plot.y1 - plot.height * frac
        page.draw_line(
            fitz.Point(plot.x0, gy),
            fitz.Point(plot.x1, gy),
            color=RGB_BORDER,
            width=0.4 if i > 0 else 0.75,
            dashes="[2 2]" if i > 0 else None,
        )
        lbl = f"{int(round(y_max * frac))}"
        tracker.insert(
            page,
            fitz.Rect(rect.x0 + 2, gy - 5.5, plot.x0 - 3, gy + 6.5),
            f'<p style="text-align:right;font-size:{6.4 if compact else 7.4}px;color:{HEX_MUTED}">{ltr_span(lbl)}</p>',
            strict=False,
            tag=f"daily_y_{i}",
        )

    slot_w = plot.width / total_calendar_days
    bar_w = max(2.2, slot_w * 0.72)

    for day in range(1, total_calendar_days + 1):
        cx = plot.x0 + slot_w * (day - 0.5)
        bx0 = cx - bar_w / 2
        bx1 = cx + bar_w / 2
        rec = by_day.get(day)
        is_present = bool(rec and rec.get("coverage_status", "PRESENT") == "PRESENT" and rec.get("base_minutes", 0) > 0)

        if not is_present:
            # Soft gray placeholder bar indicating missing sheet (NOT zero!)
            mh = plot.height * 0.92
            page.draw_rect(
                fitz.Rect(bx0, plot.y1 - mh, bx1, plot.y1),
                color=RGB_BORDER,
                fill=RGB_MISSING,
                width=0.35,
            )
        else:
            work_h = float(rec.get("reported_net_work_minutes", 0)) / 60.0
            rep_h = float(rec.get("repair_minutes", 0)) / 60.0
            total_down_h = float(rec.get("reported_total_downtime_minutes", 0)) / 60.0
            op_h = max(0.0, total_down_h - rep_h)
            # Stack: bottom green work, middle red repair, top amber operational
            cur_y = plot.y1
            for val_h, rgb in ((work_h, RGB_WORK), (rep_h, RGB_REPAIR), (op_h, RGB_NO_LOAD)):
                if val_h <= 0:
                    continue
                sh = plot.height * (val_h / y_max)
                if cur_y - sh < plot.y0:
                    sh = max(0.0, cur_y - plot.y0)
                if sh > 0.3:
                    page.draw_rect(fitz.Rect(bx0, cur_y - sh, bx1, cur_y), color=None, fill=rgb)
                cur_y -= sh

        # X-axis day ticks (every day on full chart, every 3rd day on compact)
        show_tick = (not compact) or (day in (1, 5, 10, 15, 20, 25, total_calendar_days))
        if show_tick:
            tracker.insert(
                page,
                fitz.Rect(cx - 8, plot.y1 + 2, cx + 8, rect.y1 - 1),
                f'<p style="text-align:center;font-size:{6.2 if compact else 7.0}px;color:{HEX_MUTED if is_present else "#9AA7B4"}">'
                f'{ltr_span(str(day))}</p>',
                strict=False,
                tag=f"daily_x_{day}",
            )


def draw_coverage_strip(
    page: fitz.Page,
    rect: fitz.Rect,
    tracker: BoxTracker,
    *,
    present_days: list[int],
    total_days: int = 30,
) -> None:
    """Draw a 30-day visual coverage strip distinguishing present days vs missing-sheet days."""
    present_set = set(present_days)
    cell_w = rect.width / total_days
    for d in range(1, total_days + 1):
        # Right-to-left calendar progression (day 1 on right, day 30 on left)
        x1 = rect.x1 - (d - 1) * cell_w
        x0 = x1 - cell_w
        box = fitz.Rect(x0 + 1, rect.y0, x1 - 1, rect.y1)
        ok = d in present_set
        page.draw_rect(
            box,
            color=RGB_WORK if ok else RGB_BORDER,
            fill=hex_to_rgb(HEX_OK_BG) if ok else RGB_MISSING,
            width=0.5,
            radius=0.15,
        )
        tracker.insert(
            page,
            fitz.Rect(box.x0, box.y0 + 2, box.x1, box.y1 - 1),
            f'<p style="text-align:center;font-size:6.8px;font-weight:{"bold" if ok else "normal"};'
            f'color:{HEX_OK_TEXT if ok else HEX_MUTED}">{ltr_span(str(d))}</p>',
            strict=False,
            tag=f"cov_day_{d}",
        )
