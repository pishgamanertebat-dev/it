"""Pure-stdlib SQLite repository for Fleet Performance & Downtime Analytics.

Provides versioned, idempotent, atomic monthly storage, precomputed analytical summaries,
read-only query views, report artifact tracking, and per-recipient delivery receipts.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from . import DEFAULT_DB_PATH
from .normalization import CAUSE_FIELDS, CAUSE_LABEL_BY_KEY, format_minutes_hhmm

SCHEMA_VERSION = 1

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_versions (
        version_id INTEGER PRIMARY KEY AUTOINCREMENT,
        period TEXT NOT NULL,
        version_number INTEGER NOT NULL,
        source_filename TEXT NOT NULL,
        source_path_original TEXT NOT NULL,
        snapshot_path TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0, 1)),
        status TEXT NOT NULL CHECK(status IN ('IMPORTING', 'ACTIVE', 'SUPERSEDED', 'FAILED')),
        data_quality_status TEXT NOT NULL,
        total_sheets INTEGER NOT NULL,
        period_sheets_count INTEGER NOT NULL,
        excluded_sheets_count INTEGER NOT NULL,
        calendar_days_in_month INTEGER NOT NULL,
        present_days_count INTEGER NOT NULL,
        missing_days_count INTEGER NOT NULL,
        present_days_json TEXT NOT NULL,
        missing_days_json TEXT NOT NULL,
        row_count INTEGER NOT NULL,
        valid_row_count INTEGER NOT NULL,
        discrepancy_row_count INTEGER NOT NULL,
        invalid_row_count INTEGER NOT NULL,
        warning_count INTEGER NOT NULL DEFAULT 0,
        distinct_raw_codes INTEGER NOT NULL,
        distinct_device_tracks INTEGER NOT NULL,
        resolved_devices_count INTEGER NOT NULL,
        ambiguous_devices_count INTEGER NOT NULL,
        unregistered_devices_count INTEGER NOT NULL,
        validation_summary_json TEXT NOT NULL,
        imported_at TEXT NOT NULL,
        activated_at TEXT,
        notes TEXT NOT NULL DEFAULT '',
        UNIQUE(period, version_number)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_import_versions_one_active
        ON import_versions(period) WHERE is_active = 1
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_import_versions_period_hash
        ON import_versions(period, source_sha256)
    """,
    """
    CREATE TABLE IF NOT EXISTS period_sheets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        sheet_name TEXT NOT NULL,
        included INTEGER NOT NULL CHECK(included IN (0, 1)),
        detected_month INTEGER,
        detected_day INTEGER,
        jalali_date TEXT,
        column_count INTEGER NOT NULL,
        data_row_count INTEGER NOT NULL,
        exclusion_reason TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS period_days (
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        day_of_month INTEGER NOT NULL,
        jalali_date TEXT NOT NULL,
        iso_date TEXT NOT NULL,
        coverage_status TEXT NOT NULL CHECK(coverage_status IN ('PRESENT', 'MISSING_SHEET_UNKNOWN')),
        sheet_name TEXT,
        row_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (version_id, day_of_month)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS downtime_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        jalali_date TEXT NOT NULL,
        iso_date TEXT NOT NULL,
        raw_device_code TEXT NOT NULL,
        canonical_device_key TEXT NOT NULL,
        registry_machine_id INTEGER,
        registry_canonical_code TEXT,
        device_name TEXT NOT NULL,
        device_type TEXT,
        device_group TEXT NOT NULL,
        identity_status TEXT NOT NULL CHECK(identity_status IN ('RESOLVED', 'AMBIGUOUS', 'UNREGISTERED')),
        ambiguous_candidates_json TEXT NOT NULL DEFAULT '[]',
        source_row_id TEXT NOT NULL,
        sheet_name TEXT NOT NULL,
        excel_row_num INTEGER NOT NULL,
        sheet_row_index TEXT,
        same_day_slot INTEGER NOT NULL DEFAULT 1,
        shift TEXT,
        base_minutes INTEGER NOT NULL,
        reported_net_work_minutes INTEGER NOT NULL,
        reported_total_downtime_minutes INTEGER NOT NULL,
        computed_causes_minutes INTEGER NOT NULL,
        computed_net_work_minutes INTEGER NOT NULL,
        repair_minutes INTEGER NOT NULL,
        no_load_minutes INTEGER NOT NULL,
        blast_delay_minutes INTEGER NOT NULL,
        no_loader_minutes INTEGER NOT NULL,
        driver_delay_minutes INTEGER NOT NULL,
        empty_fields_json TEXT NOT NULL DEFAULT '[]',
        formula_metadata_json TEXT NOT NULL DEFAULT '{}',
        driver_raw TEXT NOT NULL DEFAULT '',
        notes_raw TEXT NOT NULL DEFAULT '',
        notes_normalized TEXT NOT NULL DEFAULT '',
        note_topics_json TEXT NOT NULL DEFAULT '[]',
        validation_status TEXT NOT NULL CHECK(validation_status IN ('VALID', 'DISCREPANCY', 'INVALID')),
        validation_flags_json TEXT NOT NULL DEFAULT '[]',
        source_sha256 TEXT NOT NULL,
        UNIQUE(version_id, sheet_name, excel_row_num)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_records_ver_date ON downtime_records(version_id, jalali_date)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_records_ver_device ON downtime_records(version_id, canonical_device_key, iso_date)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_records_ver_raw_code ON downtime_records(version_id, raw_device_code, iso_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS record_note_topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id) ON DELETE CASCADE,
        record_id INTEGER NOT NULL REFERENCES downtime_records(id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        jalali_date TEXT NOT NULL,
        canonical_device_key TEXT NOT NULL,
        topic_key TEXT NOT NULL,
        topic_label TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_note_topics_ver_topic ON record_note_topics(version_id, topic_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_note_topics_ver_dev ON record_note_topics(version_id, canonical_device_key, topic_key)
    """,
    """
    CREATE TABLE IF NOT EXISTS discrepancies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        sheet_name TEXT NOT NULL,
        excel_row_num INTEGER NOT NULL,
        jalali_date TEXT NOT NULL,
        raw_device_code TEXT NOT NULL,
        canonical_device_key TEXT NOT NULL,
        issue_type TEXT NOT NULL,
        severity TEXT NOT NULL CHECK(severity IN ('ERROR', 'DISCREPANCY', 'WARNING')),
        message TEXT NOT NULL,
        reported_value TEXT,
        computed_value TEXT,
        delta_minutes INTEGER
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_discrepancies_ver ON discrepancies(version_id, severity)
    """,
    # Precomputed analytical summary tables (for <5ms deterministic queries!)
    """
    CREATE TABLE IF NOT EXISTS summary_fleet_monthly (
        version_id INTEGER PRIMARY KEY REFERENCES import_versions(version_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        total_rows INTEGER NOT NULL,
        devices_with_data INTEGER NOT NULL,
        days_with_data INTEGER NOT NULL,
        missing_days_count INTEGER NOT NULL,
        base_minutes INTEGER NOT NULL,
        reported_net_work_minutes INTEGER NOT NULL,
        reported_total_downtime_minutes INTEGER NOT NULL,
        computed_causes_minutes INTEGER NOT NULL,
        causes_vs_reported_delta_minutes INTEGER NOT NULL,
        repair_minutes INTEGER NOT NULL,
        no_load_minutes INTEGER NOT NULL,
        blast_delay_minutes INTEGER NOT NULL,
        no_loader_minutes INTEGER NOT NULL,
        driver_delay_minutes INTEGER NOT NULL,
        work_pct REAL NOT NULL,
        downtime_pct REAL NOT NULL,
        repair_pct_of_base REAL NOT NULL,
        repair_pct_of_downtime REAL NOT NULL,
        top_cause_key TEXT,
        top_cause_label TEXT,
        discrepancy_count INTEGER NOT NULL,
        warning_count INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS summary_device_monthly (
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        canonical_device_key TEXT NOT NULL,
        raw_device_code TEXT NOT NULL,
        registry_canonical_code TEXT,
        device_name TEXT NOT NULL,
        device_type TEXT,
        device_group TEXT NOT NULL,
        identity_status TEXT NOT NULL,
        ambiguous_candidates_json TEXT NOT NULL DEFAULT '[]',
        row_count INTEGER NOT NULL,
        days_with_data INTEGER NOT NULL,
        days_with_work INTEGER NOT NULL,
        days_with_downtime INTEGER NOT NULL,
        days_zero_work INTEGER NOT NULL,
        night_shift_rows INTEGER NOT NULL,
        base_minutes INTEGER NOT NULL,
        reported_net_work_minutes INTEGER NOT NULL,
        reported_total_downtime_minutes INTEGER NOT NULL,
        computed_causes_minutes INTEGER NOT NULL,
        repair_minutes INTEGER NOT NULL,
        no_load_minutes INTEGER NOT NULL,
        blast_delay_minutes INTEGER NOT NULL,
        no_loader_minutes INTEGER NOT NULL,
        driver_delay_minutes INTEGER NOT NULL,
        work_pct REAL NOT NULL,
        downtime_pct REAL NOT NULL,
        repair_pct_of_base REAL NOT NULL,
        repair_pct_of_downtime REAL NOT NULL,
        top_cause_key TEXT,
        top_cause_label TEXT,
        max_daily_downtime_minutes INTEGER NOT NULL,
        max_daily_downtime_date TEXT,
        max_consecutive_downtime_days INTEGER NOT NULL,
        max_consecutive_zero_work_days INTEGER NOT NULL,
        discrepancy_count INTEGER NOT NULL,
        top_notes_json TEXT NOT NULL DEFAULT '[]',
        top_topics_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (version_id, canonical_device_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS summary_fleet_daily (
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        jalali_date TEXT NOT NULL,
        iso_date TEXT NOT NULL,
        sheet_name TEXT NOT NULL,
        row_count INTEGER NOT NULL,
        devices_count INTEGER NOT NULL,
        base_minutes INTEGER NOT NULL,
        reported_net_work_minutes INTEGER NOT NULL,
        reported_total_downtime_minutes INTEGER NOT NULL,
        computed_causes_minutes INTEGER NOT NULL,
        repair_minutes INTEGER NOT NULL,
        no_load_minutes INTEGER NOT NULL,
        blast_delay_minutes INTEGER NOT NULL,
        no_loader_minutes INTEGER NOT NULL,
        driver_delay_minutes INTEGER NOT NULL,
        work_pct REAL NOT NULL,
        downtime_pct REAL NOT NULL,
        discrepancy_count INTEGER NOT NULL,
        PRIMARY KEY (version_id, jalali_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS summary_device_daily (
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        jalali_date TEXT NOT NULL,
        iso_date TEXT NOT NULL,
        canonical_device_key TEXT NOT NULL,
        raw_device_code TEXT NOT NULL,
        row_count INTEGER NOT NULL,
        base_minutes INTEGER NOT NULL,
        reported_net_work_minutes INTEGER NOT NULL,
        reported_total_downtime_minutes INTEGER NOT NULL,
        computed_causes_minutes INTEGER NOT NULL,
        repair_minutes INTEGER NOT NULL,
        no_load_minutes INTEGER NOT NULL,
        blast_delay_minutes INTEGER NOT NULL,
        no_loader_minutes INTEGER NOT NULL,
        driver_delay_minutes INTEGER NOT NULL,
        work_pct REAL NOT NULL,
        downtime_pct REAL NOT NULL,
        discrepancy_count INTEGER NOT NULL,
        PRIMARY KEY (version_id, jalali_date, canonical_device_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS summary_causes_monthly (
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        cause_key TEXT NOT NULL,
        cause_label TEXT NOT NULL,
        total_minutes INTEGER NOT NULL,
        share_of_causes_pct REAL NOT NULL,
        share_of_base_pct REAL NOT NULL,
        affected_rows INTEGER NOT NULL,
        affected_devices INTEGER NOT NULL,
        PRIMARY KEY (version_id, cause_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS summary_group_monthly (
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        device_group TEXT NOT NULL,
        is_confirmed_group INTEGER NOT NULL CHECK(is_confirmed_group IN (0, 1)),
        devices_count INTEGER NOT NULL,
        row_count INTEGER NOT NULL,
        base_minutes INTEGER NOT NULL,
        reported_net_work_minutes INTEGER NOT NULL,
        reported_total_downtime_minutes INTEGER NOT NULL,
        computed_causes_minutes INTEGER NOT NULL,
        repair_minutes INTEGER NOT NULL,
        no_load_minutes INTEGER NOT NULL,
        blast_delay_minutes INTEGER NOT NULL,
        no_loader_minutes INTEGER NOT NULL,
        driver_delay_minutes INTEGER NOT NULL,
        work_pct REAL NOT NULL,
        downtime_pct REAL NOT NULL,
        repair_pct_of_base REAL NOT NULL,
        PRIMARY KEY (version_id, device_group)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS summary_note_topics_monthly (
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        topic_key TEXT NOT NULL,
        topic_label TEXT NOT NULL,
        mention_rows INTEGER NOT NULL,
        affected_devices INTEGER NOT NULL,
        PRIMARY KEY (version_id, topic_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_artifacts (
        artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        version_number INTEGER NOT NULL,
        pdf_path TEXT NOT NULL,
        pdf_sha256 TEXT NOT NULL,
        page_count INTEGER NOT NULL,
        byte_size INTEGER NOT NULL,
        is_provisional INTEGER NOT NULL DEFAULT 1 CHECK(is_provisional IN (0, 1)),
        reconciliation_ok INTEGER NOT NULL DEFAULT 0 CHECK(reconciliation_ok IN (0, 1)),
        generated_at TEXT NOT NULL,
        UNIQUE(version_id, pdf_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS delivery_runs (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        period TEXT NOT NULL,
        version_id INTEGER NOT NULL REFERENCES import_versions(version_id),
        artifact_id INTEGER REFERENCES report_artifacts(artifact_id),
        pdf_sha256 TEXT NOT NULL,
        delivery_kind TEXT NOT NULL CHECK(delivery_kind IN ('INITIAL', 'REVISION', 'SIMULATED')),
        is_dry_run INTEGER NOT NULL DEFAULT 0 CHECK(is_dry_run IN (0, 1)),
        approved_by TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS delivery_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES delivery_runs(run_id) ON DELETE CASCADE,
        period TEXT NOT NULL,
        version_id INTEGER NOT NULL,
        pdf_sha256 TEXT NOT NULL,
        recipient_key TEXT NOT NULL,
        recipient_name TEXT NOT NULL,
        bale_user_id TEXT,
        resolution_status TEXT NOT NULL,
        delivery_status TEXT NOT NULL CHECK(
            delivery_status IN ('PENDING', 'SENDING', 'SENT', 'FAILED', 'UNCERTAIN', 'BLOCKED_AMBIGUOUS', 'BLOCKED_INVALID', 'SKIPPED_DUPLICATE')
        ),
        message_id TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        sent_at TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(run_id, recipient_key)
    )
    """,
    # Analytical SQL views for active versions
    """
    CREATE VIEW IF NOT EXISTS v_active_fleet_monthly AS
    SELECT v.version_number, v.source_filename, v.source_sha256, v.data_quality_status, s.*
    FROM summary_fleet_monthly s
    JOIN import_versions v ON v.version_id = s.version_id
    WHERE v.is_active = 1
    """,
    """
    CREATE VIEW IF NOT EXISTS v_active_device_monthly AS
    SELECT v.version_number, v.data_quality_status, s.*
    FROM summary_device_monthly s
    JOIN import_versions v ON v.version_id = s.version_id
    WHERE v.is_active = 1
    """,
    """
    CREATE VIEW IF NOT EXISTS v_active_fleet_daily AS
    SELECT v.version_number, s.*
    FROM summary_fleet_daily s
    JOIN import_versions v ON v.version_id = s.version_id
    WHERE v.is_active = 1
    """,
    """
    CREATE VIEW IF NOT EXISTS v_active_device_daily AS
    SELECT v.version_number, s.*
    FROM summary_device_daily s
    JOIN import_versions v ON v.version_id = s.version_id
    WHERE v.is_active = 1
    """,
    """
    CREATE VIEW IF NOT EXISTS v_active_causes_monthly AS
    SELECT v.version_number, s.*
    FROM summary_causes_monthly s
    JOIN import_versions v ON v.version_id = s.version_id
    WHERE v.is_active = 1
    """,
    """
    CREATE VIEW IF NOT EXISTS v_active_group_monthly AS
    SELECT v.version_number, s.*
    FROM summary_group_monthly s
    JOIN import_versions v ON v.version_id = s.version_id
    WHERE v.is_active = 1
    """,
    """
    CREATE VIEW IF NOT EXISTS v_active_discrepancies AS
    SELECT v.version_number, d.*
    FROM discrepancies d
    JOIN import_versions v ON v.version_id = d.version_id
    WHERE v.is_active = 1
    """,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_pct(numerator: int | float, denominator: int | float) -> float:
    """Safe percentage rounded to 2 decimal places; returns 0.0 when denominator <= 0."""
    if not denominator or denominator <= 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 2)


