"""Read-only monthly workbook ingestion, snapshotting, validation, and atomic SQLite import.

Uses openpyxl (from E:\\KomatsoAI\\.venv) to read workbooks strictly read-only,
creates an immutable SHA-256 snapshot copy under runtime/fleet-downtime/sources/<period>/,
filters sheets strictly to the target Jalali period, validates every row, and commits atomically.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import io
import json
from pathlib import Path
import shutil
from typing import Any

from . import DEFAULT_DB_PATH, DEFAULT_RUNTIME_DIR
from .normalization import (
    CAUSE_FIELDS,
    detect_month_from_filename,
    extract_note_topics,
    jalali_month_days,
    jalali_to_gregorian,
    map_semantic_headers,
    normalize_persian,
    normalize_raw_code,
    parse_excel_duration_minutes,
    parse_jalali_date,
    parse_period,
    parse_sheet_month_day,
    period_label_fa,
)
from .registry import DEFAULT_FLEET_DB, DEFAULT_MAINT_LAYOUT, FleetDeviceRegistry
from .repository import DowntimeRepository, utc_now
from .validation import compute_month_day_coverage, validate_record_row

MAX_WORKBOOK_BYTES = 32 * 1024 * 1024
DEFAULT_WATCH_DIR = Path(r"E:\Function")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_stable_workbook_bytes(source_path: Path) -> tuple[bytes, str]:
    """Read source workbook read-only and verify content did not change mid-read."""
    if not source_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {source_path}")
    size = source_path.stat().st_size
    if size <= 0 or size > MAX_WORKBOOK_BYTES:
        raise ValueError(f"Workbook size out of bounds ({size} bytes): {source_path}")
    data = source_path.read_bytes()
    digest = sha256_bytes(data)
    if sha256_bytes(source_path.read_bytes()) != digest:
        raise RuntimeError(f"Source workbook changed during read: {source_path}")
    return data, digest


def _locate_headers(ws) -> tuple[int, int, dict[str, int]]:
    """Find semantic header row(s) dynamically (supports 1-row or 2-row merged Persian headers)."""
    max_scan = min(8, ws.max_row)
    for r in range(1, max_scan + 1):
        row_vals = [normalize_persian(ws.cell(r, c).value) for c in range(1, ws.max_column + 1)]
        if any("کد" in v and "دستگاه" in v for v in row_vals):
            # Check if next row contains sub-headers (e.g., تعمیرات / بی باری / تاخیر انفجار)
            next_vals = (
                [normalize_persian(ws.cell(r + 1, c).value) for c in range(1, ws.max_column + 1)]
                if r + 1 <= ws.max_row
                else [""] * ws.max_column
            )
            has_sub = any(
                any(kw in v for kw in ("تعمیرات", "بی باری", "بیباری", "بارگیر", "انفجار", "نام و نام خانوادگی"))
                for v in next_vals
            )
            pairs = [
                (c, row_vals[c - 1], next_vals[c - 1] if has_sub else "")
                for c in range(1, ws.max_column + 1)
            ]
            col_map = map_semantic_headers(pairs)
            data_start = r + 2 if has_sub else r + 1
            return r, data_start, col_map
    return 0, 0, {}


def _detect_sheet_year_from_dates(ws_val, data_start: int, date_col: int | None) -> int | None:
    if not date_col or data_start <= 0:
        return None
    years = Counter()
    for r in range(data_start, min(ws_val.max_row + 1, data_start + 30)):
        raw = ws_val.cell(r, date_col).value
        if raw is None:
            continue
        try:
            jdate, _ = parse_jalali_date(raw)
            years[int(jdate[:4])] += 1
        except ValueError:
            continue
    return years.most_common(1)[0][0] if years else None


def detect_period_from_workbook(source_path: Path | str, explicit_period: str | None = None) -> dict[str, Any]:
    """Inspect workbook sheets and dates (read-only) to verify or detect the target Jalali period."""
    import openpyxl

    path = Path(source_path)
    data, digest = read_stable_workbook_bytes(path)
    fn_year, fn_month = detect_month_from_filename(path.name)

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, keep_links=False)
    try:
        month_counts = Counter()
        year_counts = Counter()
        sheet_months: list[dict[str, Any]] = []
        for sname in wb.sheetnames:
            ws = wb[sname]
            md = parse_sheet_month_day(sname)
            _, data_start, col_map = _locate_headers(ws)
            yr = _detect_sheet_year_from_dates(ws, data_start, col_map.get("date"))
            if md:
                month_counts[md[0]] += 1
            if yr:
                year_counts[yr] += 1
            sheet_months.append({"sheet": sname, "month_day": md, "year": yr})
    finally:
        wb.close()

    if explicit_period:
        target_period = parse_period(explicit_period)
        target_year, target_month = map(int, target_period.split("-"))
    else:
        target_year = fn_year or (year_counts.most_common(1)[0][0] if year_counts else None)
        target_month = fn_month or (month_counts.most_common(1)[0][0] if month_counts else None)
        if not target_year or not target_month:
            raise ValueError(f"Could not reliably determine Jalali period for {path.name}")
        target_period = f"{target_year:04d}-{target_month:02d}"

    # Verify filename month (if present) agrees with selected target_period
    if fn_month is not None and fn_month != target_month:
        raise ValueError(
            f"Filename month ({fn_month:02d}) disagrees with target period ({target_period}) for {path.name}"
        )
    if fn_year is not None and fn_year != target_year:
        raise ValueError(
            f"Filename year ({fn_year}) disagrees with target period ({target_period}) for {path.name}"
        )

    matching_sheets = [
        s for s in sheet_months
        if s["month_day"] and s["month_day"][0] == target_month and (s["year"] in (None, target_year))
    ]
    if not matching_sheets:
        raise ValueError(f"No sheets matching period {target_period} found in {path.name}")

    return {
        "period": target_period,
        "year": target_year,
        "month": target_month,
        "filename_hint": {"year": fn_year, "month": fn_month},
        "source_sha256": digest,
        "total_sheets": len(sheet_months),
        "matching_sheets_count": len(matching_sheets),
    }


def _infer_shift(
    *,
    base_minutes: int,
    same_day_slot: int,
    same_day_total_rows: int,
    same_day_dayshift_count: int,
    notes_normalized: str,
) -> str | None:
    """Infer shift ONLY when supported by clear workbook evidence (never guess)."""
    has_night_note = "شیفت شب" in notes_normalized or "شبکاری" in notes_normalized
    # Paired day + night rows for the same device on the same day (e.g., 11:00 day + 6:00/9:00 night)
    if same_day_total_rows >= 2 and same_day_dayshift_count == 1:
        if same_day_slot == 1 and base_minutes >= 600:
            return "روز"
        if same_day_slot >= 2 and base_minutes < 600:
            return "شب"
    # Standalone night shift block (6h or 9h shift with explicit night mention or short base)
    if base_minutes in (360, 540):
        return "شب"
    if has_night_note and base_minutes < 600:
        return "شب"
    # Primary 11h block without paired night shift: stay conservative ('روز' only when unambiguous)
    if base_minutes == 660 and not has_night_note:
        return "روز"
    return None


def parse_and_validate_workbook(
    source_path: Path | str,
    period: str,
    *,
    registry: FleetDeviceRegistry | None = None,
) -> dict[str, Any]:
    """Read-only extraction and full validation of one monthly period from an Excel workbook."""
    import openpyxl

    target_period = parse_period(period)
    target_year, target_month = map(int, target_period.split("-"))
    path = Path(source_path)
    data, digest = read_stable_workbook_bytes(path)

    # Guard filename/period consistency if filename explicitly names a month
    fn_year, fn_month = detect_month_from_filename(path.name)
    if fn_month is not None and fn_month != target_month:
        raise ValueError(
            f"Source filename '{path.name}' indicates month {fn_month:02d}, conflicting with requested period {target_period}"
        )
    if fn_year is not None and fn_year != target_year:
        raise ValueError(
            f"Source filename '{path.name}' indicates year {fn_year}, conflicting with requested period {target_period}"
        )

    reg = registry or FleetDeviceRegistry(DEFAULT_FLEET_DB, DEFAULT_MAINT_LAYOUT)

    wb_val = openpyxl.load_workbook(io.BytesIO(data), data_only=True, keep_links=False)
    wb_frm = openpyxl.load_workbook(io.BytesIO(data), data_only=False, keep_links=False)

    sheets_meta: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    all_discrepancies: list[dict[str, Any]] = []
    present_days_sheet: dict[int, tuple[str, int]] = {}
    excluded_sheets_list: list[dict[str, Any]] = []

    try:
        for sname in wb_val.sheetnames:
            ws_v = wb_val[sname]
            ws_f = wb_frm[sname]
            md = parse_sheet_month_day(sname)
            header_row, data_start, col_map = _locate_headers(ws_v)
            col_count = len(col_map)

            if md is None:
                info = {
                    "sheet_name": sname,
                    "included": False,
                    "detected_month": None,
                    "detected_day": None,
                    "jalali_date": None,
                    "column_count": col_count,
                    "data_row_count": 0,
                    "exclusion_reason": "نام شیت مطابق الگوی ماه.روز نیست",
                }
                sheets_meta.append(info)
                excluded_sheets_list.append(info)
                continue

            s_month, s_day = md
            if s_month != target_month:
                info = {
                    "sheet_name": sname,
                    "included": False,
                    "detected_month": s_month,
                    "detected_day": s_day,
                    "jalali_date": None,
                    "column_count": col_count,
                    "data_row_count": 0,
                    "exclusion_reason": f"مربوط به ماه دیگر ({s_month:02d}) — خارج از دوره {target_period}",
                }
                sheets_meta.append(info)
                excluded_sheets_list.append(info)
                continue

            # Validate day range for target_year/target_month
            max_days = jalali_month_days(target_year, target_month)
            if not 1 <= s_day <= max_days:
                raise ValueError(f"Invalid day {s_day} in sheet {sname!r} for period {target_period}")

            if header_row == 0 or "device_code" not in col_map or "base_hours" not in col_map:
                raise ValueError(f"Required semantic headers missing in sheet {sname!r}: mapped={col_map}")

            # Verify sheet year if dates are present
            sheet_year = _detect_sheet_year_from_dates(ws_v, data_start, col_map.get("date"))
            if sheet_year is not None and sheet_year != target_year:
                info = {
                    "sheet_name": sname,
                    "included": False,
                    "detected_month": s_month,
                    "detected_day": s_day,
                    "jalali_date": f"{sheet_year:04d}/{s_month:02d}/{s_day:02d}",
                    "column_count": col_count,
                    "data_row_count": 0,
                    "exclusion_reason": f"مربوط به سال {sheet_year} — خارج از دوره {target_period}",
                }
                sheets_meta.append(info)
                excluded_sheets_list.append(info)
                continue

            if s_day in present_days_sheet:
                raise ValueError(
                    f"Duplicate worksheet for day {target_year:04d}/{target_month:02d}/{s_day:02d}: "
                    f"{present_days_sheet[s_day][0]!r} and {sname!r}"
                )

            expected_jdate = f"{target_year:04d}/{target_month:02d}/{s_day:02d}"
            expected_iso = jalali_to_gregorian(target_year, target_month, s_day).isoformat()

            # First pass over sheet rows to count occurrences & formula majority
            raw_rows: list[dict[str, Any]] = []
            td_formula_count = 0
            nw_formula_count = 0
            code_total_counts = Counter()
            code_dayshift_counts = Counter()

            for r in range(data_start, ws_v.max_row + 1):
                raw_code_cell = ws_v.cell(r, col_map["device_code"]).value
                code_str = normalize_raw_code(raw_code_cell)
                if not code_str:
                    continue
                # Skip summary/footer rows if any appear
                if code_str in {"جمع", "جمع کل", "مجموع", "TOTAL"}:
                    continue

                base_raw_v = ws_v.cell(r, col_map["base_hours"]).value
                base_m, _, _ = parse_excel_duration_minutes(base_raw_v)
                code_total_counts[code_str] += 1
                if base_m is not None and base_m >= 600:
                    code_dayshift_counts[code_str] += 1

                if "total_downtime" in col_map:
                    fv = ws_f.cell(r, col_map["total_downtime"]).value
                    if isinstance(fv, str) and fv.startswith("="):
                        td_formula_count += 1
                if "net_work" in col_map:
                    fv = ws_f.cell(r, col_map["net_work"]).value
                    if isinstance(fv, str) and fv.startswith("="):
                        nw_formula_count += 1

                raw_rows.append({"excel_row_num": r, "code_str": code_str})

            sheet_td_mostly_formula = td_formula_count > (len(raw_rows) * 0.5) if raw_rows else False
            sheet_nw_mostly_formula = nw_formula_count > (len(raw_rows) * 0.5) if raw_rows else False

            # Second pass: full extraction, identity resolution, validation
            code_seen_slot = Counter()
            code_seen_dayshift_slot = Counter()
            sheet_record_count = 0

            for item in raw_rows:
                r = item["excel_row_num"]
                code_str = item["code_str"]
                code_seen_slot[code_str] += 1
                slot_num = code_seen_slot[code_str]

                parse_errors: list[str] = []
                empty_fields: list[str] = []
                formula_meta: dict[str, Any] = {}

                def _eval_uncached_formula(coord: str, depth: int = 0) -> int | None:
                    """Bounded local evaluator ONLY when openpyxl/writer omitted cached formula value."""
                    if depth > 6:
                        return None
                    cell_v = ws_v[coord].value
                    if cell_v is not None:
                        m, is_emp, kind = parse_excel_duration_minutes(cell_v)
                        return 0 if is_emp else (m if not kind.startswith(("invalid_", "excel_error")) else None)
                    cell_f = ws_f[coord].value
                    if not isinstance(cell_f, str) or not cell_f.startswith("="):
                        return 0
                    import re as _re
                    expr = cell_f[1:].strip().upper()
                    # Support SUM(A1:E1)
                    sum_m = _re.fullmatch(r"SUM\(\$?([A-Z]{1,3})\$?(\d+):\$?([A-Z]{1,3})\$?(\d+)\)", expr)
                    if sum_m:
                        from openpyxl.utils import column_index_from_string, get_column_letter
                        c1 = column_index_from_string(sum_m.group(1))
                        r1 = int(sum_m.group(2))
                        c2 = column_index_from_string(sum_m.group(3))
                        r2 = int(sum_m.group(4))
                        total = 0
                        for rr in range(min(r1, r2), max(r1, r2) + 1):
                            for cc in range(min(c1, c2), max(c1, c2) + 1):
                                sub = _eval_uncached_formula(f"{get_column_letter(cc)}{rr}", depth + 1)
                                if sub is None:
                                    return None
                                total += sub
                        return total
                    # Support simple +/- of local cell references like D4+E4+G4+H4+F4 or C4-I4
                    tokens = _re.findall(r"([+-]?)\s*\$?([A-Z]{1,3})\$?(\d+)", expr)
                    reconstructed = "".join(f"{s}{c}{n}" for s, c, n in tokens)
                    if tokens and reconstructed == _re.sub(r"[\s$]+", "", expr):
                        total = 0
                        for sign, col_l, row_n in tokens:
                            sub = _eval_uncached_formula(f"{col_l}{row_n}", depth + 1)
                            if sub is None:
                                return None
                            total += -sub if sign == "-" else sub
                        return total
                    return None

                def extract_minutes(col_key: str, *, required_zero_if_empty: bool = True) -> int | None:
                    c = col_map.get(col_key)
                    if c is None:
                        empty_fields.append(col_key)
                        return 0 if required_zero_if_empty else None
                    cell_v = ws_v.cell(r, c)
                    rv = cell_v.value
                    rf = ws_f.cell(r, c).value
                    is_f = isinstance(rf, str) and rf.startswith("=")
                    if col_key in ("total_downtime", "net_work"):
                        formula_meta[col_key] = {
                            "is_formula": is_f,
                            "formula": rf if is_f else None,
                            "cached_present": rv is not None,
                            "sheet_mostly_formula": (
                                sheet_td_mostly_formula if col_key == "total_downtime" else sheet_nw_mostly_formula
                            ),
                        }
                    if rv is None and is_f:
                        evaluated = _eval_uncached_formula(cell_v.coordinate)
                        if evaluated is not None:
                            return evaluated
                    mins, is_empty, kind = parse_excel_duration_minutes(rv)
                    if is_empty:
                        empty_fields.append(col_key)
                        return 0 if required_zero_if_empty else None
                    if mins is None or kind.startswith("invalid_") or kind.startswith("excel_error"):
                        parse_errors.append(f"{col_key}={rv!r} ({kind})")
                        return None
                    return mins

                base_m = extract_minutes("base_hours", required_zero_if_empty=False)
                is_dayshift_row = base_m is not None and base_m >= 600
                if is_dayshift_row:
                    code_seen_dayshift_slot[code_str] += 1
                    dayshift_slot = code_seen_dayshift_slot[code_str]
                else:
                    dayshift_slot = 1

                cause_map: dict[str, int | None] = {
                    "repair_minutes": extract_minutes("repair"),
                    "no_load_minutes": extract_minutes("no_load"),
                    "blast_delay_minutes": extract_minutes("blast_delay"),
                    "no_loader_minutes": extract_minutes("no_loader"),
                    "driver_delay_minutes": extract_minutes("driver_delay"),
                }
                rep_down = extract_minutes("total_downtime")
                rep_net = extract_minutes("net_work")

                raw_date_v = ws_v.cell(r, col_map["date"]).value if "date" in col_map else None
                parsed_jdate = None
                parsed_iso = None
                if raw_date_v is not None and normalize_persian(raw_date_v):
                    try:
                        parsed_jdate, parsed_iso = parse_jalali_date(raw_date_v, default_year=target_year)
                    except ValueError:
                        parsed_jdate = None

                raw_driver = ws_v.cell(r, col_map["drivers"]).value if "drivers" in col_map else ""
                driver_clean = normalize_persian(raw_driver)
                if driver_clean in {"_", "-", "—"}:
                    driver_clean = ""

                raw_note_val = ws_v.cell(r, col_map["notes"]).value if "notes" in col_map else ""
                notes_raw = "" if raw_note_val is None else str(raw_note_val).strip()
                notes_norm = normalize_persian(notes_raw)
                note_topics = extract_note_topics(notes_raw)

                sheet_row_idx_val = ws_v.cell(r, col_map["row_num"]).value if "row_num" in col_map else None
                sheet_row_idx = normalize_persian(sheet_row_idx_val) if sheet_row_idx_val is not None else None

                resolution = reg.resolve_workbook_code(
                    code_str,
                    same_day_dayshift_slot=dayshift_slot,
                    same_day_dayshift_count=code_dayshift_counts[code_str],
                )

                val_res = validate_record_row(
                    period=target_period,
                    sheet_name=sname,
                    excel_row_num=r,
                    raw_device_code=code_str,
                    canonical_device_key=resolution.canonical_device_key,
                    expected_jalali_date=expected_jdate,
                    parsed_jalali_date=parsed_jdate,
                    raw_date_value=raw_date_v,
                    base_minutes=base_m,
                    reported_net_work_minutes=rep_net,
                    reported_total_downtime_minutes=rep_down,
                    cause_minutes=cause_map,
                    empty_fields=empty_fields,
                    formula_metadata=formula_meta,
                    parse_errors=parse_errors,
                )

                shift_val = _infer_shift(
                    base_minutes=base_m or 0,
                    same_day_slot=slot_num,
                    same_day_total_rows=code_total_counts[code_str],
                    same_day_dayshift_count=code_dayshift_counts[code_str],
                    notes_normalized=notes_norm,
                )

                rec = {
                    "period": target_period,
                    "jalali_date": parsed_jdate or expected_jdate,
                    "iso_date": parsed_iso or expected_iso,
                    "raw_device_code": code_str,
                    "canonical_device_key": resolution.canonical_device_key,
                    "registry_machine_id": resolution.registry_machine_id,
                    "registry_canonical_code": resolution.registry_canonical_code,
                    "device_name": resolution.device_name,
                    "device_type": resolution.device_type,
                    "device_group": resolution.device_group,
                    "identity_status": resolution.identity_status,
                    "ambiguous_candidates": list(resolution.ambiguous_candidates),
                    "resolution_note": resolution.resolution_note,
                    "source_row_id": f"{target_period}:{sname}:R{r}",
                    "sheet_name": sname,
                    "excel_row_num": r,
                    "sheet_row_index": sheet_row_idx,
                    "same_day_slot": slot_num,
                    "shift": shift_val,
                    "base_minutes": base_m if base_m is not None else 0,
                    "reported_net_work_minutes": rep_net if rep_net is not None else 0,
                    "reported_total_downtime_minutes": rep_down if rep_down is not None else 0,
                    "computed_causes_minutes": val_res.computed_causes_minutes if val_res.computed_causes_minutes is not None else 0,
                    "computed_net_work_minutes": val_res.computed_net_work_minutes if val_res.computed_net_work_minutes is not None else 0,
                    "repair_minutes": cause_map["repair_minutes"] or 0,
                    "no_load_minutes": cause_map["no_load_minutes"] or 0,
                    "blast_delay_minutes": cause_map["blast_delay_minutes"] or 0,
                    "no_loader_minutes": cause_map["no_loader_minutes"] or 0,
                    "driver_delay_minutes": cause_map["driver_delay_minutes"] or 0,
                    "empty_fields": empty_fields,
                    "formula_metadata": formula_meta,
                    "driver_raw": driver_clean,
                    "notes_raw": notes_raw,
                    "notes_normalized": notes_norm,
                    "note_topics": note_topics,
                    "validation_status": val_res.validation_status,
                    "validation_flags": val_res.validation_flags,
                    "source_sha256": digest,
                }
                all_records.append(rec)
                all_discrepancies.extend(val_res.discrepancies)
                sheet_record_count += 1

            present_days_sheet[s_day] = (sname, sheet_record_count)
            sheets_meta.append(
                {
                    "sheet_name": sname,
                    "included": True,
                    "detected_month": s_month,
                    "detected_day": s_day,
                    "jalali_date": expected_jdate,
                    "column_count": col_count,
                    "data_row_count": sheet_record_count,
                    "exclusion_reason": "",
                }
            )
    finally:
        wb_val.close()
        wb_frm.close()

    if not present_days_sheet:
        raise ValueError(f"No valid worksheets for period {target_period} found in {path.name}")

    day_coverage = compute_month_day_coverage(target_period, set(present_days_sheet.keys()))
    total_cal_days = day_coverage["calendar_days_in_month"]
    day_rows: list[dict[str, Any]] = []
    for d in range(1, total_cal_days + 1):
        jdate = f"{target_year:04d}/{target_month:02d}/{d:02d}"
        iso = jalali_to_gregorian(target_year, target_month, d).isoformat()
        if d in present_days_sheet:
            sh, cnt = present_days_sheet[d]
            day_rows.append(
                {
                    "day_of_month": d,
                    "jalali_date": jdate,
                    "iso_date": iso,
                    "coverage_status": "PRESENT",
                    "sheet_name": sh,
                    "row_count": cnt,
                }
            )
        else:
            day_rows.append(
                {
                    "day_of_month": d,
                    "jalali_date": jdate,
                    "iso_date": iso,
                    "coverage_status": "MISSING_SHEET_UNKNOWN",
                    "sheet_name": None,
                    "row_count": 0,
                }
            )

    # Identity breakdown summary
    identity_tracks: dict[str, dict[str, Any]] = {}
    for rec in all_records:
        k = rec["canonical_device_key"]
        if k not in identity_tracks:
            identity_tracks[k] = {
                "canonical_device_key": k,
                "raw_device_code": rec["raw_device_code"],
                "registry_canonical_code": rec["registry_canonical_code"],
                "device_name": rec["device_name"],
                "device_group": rec["device_group"],
                "identity_status": rec["identity_status"],
                "ambiguous_candidates": rec["ambiguous_candidates"],
                "resolution_note": rec["resolution_note"],
                "row_count": 0,
            }
        identity_tracks[k]["row_count"] += 1

    ambiguous_items = [v for v in identity_tracks.values() if v["identity_status"] == "AMBIGUOUS"]
    unregistered_items = [v for v in identity_tracks.values() if v["identity_status"] == "UNREGISTERED"]
    resolved_items = [v for v in identity_tracks.values() if v["identity_status"] == "RESOLVED"]

    validation_summary = {
        "period": target_period,
        "period_label_fa": period_label_fa(target_period),
        "source_filename": path.name,
        "source_path": str(path),
        "source_sha256": digest,
        "validated_at": utc_now(),
        "sheets": {
            "total": len(sheets_meta),
            "included_period_sheets": sum(1 for s in sheets_meta if s["included"]),
            "excluded_other_sheets": len(excluded_sheets_list),
            "included_sheet_names": [s["sheet_name"] for s in sheets_meta if s["included"]],
            "excluded_sheets": [
                {"sheet_name": s["sheet_name"], "reason": s["exclusion_reason"]}
                for s in excluded_sheets_list
            ],
        },
        "day_coverage": day_coverage,
        "rows": {
            "total": len(all_records),
            "valid": sum(1 for r in all_records if r["validation_status"] == "VALID"),
            "discrepancy": sum(1 for r in all_records if r["validation_status"] == "DISCREPANCY"),
            "invalid": sum(1 for r in all_records if r["validation_status"] == "INVALID"),
        },
        "devices": {
            "distinct_raw_codes": len({r["raw_device_code"] for r in all_records}),
            "distinct_device_tracks": len(identity_tracks),
            "resolved_count": len(resolved_items),
            "ambiguous_count": len(ambiguous_items),
            "unregistered_count": len(unregistered_items),
            "ambiguous_devices": ambiguous_items,
            "unregistered_devices": unregistered_items,
        },
        "discrepancies": [d for d in all_discrepancies if d["severity"] in ("ERROR", "DISCREPANCY")],
        "warnings": [d for d in all_discrepancies if d["severity"] == "WARNING"],
        "totals_minutes": {
            "base_minutes": sum(r["base_minutes"] for r in all_records),
            "reported_net_work_minutes": sum(r["reported_net_work_minutes"] for r in all_records),
            "reported_total_downtime_minutes": sum(r["reported_total_downtime_minutes"] for r in all_records),
            "computed_causes_minutes": sum(r["computed_causes_minutes"] for r in all_records),
            "causes_vs_reported_delta_minutes": (
                sum(r["computed_causes_minutes"] for r in all_records)
                - sum(r["reported_total_downtime_minutes"] for r in all_records)
            ),
            "by_cause": {k: sum(r[k] for r in all_records) for k, _ in CAUSE_FIELDS},
        },
    }

    return {
        "period": target_period,
        "source_filename": path.name,
        "source_path_original": str(path),
        "source_sha256": digest,
        "source_bytes": data,
        "sheets_meta": sheets_meta,
        "day_coverage": day_coverage,
        "day_rows": day_rows,
        "records": all_records,
        "discrepancies": all_discrepancies,
        "validation_summary": validation_summary,
    }


def import_monthly_workbook(
    source_path: Path | str,
    period: str | None = None,
    *,
    runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
    db_path: Path | str | None = None,
    fleet_db_path: Path | str = DEFAULT_FLEET_DB,
    write_evidence: bool = True,
    notes: str = "",
) -> dict[str, Any]:
    """End-to-end idempotent monthly workbook import: snapshot -> validate -> atomic SQLite commit.

    - Never modifies the source workbook.
    - If the exact same SHA-256 is already imported and active for the period, skips duplicate import.
    - If a revised workbook (new SHA-256) arrives, preserves prior version and atomically promotes the new version.
    """
    rt = Path(runtime_dir)
    repo = DowntimeRepository(Path(db_path) if db_path else rt / "downtime.sqlite3")
    repo.ensure_schema()

    src = Path(source_path)
    if period is None:
        detected = detect_period_from_workbook(src)
        target_period = detected["period"]
        source_sha256 = detected["source_sha256"]
    else:
        target_period = parse_period(period)
        _, source_sha256 = read_stable_workbook_bytes(src)

    # Idempotency check: identical SHA-256 already active for this period?
    active = repo.get_active_version(target_period)
    if active and active["source_sha256"] == source_sha256:
        evidence_path = rt / "evidence" / f"{target_period}-validation.json"
        return {
            "status": "UNCHANGED_DUPLICATE",
            "period": target_period,
            "version_id": active["version_id"],
            "version_number": active["version_number"],
            "source_sha256": source_sha256,
            "is_revision": False,
            "data_quality_status": active["data_quality_status"],
            "evidence_path": str(evidence_path) if evidence_path.exists() else None,
            "message": "فایل با همین Hash قبلاً به عنوان نسخه فعال ثبت شده است؛ Import تکراری انجام نشد.",
        }

    # Parse & validate completely BEFORE touching SQLite or snapshots
    registry = FleetDeviceRegistry(fleet_db_path, DEFAULT_MAINT_LAYOUT)
    parsed = parse_and_validate_workbook(src, target_period, registry=registry)

    if parsed["validation_summary"]["rows"]["invalid"] > 0:
        raise ValueError(
            f"Import aborted: {parsed['validation_summary']['rows']['invalid']} invalid row(s) detected in {src.name}"
        )

    # Save immutable snapshot copy under runtime/fleet-downtime/sources/<period>/
    sources_dir = rt / "sources" / target_period
    sources_dir.mkdir(parents=True, exist_ok=True)
    snap_name = f"{source_sha256[:16]}__{src.name}"
    snap_path = sources_dir / snap_name
    if not snap_path.exists():
        tmp_snap = snap_path.with_suffix(".tmp")
        tmp_snap.write_bytes(parsed["source_bytes"])
        tmp_snap.replace(snap_path)

    is_revision = active is not None
    version_row = repo.commit_imported_version(
        period=target_period,
        source_filename=parsed["source_filename"],
        source_path_original=parsed["source_path_original"],
        snapshot_path=str(snap_path),
        source_sha256=source_sha256,
        sheets_meta=parsed["sheets_meta"],
        day_coverage=parsed["day_coverage"],
        day_rows=parsed["day_rows"],
        records=parsed["records"],
        discrepancies=parsed["discrepancies"],
        validation_summary=parsed["validation_summary"],
        notes=notes,
        activate=True,
    )

    evidence_path = None
    if write_evidence:
        ev_dir = rt / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        ev_file = ev_dir / f"{target_period}-validation.json"
        ev_payload = dict(parsed["validation_summary"])
        ev_payload["version_id"] = version_row["version_id"]
        ev_payload["version_number"] = version_row["version_number"]
        ev_payload["is_revision"] = is_revision
        ev_payload["snapshot_path"] = str(snap_path)
        ev_payload["data_quality_status"] = version_row["data_quality_status"]
        tmp_ev = ev_file.with_suffix(".json.tmp")
        tmp_ev.write_text(json.dumps(ev_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_ev.replace(ev_file)
        evidence_path = str(ev_file)

    return {
        "status": "IMPORTED_REVISION" if is_revision else "IMPORTED_NEW",
        "period": target_period,
        "version_id": version_row["version_id"],
        "version_number": version_row["version_number"],
        "source_sha256": source_sha256,
        "snapshot_path": str(snap_path),
        "is_revision": is_revision,
        "data_quality_status": version_row["data_quality_status"],
        "row_count": version_row["row_count"],
        "discrepancy_row_count": version_row["discrepancy_row_count"],
        "warning_count": version_row["warning_count"],
        "evidence_path": evidence_path,
    }


def discover_monthly_workbooks(
    watch_dir: Path | str = DEFAULT_WATCH_DIR,
    *,
    allowed_periods: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Discover candidate monthly downtime workbooks in an authorized directory (read-only).

    Matches Persian filenames containing 'توقفات' and '.xlsx' (ignoring Excel lock files '~$*').
    """
    folder = Path(watch_dir)
    if not folder.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for p in sorted(folder.glob("*.xlsx")):
        if p.name.startswith("~$"):
            continue
        norm_name = normalize_persian(p.name)
        if "توقفات" not in norm_name:
            continue
        try:
            meta = detect_period_from_workbook(p)
        except Exception as exc:
            found.append({"path": str(p), "filename": p.name, "status": "UNPARSEABLE", "error": str(exc)})
            continue
        if allowed_periods is not None and meta["period"] not in allowed_periods:
            continue
        found.append({"path": str(p), "filename": p.name, "status": "DETECTED", **meta})
    return found
