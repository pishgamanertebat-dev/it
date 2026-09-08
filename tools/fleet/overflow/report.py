from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path

DEFAULT_SOURCE = Path(r"E:\Function\سرریز روزانه.xlsx")
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩كي", "01234567890123456789کی")
HELP = "بنویسید: سرریز روزانه\nیا با تاریخ: سرریز 1405/06/14"


class ReportError(ValueError):
    pass


def normalize(value):
    return " ".join(str(value or "").translate(DIGITS).replace("\u200c", " ").split())


def validate_date(value):
    match = re.fullmatch(r"(1[34]\d{2})[/.-](\d{1,2})[/.-](\d{1,2})", normalize(value))
    if not match:
        raise ReportError("قالب تاریخ معتبر نیست.\n" + HELP)
    year, month, day = map(int, match.groups())
    # Jalaali break interval 1210..1634; accepted years are 1300..1499.
    leap = (((year - 1210) % 33 + 1) % 33 - 1) % 4 == 0
    maximum = 31 if month <= 6 else (30 if month < 12 or leap else 29)
    if not 1 <= month <= 12 or not 1 <= day <= maximum:
        raise ReportError("تاریخ نامعتبر است.\n" + HELP)
    return f"{year:04d}/{month:02d}/{day:02d}"


def parse_command(text):
    """Return (matched, date); malformed report commands stay out of the LLM."""
    match = re.fullmatch(r"سر\s*ریز(?:\s+روزانه)?(?:\s+(.*))?", normalize(text))
    if not match:
        return False, None
    tail = (match.group(1) or "").strip()
    if tail in {"", "میخواهم", "می خواهم", "میخوام", "می خوام"}:
        return True, None
    tail = re.sub(r"^(?:تاریخ|مورخ)\s+", "", tail)
    return True, validate_date(tail)


def load_report(date=None, source=None):
    import openpyxl

    date = validate_date(date) if date else None
    path = Path(source or os.environ.get("FLEET_OVERFLOW_SOURCE", DEFAULT_SOURCE))
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ReportError("فایل سرریز روزانه در دسترس نیست یا قابل خواندن نیست؛ لطفاً دوباره تلاش کنید.") from exc
    try:
        dates = {}
        for sheet in workbook:
            top = list(sheet.iter_rows(min_row=1, max_row=2, values_only=True))
            if len(top) < 2 or normalize(top[1][0]) != "ردیف":
                continue
            found = set()
            for cell in top[0]:
                try:
                    found.add(validate_date(cell))
                except ReportError:
                    pass
            if len(found) == 1:
                dates.setdefault(found.pop(), []).append(sheet.title)
        if not dates:
            raise ReportError("هیچ گزارش روزانه با تاریخ معتبر در فایل پیدا نشد.")
        selected = date or max(dates)
        if selected not in dates:
            raise ReportError(f"برای تاریخ {selected} گزارش سرریز ثبت نشده است.\nآخرین گزارش موجود: {max(dates)}\n" + HELP)
        if len(dates[selected]) != 1:
            raise ReportError(f"تاریخ {selected} در چند شیت تکرار شده است؛ ابتدا تاریخ‌های فایل اکسل باید اصلاح شوند.")
        sheet = workbook[dates[selected][0]]
        rows = list(sheet.values)
        indices = [i for i, value in enumerate(rows[1]) if value is not None]
        headers = [str(rows[1][i]) for i in indices]
        if not {"نام دستگاه", "کد دستگاه"}.issubset({normalize(h) for h in headers}):
            raise ReportError("ستون‌های نام و کد دستگاه در گزارش پیدا نشدند.")
        records, totals = [], None
        for row in rows[2:]:
            values = [row[i] if i < len(row) else None for i in indices]
            if normalize(values[0]) == "جمع":
                totals = values
                break
            if any(value is not None for value in values):
                records.append(values)
        if not records:
            raise ReportError(f"گزارش تاریخ {selected} هنوز ردیف ثبت‌شده ندارد.")
        return {"date": selected, "latest_date": max(dates), "sheet": sheet.title,
                "headers": headers, "rows": records, "totals": totals}
    finally:
        workbook.close()


def display(value):
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render_report(report, output_dir):
    """Render native HTML tables with MuPDF's Persian shaping, without an AI."""
    import fitz

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    # Small pages keep all columns legible in messenger photos.
    groups = [report["rows"][i:i + 16] for i in range(0, len(report["rows"]), 16)]
    for page_no, group in enumerate(groups, 1):
        rows = list(group)
        if page_no == len(groups) and report["totals"] is not None:
            rows.append(report["totals"])
        def cells(values, tag):
            # Reverse physical columns: MuPDF table layout is left-to-right.
            return "".join(f'<{tag} dir="auto">{html.escape(display(v))}</{tag}>' for v in reversed(values))
        table = "<tr>" + cells(report["headers"], "th") + "</tr>"
        for i, row in enumerate(rows):
            color = "#e1edf3" if normalize(row[0]) == "جمع" else ("#f0f5f8" if i % 2 == 0 else "#ffffff")
            table += f'<tr style="background:{color}">' + cells(row, "td") + "</tr>"
        title = f'سرریز روزانه — {report["date"]}'
        body = f'''<html><body><h2 dir="rtl">{title}</h2>
        <p dir="rtl">تعداد ردیف‌ها: {len(report['rows'])} | صفحه {page_no} از {len(groups)}</p>
        <table>{table}</table><p dir="rtl">— یعنی خانهٔ خالی در اکسل؛ صفرها عیناً نمایش داده شده‌اند.</p></body></html>'''
        css = "body{font-family:sans-serif;font-size:14px;color:#173448} h2{font-size:25px} table{border-collapse:collapse;width:100%} th,td{border:1px solid #becdd6;padding:10px 5px;text-align:center} th{background:#173448;color:white} p{font-size:13px}"
        with fitz.open() as doc:
            page = doc.new_page(width=1250, height=1800)
            spare, scale = page.insert_htmlbox(fitz.Rect(25, 20, 1225, 1775), body, css=css, scale_low=1)
            if spare < 0:
                raise ReportError("متن گزارش برای تصویر جدول بیش از حد بلند است؛ قالب گزارش باید بررسی شود.")
            clip = fitz.Rect(0, 0, 1250, min(1800, 1775 - spare + 40))
            path = output_dir / f"overflow-{report['date'].replace('/', '-')}-{page_no}.png"
            page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=clip).save(path)
            paths.append(str(path))
    return paths


def main():
    parser = argparse.ArgumentParser(description="Daily overflow table from Excel")
    parser.add_argument("--date")
    parser.add_argument("--source")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    try:
        report = load_report(args.date, args.source)
        result = {"ok": True, "report": report}
        if args.output_dir:
            result["images"] = render_report(report, args.output_dir)
    except ReportError as exc:
        result = {"ok": False, "message": str(exc)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