def _max_streak_over_present_days(
    present_day_records: list[tuple[int, bool]],
) -> int:
    """Compute longest streak across consecutive PRESENT days only.

    Missing calendar days (missing sheets) break continuity rather than being assumed
    either downtime or work, per Section 7.
    """
    best = 0
    current = 0
    prev_day: int | None = None
    for day_num, condition in sorted(present_day_records, key=lambda x: x[0]):
        if not condition:
            current = 0
            prev_day = day_num
            continue
        if prev_day is not None and day_num == prev_day + 1:
            current += 1
        else:
            current = 1
        if current > best:
            best = current
        prev_day = day_num
    return best


class DowntimeRepository:
    """SQLite storage and query repository for Fleet Downtime Analytics."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)

    def ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect(write=True)) as conn, conn:
            for stmt in SCHEMA_STATEMENTS:
                conn.execute(stmt)
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def _connect(self, *, write: bool = False) -> sqlite3.Connection:
        if write:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
        else:
            if not self.db_path.exists():
                raise FileNotFoundError(f"Downtime database does not exist: {self.db_path}")
            uri = self.db_path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def get_active_version(self, period: str) -> dict[str, Any] | None:
        if not self.db_path.exists():
            return None
        with closing(self._connect(write=False)) as conn:
            row = conn.execute(
                "SELECT * FROM import_versions WHERE period = ? AND is_active = 1 LIMIT 1",
                (period,),
            ).fetchone()
            return dict(row) if row else None

    def get_version_by_hash(self, period: str, source_sha256: str) -> dict[str, Any] | None:
        if not self.db_path.exists():
            return None
        with closing(self._connect(write=False)) as conn:
            row = conn.execute(
                "SELECT * FROM import_versions WHERE period = ? AND source_sha256 = ? "
                "AND status IN ('ACTIVE', 'SUPERSEDED') ORDER BY version_number DESC LIMIT 1",
                (period, source_sha256),
            ).fetchone()
            return dict(row) if row else None

    def list_periods(self) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with closing(self._connect(write=False)) as conn:
            rows = conn.execute(
                """
                SELECT v.period, v.version_id, v.version_number, v.source_filename, v.source_sha256,
                       v.data_quality_status, v.present_days_count, v.missing_days_count,
                       v.calendar_days_in_month, v.row_count, v.discrepancy_row_count,
                       v.distinct_device_tracks, v.resolved_devices_count, v.ambiguous_devices_count,
                       v.unregistered_devices_count, v.imported_at, v.activated_at,
                       f.base_minutes, f.reported_net_work_minutes, f.reported_total_downtime_minutes,
                       f.work_pct, f.downtime_pct
                FROM import_versions v
                LEFT JOIN summary_fleet_monthly f ON f.version_id = v.version_id
                WHERE v.is_active = 1
                ORDER BY v.period DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def list_versions(self, period: str) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with closing(self._connect(write=False)) as conn:
            rows = conn.execute(
                "SELECT * FROM import_versions WHERE period = ? ORDER BY version_number DESC",
                (period,),
            ).fetchall()
            return [dict(r) for r in rows]

    def commit_imported_version(
        self,
        *,
        period: str,
        source_filename: str,
        source_path_original: str,
        snapshot_path: str,
        source_sha256: str,
        sheets_meta: list[dict[str, Any]],
        day_coverage: dict[str, Any],
        day_rows: list[dict[str, Any]],
        records: list[dict[str, Any]],
        discrepancies: list[dict[str, Any]],
        validation_summary: dict[str, Any],
        notes: str = "",
        activate: bool = True,
    ) -> dict[str, Any]:
        """Atomically insert a complete new version, compute all summary tables, and optionally activate it.

        If any error occurs, the entire transaction rolls back and any previous active version remains untouched.
        """
        self.ensure_schema()
        now = utc_now()

        distinct_raw = len({r["raw_device_code"] for r in records})
        device_tracks: dict[str, str] = {}
        for r in records:
            device_tracks[r["canonical_device_key"]] = r["identity_status"]
        resolved_cnt = sum(1 for st in device_tracks.values() if st == "RESOLVED")
        ambig_cnt = sum(1 for st in device_tracks.values() if st == "AMBIGUOUS")
        unreg_cnt = sum(1 for st in device_tracks.values() if st == "UNREGISTERED")

        valid_cnt = sum(1 for r in records if r["validation_status"] == "VALID")
        discrep_cnt = sum(1 for r in records if r["validation_status"] == "DISCREPANCY")
        invalid_cnt = sum(1 for r in records if r["validation_status"] == "INVALID")
        warning_cnt = sum(1 for d in discrepancies if d["severity"] == "WARNING")
        period_sheets = sum(1 for s in sheets_meta if s["included"])
        excluded_sheets = len(sheets_meta) - period_sheets

        if invalid_cnt > 0:
            dq_status = "INVALID_RECORDS_PRESENT"
        elif discrep_cnt > 0:
            dq_status = "PROVISIONAL_WITH_DISCREPANCIES"
        elif day_coverage["missing_days_count"] > 0:
            dq_status = "PARTIAL_MONTH_COVERAGE"
        else:
            dq_status = "CLEAN"

        with closing(self._connect(write=True)) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                max_ver = conn.execute(
                    "SELECT COALESCE(MAX(version_number), 0) FROM import_versions WHERE period = ?",
                    (period,),
                ).fetchone()[0]
                next_ver = int(max_ver) + 1

                cur = conn.execute(
                    """
                    INSERT INTO import_versions (
                        period, version_number, source_filename, source_path_original,
                        snapshot_path, source_sha256, is_active, status, data_quality_status,
                        total_sheets, period_sheets_count, excluded_sheets_count,
                        calendar_days_in_month, present_days_count, missing_days_count,
                        present_days_json, missing_days_json,
                        row_count, valid_row_count, discrepancy_row_count, invalid_row_count, warning_count,
                        distinct_raw_codes, distinct_device_tracks,
                        resolved_devices_count, ambiguous_devices_count, unregistered_devices_count,
                        validation_summary_json, imported_at, activated_at, notes
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, 0, 'IMPORTING', ?,
                        ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, NULL, ?
                    )
                    """,
                    (
                        period,
                        next_ver,
                        source_filename,
                        source_path_original,
                        snapshot_path,
                        source_sha256,
                        dq_status,
                        len(sheets_meta),
                        period_sheets,
                        excluded_sheets,
                        day_coverage["calendar_days_in_month"],
                        day_coverage["present_days_count"],
                        day_coverage["missing_days_count"],
                        json.dumps(day_coverage["present_days"], ensure_ascii=False),
                        json.dumps(day_coverage["missing_days"], ensure_ascii=False),
                        len(records),
                        valid_cnt,
                        discrep_cnt,
                        invalid_cnt,
                        warning_cnt,
                        distinct_raw,
                        len(device_tracks),
                        resolved_cnt,
                        ambig_cnt,
                        unreg_cnt,
                        json.dumps(validation_summary, ensure_ascii=False),
                        now,
                        notes,
                    ),
                )
                version_id = int(cur.lastrowid)

                # Insert sheet inventory
                conn.executemany(
                    """
                    INSERT INTO period_sheets (
                        version_id, period, sheet_name, included, detected_month, detected_day,
                        jalali_date, column_count, data_row_count, exclusion_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            version_id,
                            period,
                            s["sheet_name"],
                            1 if s["included"] else 0,
                            s.get("detected_month"),
                            s.get("detected_day"),
                            s.get("jalali_date"),
                            s.get("column_count", 0),
                            s.get("data_row_count", 0),
                            s.get("exclusion_reason", ""),
                        )
                        for s in sheets_meta
                    ],
                )

                # Insert calendar day coverage
                conn.executemany(
                    """
                    INSERT INTO period_days (
                        version_id, period, day_of_month, jalali_date, iso_date,
                        coverage_status, sheet_name, row_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            version_id,
                            period,
                            d["day_of_month"],
                            d["jalali_date"],
                            d["iso_date"],
                            d["coverage_status"],
                            d.get("sheet_name"),
                            d.get("row_count", 0),
                        )
                        for d in day_rows
                    ],
                )

                # Insert downtime records + note topics
                for rec in records:
                    rcur = conn.execute(
                        """
                        INSERT INTO downtime_records (
                            version_id, period, jalali_date, iso_date,
                            raw_device_code, canonical_device_key, registry_machine_id,
                            registry_canonical_code, device_name, device_type, device_group,
                            identity_status, ambiguous_candidates_json,
                            source_row_id, sheet_name, excel_row_num, sheet_row_index,
                            same_day_slot, shift,
                            base_minutes, reported_net_work_minutes, reported_total_downtime_minutes,
                            computed_causes_minutes, computed_net_work_minutes,
                            repair_minutes, no_load_minutes, blast_delay_minutes,
                            no_loader_minutes, driver_delay_minutes,
                            empty_fields_json, formula_metadata_json,
                            driver_raw, notes_raw, notes_normalized, note_topics_json,
                            validation_status, validation_flags_json, source_sha256
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            version_id,
                            period,
                            rec["jalali_date"],
                            rec["iso_date"],
                            rec["raw_device_code"],
                            rec["canonical_device_key"],
                            rec["registry_machine_id"],
                            rec["registry_canonical_code"],
                            rec["device_name"],
                            rec["device_type"],
                            rec["device_group"],
                            rec["identity_status"],
                            json.dumps(rec["ambiguous_candidates"], ensure_ascii=False),
                            rec["source_row_id"],
                            rec["sheet_name"],
                            rec["excel_row_num"],
                            rec.get("sheet_row_index"),
                            rec["same_day_slot"],
                            rec.get("shift"),
                            rec["base_minutes"],
                            rec["reported_net_work_minutes"],
                            rec["reported_total_downtime_minutes"],
                            rec["computed_causes_minutes"],
                            rec["computed_net_work_minutes"],
                            rec["repair_minutes"],
                            rec["no_load_minutes"],
                            rec["blast_delay_minutes"],
                            rec["no_loader_minutes"],
                            rec["driver_delay_minutes"],
                            json.dumps(rec["empty_fields"], ensure_ascii=False),
                            json.dumps(rec["formula_metadata"], ensure_ascii=False),
                            rec["driver_raw"],
                            rec["notes_raw"],
                            rec["notes_normalized"],
                            json.dumps(rec["note_topics"], ensure_ascii=False),
                            rec["validation_status"],
                            json.dumps(rec["validation_flags"], ensure_ascii=False),
                            source_sha256,
                        ),
                    )
                    rec_id = int(rcur.lastrowid)
                    for topic in rec["note_topics"]:
                        conn.execute(
                            """
                            INSERT INTO record_note_topics (
                                version_id, record_id, period, jalali_date,
                                canonical_device_key, topic_key, topic_label
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                version_id,
                                rec_id,
                                period,
                                rec["jalali_date"],
                                rec["canonical_device_key"],
                                topic["topic_key"],
                                topic["topic_label"],
                            ),
                        )

                # Insert discrepancies
                conn.executemany(
                    """
                    INSERT INTO discrepancies (
                        version_id, period, sheet_name, excel_row_num, jalali_date,
                        raw_device_code, canonical_device_key, issue_type, severity,
                        message, reported_value, computed_value, delta_minutes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            version_id,
                            period,
                            d["sheet_name"],
                            d["excel_row_num"],
                            d["jalali_date"],
                            d["raw_device_code"],
                            d["canonical_device_key"],
                            d["issue_type"],
                            d["severity"],
                            d["message"],
                            None if d.get("reported_value") is None else str(d.get("reported_value")),
                            None if d.get("computed_value") is None else str(d.get("computed_value")),
                            d.get("delta_minutes"),
                        )
                        for d in discrepancies
                    ],
                )

                # Precompute all summary tables inside the same atomic transaction
                self._populate_summaries(
                    conn,
                    version_id=version_id,
                    period=period,
                    records=records,
                    day_coverage=day_coverage,
                    discrepancies=discrepancies,
                )

                # Atomically promote to active version if requested
                if activate:
                    conn.execute(
                        "UPDATE import_versions SET is_active = 0, status = 'SUPERSEDED' "
                        "WHERE period = ? AND is_active = 1",
                        (period,),
                    )
                    conn.execute(
                        "UPDATE import_versions SET is_active = 1, status = 'ACTIVE', activated_at = ? "
                        "WHERE version_id = ?",
                        (now, version_id),
                    )
                else:
                    conn.execute(
                        "UPDATE import_versions SET status = 'SUPERSEDED' WHERE version_id = ?",
                        (version_id,),
                    )

                conn.commit()
                return dict(
                    conn.execute(
                        "SELECT * FROM import_versions WHERE version_id = ?", (version_id,)
                    ).fetchone()
                )
            except Exception:
                conn.rollback()
                raise

    def activate_version(self, period: str, version_number: int) -> dict[str, Any]:
        """Atomically switch the active version for a period (admin operation)."""
        now = utc_now()
        with closing(self._connect(write=True)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            target = conn.execute(
                "SELECT version_id FROM import_versions WHERE period = ? AND version_number = ? AND status != 'FAILED'",
                (period, version_number),
            ).fetchone()
            if not target:
                conn.rollback()
                raise ValueError(f"Version {version_number} not found for period {period}")
            vid = int(target["version_id"])
            conn.execute(
                "UPDATE import_versions SET is_active = 0, status = 'SUPERSEDED' "
                "WHERE period = ? AND is_active = 1 AND version_id != ?",
                (period, vid),
            )
            conn.execute(
                "UPDATE import_versions SET is_active = 1, status = 'ACTIVE', activated_at = ? WHERE version_id = ?",
                (now, vid),
            )
            conn.commit()
            return dict(conn.execute("SELECT * FROM import_versions WHERE version_id = ?", (vid,)).fetchone())

    def _populate_summaries(
        self,
        conn: sqlite3.Connection,
        *,
        version_id: int,
        period: str,
        records: list[dict[str, Any]],
        day_coverage: dict[str, Any],
        discrepancies: list[dict[str, Any]],
    ) -> None:
        # 1. Fleet daily & device daily aggregation
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_device: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_device_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for r in records:
            by_day[r["jalali_date"]].append(r)
            by_device[r["canonical_device_key"]].append(r)
            by_device_day[(r["jalali_date"], r["canonical_device_key"])].append(r)
            by_group[r["device_group"]].append(r)

        # Fleet monthly totals (weighted sums, never unweighted averages!)
        f_base = sum(r["base_minutes"] for r in records)
        f_net = sum(r["reported_net_work_minutes"] for r in records)
        f_down = sum(r["reported_total_downtime_minutes"] for r in records)
        f_causes = sum(r["computed_causes_minutes"] for r in records)
        f_cause_totals = {k: sum(r[k] for r in records) for k, _ in CAUSE_FIELDS}
        top_cause_key = max(f_cause_totals, key=lambda k: f_cause_totals[k]) if f_causes > 0 else None
        top_cause_label = CAUSE_LABEL_BY_KEY.get(top_cause_key) if top_cause_key else None
        discrep_cnt = sum(1 for r in records if r["validation_status"] in ("DISCREPANCY", "INVALID"))
        warn_cnt = sum(1 for d in discrepancies if d["severity"] == "WARNING")

        conn.execute(
            """
            INSERT INTO summary_fleet_monthly (
                version_id, period, total_rows, devices_with_data, days_with_data, missing_days_count,
                base_minutes, reported_net_work_minutes, reported_total_downtime_minutes,
                computed_causes_minutes, causes_vs_reported_delta_minutes,
                repair_minutes, no_load_minutes, blast_delay_minutes, no_loader_minutes, driver_delay_minutes,
                work_pct, downtime_pct, repair_pct_of_base, repair_pct_of_downtime,
                top_cause_key, top_cause_label, discrepancy_count, warning_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                period,
                len(records),
                len(by_device),
                day_coverage["present_days_count"],
                day_coverage["missing_days_count"],
                f_base,
                f_net,
                f_down,
                f_causes,
                f_causes - f_down,
                f_cause_totals["repair_minutes"],
                f_cause_totals["no_load_minutes"],
                f_cause_totals["blast_delay_minutes"],
                f_cause_totals["no_loader_minutes"],
                f_cause_totals["driver_delay_minutes"],
                safe_pct(f_net, f_base),
                safe_pct(f_down, f_base),
                safe_pct(f_cause_totals["repair_minutes"], f_base),
                safe_pct(f_cause_totals["repair_minutes"], f_causes if f_causes > 0 else f_down),
                top_cause_key,
                top_cause_label,
                discrep_cnt,
                warn_cnt,
            ),
        )

        # 2. Monthly causes summary
        for ckey, clabel in CAUSE_FIELDS:
            cmin = f_cause_totals[ckey]
            aff_rows = sum(1 for r in records if r[ckey] > 0)
            aff_devs = len({r["canonical_device_key"] for r in records if r[ckey] > 0})
            conn.execute(
                """
                INSERT INTO summary_causes_monthly (
                    version_id, period, cause_key, cause_label, total_minutes,
                    share_of_causes_pct, share_of_base_pct, affected_rows, affected_devices
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    period,
                    ckey,
                    clabel,
                    cmin,
                    safe_pct(cmin, f_causes),
                    safe_pct(cmin, f_base),
                    aff_rows,
                    aff_devs,
                ),
            )

        # 3. Fleet daily summary
        for jdate, drows in sorted(by_day.items()):
            dbase = sum(r["base_minutes"] for r in drows)
            dnet = sum(r["reported_net_work_minutes"] for r in drows)
            ddown = sum(r["reported_total_downtime_minutes"] for r in drows)
            dcauses = sum(r["computed_causes_minutes"] for r in drows)
            conn.execute(
                """
                INSERT INTO summary_fleet_daily (
                    version_id, period, jalali_date, iso_date, sheet_name,
                    row_count, devices_count,
                    base_minutes, reported_net_work_minutes, reported_total_downtime_minutes,
                    computed_causes_minutes,
                    repair_minutes, no_load_minutes, blast_delay_minutes, no_loader_minutes, driver_delay_minutes,
                    work_pct, downtime_pct, discrepancy_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    period,
                    jdate,
                    drows[0]["iso_date"],
                    drows[0]["sheet_name"],
                    len(drows),
                    len({r["canonical_device_key"] for r in drows}),
                    dbase,
                    dnet,
                    ddown,
                    dcauses,
                    sum(r["repair_minutes"] for r in drows),
                    sum(r["no_load_minutes"] for r in drows),
                    sum(r["blast_delay_minutes"] for r in drows),
                    sum(r["no_loader_minutes"] for r in drows),
                    sum(r["driver_delay_minutes"] for r in drows),
                    safe_pct(dnet, dbase),
                    safe_pct(ddown, dbase),
                    sum(1 for r in drows if r["validation_status"] != "VALID"),
                ),
            )

        # 4. Device daily summary
        dev_daily_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for (jdate, dkey), ddrows in sorted(by_device_day.items()):
            dbase = sum(r["base_minutes"] for r in ddrows)
            dnet = sum(r["reported_net_work_minutes"] for r in ddrows)
            ddown = sum(r["reported_total_downtime_minutes"] for r in ddrows)
            dcauses = sum(r["computed_causes_minutes"] for r in ddrows)
            item = {
                "jalali_date": jdate,
                "iso_date": ddrows[0]["iso_date"],
                "day_num": int(jdate.split("/")[2]),
                "base_minutes": dbase,
                "reported_net_work_minutes": dnet,
                "reported_total_downtime_minutes": ddown,
                "computed_causes_minutes": dcauses,
            }
            dev_daily_map[dkey].append(item)
            conn.execute(
                """
                INSERT INTO summary_device_daily (
                    version_id, period, jalali_date, iso_date, canonical_device_key, raw_device_code,
                    row_count, base_minutes, reported_net_work_minutes, reported_total_downtime_minutes,
                    computed_causes_minutes,
                    repair_minutes, no_load_minutes, blast_delay_minutes, no_loader_minutes, driver_delay_minutes,
                    work_pct, downtime_pct, discrepancy_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    period,
                    jdate,
                    ddrows[0]["iso_date"],
                    dkey,
                    ddrows[0]["raw_device_code"],
                    len(ddrows),
                    dbase,
                    dnet,
                    ddown,
                    dcauses,
                    sum(r["repair_minutes"] for r in ddrows),
                    sum(r["no_load_minutes"] for r in ddrows),
                    sum(r["blast_delay_minutes"] for r in ddrows),
                    sum(r["no_loader_minutes"] for r in ddrows),
                    sum(r["driver_delay_minutes"] for r in ddrows),
                    safe_pct(dnet, dbase),
                    safe_pct(ddown, dbase),
                    sum(1 for r in ddrows if r["validation_status"] != "VALID"),
                ),
            )

        # 5. Device monthly summary
        for dkey, drows in sorted(by_device.items()):
            first = drows[0]
            daily_items = dev_daily_map[dkey]
            base_m = sum(r["base_minutes"] for r in drows)
            net_m = sum(r["reported_net_work_minutes"] for r in drows)
            down_m = sum(r["reported_total_downtime_minutes"] for r in drows)
            causes_m = sum(r["computed_causes_minutes"] for r in drows)
            c_totals = {k: sum(r[k] for r in drows) for k, _ in CAUSE_FIELDS}
            top_ck = max(c_totals, key=lambda k: c_totals[k]) if causes_m > 0 else None
            top_cl = CAUSE_LABEL_BY_KEY.get(top_ck) if top_ck else None

            days_with_data = len(daily_items)
            days_with_work = sum(1 for x in daily_items if x["reported_net_work_minutes"] > 0)
            days_with_down = sum(1 for x in daily_items if x["reported_total_downtime_minutes"] > 0 or x["computed_causes_minutes"] > 0)
            days_zero_work = sum(1 for x in daily_items if x["reported_net_work_minutes"] == 0)
            night_rows = sum(1 for r in drows if r.get("shift") == "شب")

            max_day = max(
                daily_items,
                key=lambda x: (x["reported_total_downtime_minutes"], x["computed_causes_minutes"], x["jalali_date"]),
            )
            max_down_m = max(max_day["reported_total_downtime_minutes"], max_day["computed_causes_minutes"])
            max_down_date = max_day["jalali_date"] if max_down_m > 0 else None

            streak_down = _max_streak_over_present_days(
                [(x["day_num"], x["reported_total_downtime_minutes"] > 0 or x["computed_causes_minutes"] > 0) for x in daily_items]
            )
            streak_zero = _max_streak_over_present_days(
                [(x["day_num"], x["reported_net_work_minutes"] == 0) for x in daily_items]
            )

            note_counts = Counter(
                r["notes_normalized"]
                for r in drows
                if r["notes_normalized"] and r["notes_normalized"] not in {"-", "_", "—"}
            )
            top_notes = [{"note": n, "count": c} for n, c in note_counts.most_common(5)]

            topic_counts = Counter()
            topic_labels: dict[str, str] = {}
            for r in drows:
                for t in r["note_topics"]:
                    topic_counts[t["topic_key"]] += 1
                    topic_labels[t["topic_key"]] = t["topic_label"]
            top_topics = [
                {"topic_key": k, "topic_label": topic_labels[k], "count": c}
                for k, c in topic_counts.most_common(6)
            ]

            conn.execute(
                """
                INSERT INTO summary_device_monthly (
                    version_id, period, canonical_device_key, raw_device_code, registry_canonical_code,
                    device_name, device_type, device_group, identity_status, ambiguous_candidates_json,
                    row_count, days_with_data, days_with_work, days_with_downtime, days_zero_work, night_shift_rows,
                    base_minutes, reported_net_work_minutes, reported_total_downtime_minutes, computed_causes_minutes,
                    repair_minutes, no_load_minutes, blast_delay_minutes, no_loader_minutes, driver_delay_minutes,
                    work_pct, downtime_pct, repair_pct_of_base, repair_pct_of_downtime,
                    top_cause_key, top_cause_label, max_daily_downtime_minutes, max_daily_downtime_date,
                    max_consecutive_downtime_days, max_consecutive_zero_work_days, discrepancy_count,
                    top_notes_json, top_topics_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                """,
                (
                    version_id,
                    period,
                    dkey,
                    first["raw_device_code"],
                    first["registry_canonical_code"],
                    first["device_name"],
                    first["device_type"],
                    first["device_group"],
                    first["identity_status"],
                    json.dumps(first["ambiguous_candidates"], ensure_ascii=False),
                    len(drows),
                    days_with_data,
                    days_with_work,
                    days_with_down,
                    days_zero_work,
                    night_rows,
                    base_m,
                    net_m,
                    down_m,
                    causes_m,
                    c_totals["repair_minutes"],
                    c_totals["no_load_minutes"],
                    c_totals["blast_delay_minutes"],
                    c_totals["no_loader_minutes"],
                    c_totals["driver_delay_minutes"],
                    safe_pct(net_m, base_m),
                    safe_pct(down_m, base_m),
                    safe_pct(c_totals["repair_minutes"], base_m),
                    safe_pct(c_totals["repair_minutes"], causes_m if causes_m > 0 else down_m),
                    top_ck,
                    top_cl,
                    max_down_m,
                    max_down_date,
                    streak_down,
                    streak_zero,
                    sum(1 for r in drows if r["validation_status"] != "VALID"),
                    json.dumps(top_notes, ensure_ascii=False),
                    json.dumps(top_topics, ensure_ascii=False),
                ),
            )

        # 6. Machine group summary (strictly separating confirmed vs ambiguous/unregistered groups)
        for gname, grows in sorted(by_group.items()):
            gbase = sum(r["base_minutes"] for r in grows)
            gnet = sum(r["reported_net_work_minutes"] for r in grows)
            gdown = sum(r["reported_total_downtime_minutes"] for r in grows)
            gcauses = sum(r["computed_causes_minutes"] for r in grows)
            grep = sum(r["repair_minutes"] for r in grows)
            confirmed = 1 if all(r["identity_status"] == "RESOLVED" for r in grows) else 0
            conn.execute(
                """
                INSERT INTO summary_group_monthly (
                    version_id, period, device_group, is_confirmed_group, devices_count, row_count,
                    base_minutes, reported_net_work_minutes, reported_total_downtime_minutes,
                    computed_causes_minutes,
                    repair_minutes, no_load_minutes, blast_delay_minutes, no_loader_minutes, driver_delay_minutes,
                    work_pct, downtime_pct, repair_pct_of_base
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    period,
                    gname,
                    confirmed,
                    len({r["canonical_device_key"] for r in grows}),
                    len(grows),
                    gbase,
                    gnet,
                    gdown,
                    gcauses,
                    grep,
                    sum(r["no_load_minutes"] for r in grows),
                    sum(r["blast_delay_minutes"] for r in grows),
                    sum(r["no_loader_minutes"] for r in grows),
                    sum(r["driver_delay_minutes"] for r in grows),
                    safe_pct(gnet, gbase),
                    safe_pct(gdown, gbase),
                    safe_pct(grep, gbase),
                ),
            )

        # 7. Monthly note topics summary
        topic_rows_map: dict[str, int] = Counter()
        topic_devs_map: dict[str, set[str]] = defaultdict(set)
        topic_lbl_map: dict[str, str] = {}
        for r in records:
            for t in r["note_topics"]:
                tk = t["topic_key"]
                topic_rows_map[tk] += 1
                topic_devs_map[tk].add(r["canonical_device_key"])
                topic_lbl_map[tk] = t["topic_label"]
        for tk, cnt in topic_rows_map.items():
            conn.execute(
                """
                INSERT INTO summary_note_topics_monthly (
                    version_id, period, topic_key, topic_label, mention_rows, affected_devices
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (version_id, period, tk, topic_lbl_map[tk], cnt, len(topic_devs_map[tk])),
            )

    def record_report_artifact(
        self,
        *,
        version_id: int,
        period: str,
        version_number: int,
        pdf_path: Path | str,
        pdf_sha256: str,
        page_count: int,
        byte_size: int,
        is_provisional: bool,
        reconciliation_ok: bool,
    ) -> dict[str, Any]:
        self.ensure_schema()
        now = utc_now()
        with closing(self._connect(write=True)) as conn, conn:
            conn.execute(
                """
                INSERT INTO report_artifacts (
                    version_id, period, version_number, pdf_path, pdf_sha256,
                    page_count, byte_size, is_provisional, reconciliation_ok, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(version_id, pdf_sha256) DO UPDATE SET
                    pdf_path = excluded.pdf_path,
                    page_count = excluded.page_count,
                    byte_size = excluded.byte_size,
                    is_provisional = excluded.is_provisional,
                    reconciliation_ok = excluded.reconciliation_ok,
                    generated_at = excluded.generated_at
                """,
                (
                    version_id,
                    period,
                    version_number,
                    str(pdf_path),
                    pdf_sha256,
                    page_count,
                    byte_size,
                    1 if is_provisional else 0,
                    1 if reconciliation_ok else 0,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM report_artifacts WHERE version_id = ? AND pdf_sha256 = ?",
                (version_id, pdf_sha256),
            ).fetchone()
            return dict(row)

    def get_latest_report_artifact(self, period: str, *, version_id: int | None = None) -> dict[str, Any] | None:
        if not self.db_path.exists():
            return None
        with closing(self._connect(write=False)) as conn:
            if version_id is not None:
                row = conn.execute(
                    "SELECT * FROM report_artifacts WHERE version_id = ? ORDER BY artifact_id DESC LIMIT 1",
                    (version_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT a.* FROM report_artifacts a
                    JOIN import_versions v ON v.version_id = a.version_id
                    WHERE a.period = ? AND v.is_active = 1
                    ORDER BY a.artifact_id DESC LIMIT 1
                    """,
                    (period,),
                ).fetchone()
            return dict(row) if row else None
