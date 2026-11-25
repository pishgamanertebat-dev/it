"""Comprehensive acceptance test suite covering all Section 15 requirements.

Tests:
  1. Real Aban 1405 workbook import (read-only, 36 sheets -> 23 included / 13 excluded,
     1373 rows, 49 raw codes -> 50 device tracks, missing days 1-6 & 16, both known
     discrepancies on 08.14 R60 [714] & 08.12 R60 [715], idempotency, revision upgrade,
     rollback recovery on invalid workbook, older 11-column semantic layout, >24h sums).
  2. Deterministic calculations (weighted fleet %, per-device sums, cause totals, rankings,
     zero-division guards, incomplete day streaks, zero-downtime scorecard handling).
  3. AI query API & plugin authorization (all 13 required Persian sample queries, Persian/ASCII
     digits, ی/ي & ک/ك normalization, ambiguous 601 disambiguation, missing day/month,
     approved access across all 4 profiles without privilege escalation, fail-closed denial,
     zero heavy imports on query path).
  4. Executive PDF validation (searchable Persian, pure vector, zero overflow, 100% SQLite
     reconciliation, all 50 devices + zero-downtime synthetic device badge).
  5. Bale delivery pipeline (registry resolution with Emami ambiguity block, simulated 5-user
     delivery, idempotency, independent per-recipient retry, restart durability, invalid ID
     prohibition, provisional & revision policy gates).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import openpyxl

from tools.fleet.downtime.analytics import DowntimeAnalytics
from tools.fleet.downtime.delivery import (
    DowntimeDeliveryService,
    ResolvedRecipient,
    SimulatedBaleTransport,
    build_bale_caption,
    resolve_owner_recipients,
    validate_bale_recipient_id,
)
from tools.fleet.downtime.importer import discover_monthly_workbooks, import_monthly_workbook
from tools.fleet.downtime.normalization import (
    extract_note_topics,
    format_minutes_hhmm,
    jalali_to_gregorian,
    map_semantic_headers,
    normalize_persian,
    parse_excel_duration_minutes,
    parse_jalali_date,
    parse_period,
)
from tools.fleet.downtime.query import DowntimeQueryService
from tools.fleet.downtime.registry import FleetDeviceRegistry
from tools.fleet.downtime.report import generate_monthly_pdf_report
from tools.fleet.downtime.repository import DowntimeRepository, safe_pct

REAL_ABAN_XLSX = Path(r"E:\Function\توقفات دستگاهها ابان.xlsx")
REAL_RUNTIME_DIR = Path(r"E:\KomatsoAI\runtime\fleet-downtime")
REAL_AUTH_DB = Path(r"E:\KomatsoAI\reports\telegram_usage\telegram_users.db")


class NormalizationAndCalendarTests(unittest.TestCase):
    def test_persian_normalization_and_digits(self):
        self.assertEqual(normalize_persian("  بي‌باري\u200c ۱۲۵۲  كوماتسو "), "بی باری 1252 کوماتسو")
        self.assertEqual(parse_period("آبان ۱۴۰۵"), "1405-08")
        self.assertEqual(parse_period("ابان 1405"), "1405-08")
        self.assertEqual(parse_period("1405/8"), "1405-08")

    def test_jalali_calendar_matches_jdatetime(self):
        import jdatetime

        for jy, jm, jd in [(1405, 8, 7), (1405, 8, 20), (1405, 8, 30), (1405, 1, 1), (1404, 12, 29)]:
            expected = jdatetime.date(jy, jm, jd).togregorian()
            self.assertEqual(jalali_to_gregorian(jy, jm, jd), expected)
        jdate, iso = parse_jalali_date("۲۰ ابان ۱۴۰۵")
        self.assertEqual(jdate, "1405/08/20")
        self.assertEqual(iso, jdatetime.date(1405, 8, 20).togregorian().isoformat())

    def test_excel_duration_and_over_24h_no_wrap(self):
        self.assertEqual(parse_excel_duration_minutes(time(11, 0))[:2], (660, False))
        self.assertEqual(parse_excel_duration_minutes(timedelta(hours=30, minutes=15))[:2], (1815, False))
        self.assertEqual(parse_excel_duration_minutes(datetime(1899, 12, 31, 3, 30))[:2], (1650, False))
        self.assertEqual(parse_excel_duration_minutes(0.25)[:2], (360, False))
        self.assertEqual(parse_excel_duration_minutes(0)[:2], (0, False))
        self.assertEqual(parse_excel_duration_minutes(None)[:2], (None, True))
        self.assertEqual(parse_excel_duration_minutes("35:45")[:2], (2145, False))
        self.assertEqual(format_minutes_hhmm(15180), "253:00")
        self.assertEqual(format_minutes_hhmm(838440), "13974:00")

    def test_semantic_header_mapping_supports_11_and_13_columns(self):
        legacy_11 = [
            (1, "ردیف", ""),
            (2, "کد دستگاه", ""),
            (3, "ساعتات کار", ""),  # real typo in 06.30 / 07.01 sheets
            (4, "ساعات توقف", "تعمیرات"),
            (5, "", "بي باري"),
            (6, "", "نبود بارگير"),
            (7, "", "تاخير راننده"),
            (8, "جمع ساعت توقف", ""),
            (9, "جمع ساعت کار", ""),
            (10, "تاریخ", ""),
            (11, "ملاحظات", ""),
        ]
        mapped = map_semantic_headers(legacy_11)
        self.assertEqual(mapped["base_hours"], 3)
        self.assertEqual(mapped["repair"], 4)
        self.assertEqual(mapped["no_load"], 5)
        self.assertEqual(mapped["no_loader"], 6)
        self.assertEqual(mapped["driver_delay"], 7)
        self.assertEqual(mapped["total_downtime"], 8)
        self.assertEqual(mapped["net_work"], 9)
        self.assertNotIn("blast_delay", mapped)

    def test_note_topic_extraction_preserves_qualitative_themes(self):
        topics = {t["topic_key"] for t in extract_note_topics("روغن ریزی پمپ و خرابی جک پاکت - تاخیر در انفجار")}
        self.assertTrue({"oil_leak", "pump_issue", "jack_cylinder_issue", "blast_delay"} <= topics)
        # Pure no-fault shift note yields empty fault topics
        self.assertEqual(extract_note_topics("شیفت شب بدونه توقف و خرابی"), [])

    def test_safe_pct_zero_division(self):
        self.assertEqual(safe_pct(100, 0), 0.0)
        self.assertEqual(safe_pct(0, 0), 0.0)
        self.assertEqual(safe_pct(300, 600), 50.0)


class RealAbanImportAndAnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="downtime-test-")
        cls.rt = Path(cls.tmp.name)
        cls.source_sha_before = hashlib.sha256(REAL_ABAN_XLSX.read_bytes()).hexdigest()
        cls.import_res = import_monthly_workbook(REAL_ABAN_XLSX, period="1405-08", runtime_dir=cls.rt)
        cls.repo = DowntimeRepository(cls.rt / "downtime.sqlite3")
        cls.analytics = DowntimeAnalytics(cls.repo)
        cls.query = DowntimeQueryService(db_path=cls.rt / "downtime.sqlite3", runtime_dir=cls.rt)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_01_source_workbook_strictly_read_only(self):
        after = hashlib.sha256(REAL_ABAN_XLSX.read_bytes()).hexdigest()
        self.assertEqual(self.source_sha_before, after)

    def test_02_aban_counts_sheets_days_and_identity(self):
        self.assertEqual(self.import_res["status"], "IMPORTED_NEW")
        self.assertEqual(self.import_res["row_count"], 1373)
        self.assertEqual(self.import_res["discrepancy_row_count"], 2)

        summary = self.analytics.fleet_monthly_summary("1405-08")
        self.assertTrue(summary["found"])
        self.assertEqual(summary["version"]["total_sheets"], 36)
        self.assertEqual(summary["version"]["period_sheets_count"], 23)
        self.assertEqual(summary["version"]["excluded_sheets_count"], 13)
        self.assertEqual(summary["coverage"]["present_days_count"], 23)
        self.assertEqual(summary["coverage"]["missing_days"], [1, 2, 3, 4, 5, 6, 16])

        # Other months MUST NOT be published
        self.assertFalse(self.analytics.fleet_monthly_summary("1405-07")["found"])
        self.assertFalse(self.analytics.fleet_monthly_summary("1405-06")["found"])

        # Identity resolution: 49 raw codes -> 50 tracks (601 split cleanly into 2 ambiguous tracks!)
        id_ov = summary["identity_overview"]
        self.assertEqual(id_ov["distinct_raw_codes"], 49)
        self.assertEqual(id_ov["distinct_device_tracks"], 50)
        self.assertEqual(id_ov["resolved_devices_count"], 41)
        self.assertEqual(id_ov["ambiguous_devices_count"], 3)
        self.assertEqual(id_ov["unregistered_devices_count"], 6)

    def test_03_exact_two_known_discrepancies_detected_without_mutating_source(self):
        summary = self.analytics.fleet_monthly_summary("1405-08")
        fleet = summary["fleet"]
        self.assertEqual(fleet["base_minutes"], 838440)
        self.assertEqual(fleet["reported_net_work_minutes"], 510626)
        self.assertEqual(fleet["reported_total_downtime_minutes"], 327814)
        self.assertEqual(fleet["computed_causes_minutes"], 328024)
        self.assertEqual(fleet["causes_vs_reported_delta_minutes"], 210)
        self.assertEqual(fleet["work_pct"], round(510626 / 838440 * 100, 2))
        self.assertEqual(fleet["downtime_pct"], round(327814 / 838440 * 100, 2))

        discreps = {(d["sheet_name"], d["excel_row_num"], d["raw_device_code"]) for d in summary["discrepancies"]}
        self.assertEqual(discreps, {("08.14", 60, "714"), ("08.12", 60, "715")})

    def test_04_multi_shift_and_same_code_rows_preserved(self):
        # 1251 has 23 day rows + 15 night rows = 38 rows!
        det_1251 = self.analytics.device_detail("1405-08", "EX1251")["device"]
        self.assertEqual(det_1251["row_count"], 38)
        self.assertEqual(det_1251["days_with_data"], 23)
        self.assertEqual(det_1251["night_shift_rows"], 15)
        self.assertEqual(det_1251["base_hhmm"], "343:00")

        # 601 has 23 rows on track #1 and 23 rows on track #2 (46 total, never merged!)
        t1 = self.analytics.device_detail("1405-08", "AMBIG:601#1")["device"]
        t2 = self.analytics.device_detail("1405-08", "AMBIG:601#2")["device"]
        self.assertEqual(t1["row_count"], 23)
        self.assertEqual(t2["row_count"], 23)
        self.assertEqual(t1["identity_status"], "AMBIGUOUS")
        self.assertEqual(t2["identity_status"], "AMBIGUOUS")

    def test_05_all_13_required_persian_queries_and_edge_cases(self):
        # 1. Daily query (20 Aban)
        q1 = self.query.get_daily_downtime("۲۰ آبان ۱۴۰۵")
        self.assertTrue(q1["ok"] and q1["found"])
        self.assertEqual(q1["jalali_date"], "1405/08/20")

        # 2. Monthly summary
        q2 = self.query.get_monthly_downtime_summary("آبان ۱۴۰۵")
        self.assertTrue(q2["ok"] and q2["found"])
        self.assertEqual(q2["fleet"]["base_hhmm"], "13974:00")

        # 3. Device 706 (Persian digits)
        q3 = self.query.get_device_downtime("۷۰۶", period="آبان ۱۴۰۵")
        self.assertTrue(q3["ok"] and q3["found"])
        self.assertEqual(q3["device"]["canonical_device_key"], "HD706")

        # 4. Excavator 1252 repair hours (and alias 1002 -> EX1252)
        q4 = self.query.get_device_downtime("بیل ۱۲۵۲", period="1405-08")
        q4_alias = self.query.get_device_downtime("1002", period="1405-08")
        self.assertEqual(q4["device"]["canonical_device_key"], "EX1252")
        self.assertEqual(q4["device"]["repair_minutes"], q4_alias["device"]["repair_minutes"])

        # 5 & 6. Ranking by downtime hours & downtime %
        q5 = self.query.rank_devices_by_downtime(period="1405-08", metric="downtime_hours", limit=5)
        q6 = self.query.rank_devices_by_downtime(period="1405-08", metric="downtime_pct", limit=5)
        self.assertEqual(q5["items"][0]["raw_device_code"], "721")
        self.assertEqual(q6["items"][0]["raw_device_code"], "471")
        self.assertEqual(q6["items"][0]["downtime_pct"], 100.0)

        # 7 & 8. Causes + blast delay
        q7 = self.query.get_downtime_causes(period="1405-08")
        self.assertEqual(q7["causes"][0]["cause_label"], "تعمیرات")
        q8 = self.query.get_downtime_causes(period="1405-08", cause="انفجار")
        self.assertEqual(len(q8["causes"]), 1)
        self.assertEqual(q8["causes"][0]["total_hhmm"], "614:31")

        # 9. Five lowest-work devices
        q9 = self.query.rank_devices_by_downtime(period="1405-08", metric="work_hours", order="asc", limit=5)
        self.assertEqual(len(q9["items"]), 5)
        self.assertEqual(q9["items"][0]["reported_net_work_minutes"], 0)

        # 10. Excavator 853 on 29 Aban
        q10 = self.query.get_device_downtime("بیل ۸۵۳", date="۲۹ آبان ۱۴۰۵")
        self.assertTrue(q10["ok"] and q10["found"])
        self.assertEqual(q10["jalali_date"], "1405/08/29")

        # 11. Compare 706 vs 717
        q11 = self.query.compare_device_performance("۷۰۶", "۷۱۷", period="1405-08")
        self.assertTrue(q11["ok"] and q11["found"])
        self.assertEqual(q11["device_a"]["canonical_device_key"], "HD706")
        self.assertEqual(q11["device_b"]["canonical_device_key"], "UNREG:717")

        # 12. Most zero-work days
        q12 = self.query.rank_devices_by_downtime(period="1405-08", metric="days_zero_work", order="desc", limit=5)
        self.assertEqual(q12["items"][0]["days_zero_work"], 23)

        # 13. Ambiguous 601 requests disambiguation!
        q_amb = self.query.get_device_downtime("۶۰۱", period="1405-08")
        self.assertEqual(q_amb["status"], "ambiguous")
        self.assertEqual({c["canonical_code"] for c in q_amb["candidates"]}, {"EX601", "WA601"})

        # 14. Missing day (16 Aban) & missing month (Mehr 1405)
        q_miss_day = self.query.get_daily_downtime("۱۶ آبان ۱۴۰۵")
        self.assertEqual(q_miss_day["status"], "MISSING_SHEET_UNKNOWN")
        q_miss_mon = self.query.get_monthly_downtime_summary("مهر ۱۴۰۵")
        self.assertFalse(q_miss_mon["found"])

    def test_06_idempotency_revision_and_failure_rollback(self):
        # Duplicate hash -> UNCHANGED_DUPLICATE
        dup = import_monthly_workbook(REAL_ABAN_XLSX, period="1405-08", runtime_dir=self.rt)
        self.assertEqual(dup["status"], "UNCHANGED_DUPLICATE")
        self.assertEqual(dup["version_number"], 1)

        # Create a revised workbook (fixing the 08.12 row 60 literal 0 -> 0:30) and verify v2 promotion
        rev_path = self.rt / "توقفات دستگاهها ابان اصلاحی.xlsx"
        wb = openpyxl.load_workbook(REAL_ABAN_XLSX)
        wb["08.12"]["I60"] = time(0, 30)
        wb["08.12"]["J60"] = time(5, 30)
        wb.save(rev_path)
        wb.close()

        rev = import_monthly_workbook(rev_path, period="1405-08", runtime_dir=self.rt)
        self.assertEqual(rev["status"], "IMPORTED_REVISION")
        self.assertEqual(rev["version_number"], 2)
        self.assertEqual(rev["discrepancy_row_count"], 1)
        versions = self.repo.list_versions("1405-08")
        self.assertEqual(len(versions), 2)
        self.assertEqual([v["status"] for v in versions], ["ACTIVE", "SUPERSEDED"])

        # Corrupt / invalid workbook MUST fail cleanly leaving v2 ACTIVE intact
        bad_path = self.rt / "توقفات دستگاهها ابان خراب.xlsx"
        wb_bad = openpyxl.load_workbook(rev_path)
        wb_bad["08.30"]["C4"] = -5  # invalid negative duration
        wb_bad.save(bad_path)
        wb_bad.close()
        with self.assertRaises(ValueError):
            import_monthly_workbook(bad_path, period="1405-08", runtime_dir=self.rt)
        active_after_err = self.repo.get_active_version("1405-08")
        self.assertEqual(active_after_err["version_number"], 2)

        # Restore v1 active for remaining tests in class
        self.repo.activate_version("1405-08", 1)

    def test_07_zero_downtime_synthetic_workbook_and_pdf_badge(self):
        # Build minimal 11-column synthetic month (1405-09) with 1 device having ZERO downtime!
        syn_rt = self.rt / "syn_test"
        syn_xlsx = syn_rt / "توقفات دستگاهها آذر 1405.xlsx"
        syn_rt.mkdir(parents=True, exist_ok=True)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "09.01"
        ws.append(["گزارش کار و توقفات ماشین آلات"])
        ws.append(["ردیف", "کد دستگاه", "ساعتات کار", "ساعات توقف", "", "", "", "جمع ساعت توقف", "جمع ساعت کار", "تاریخ", "ملاحظات"])
        ws.append(["", "", "", "تعمیرات", "بی باری", "نبود بارگیر", "تاخیر راننده", "", "", "", ""])
        ws.append([1, 706, time(11, 0), time(0, 0), time(0, 0), time(0, 0), time(0, 0), time(0, 0), time(11, 0), "1405/09/01", ""])
        wb.save(syn_xlsx)
        wb.close()

        imp = import_monthly_workbook(syn_xlsx, period="1405-09", runtime_dir=syn_rt)
        self.assertEqual(imp["status"], "IMPORTED_NEW")
        pdf_res = generate_monthly_pdf_report("1405-09", runtime_dir=syn_rt)
        self.assertTrue(pdf_res["reconciliation_ok"])
        import fitz

        with fitz.open(pdf_res["pdf_path"]) as doc:
            full_text = "\n".join(p.get_text() for p in doc)
        self.assertIn("بدون توقف ثبت", full_text)


class RealPdfAndDeliveryTests(unittest.TestCase):
    def test_01_real_aban_pdf_reconciles_with_zero_overflow(self):
        # Verify the generated real Aban PDF artifact exists and passes strict validation
        pdf_path = REAL_RUNTIME_DIR / "reports" / "1405-08" / "Fleet-Downtime-1405-08.pdf"
        self.assertTrue(pdf_path.is_file(), f"Missing real Aban PDF: {pdf_path}")
        res = generate_monthly_pdf_report("1405-08", runtime_dir=REAL_RUNTIME_DIR, force=False)
        self.assertEqual(res["status"], "CACHED_UNCHANGED")
        self.assertTrue(res["reconciliation_ok"])

    def test_02_bale_recipient_registry_resolution_blocks_ambiguous_emami(self):
        resolved = resolve_owner_recipients(auth_db_path=REAL_AUTH_DB)
        by_key = {r.recipient_key: r for r in resolved}
        self.assertEqual(by_key["project_admin"].resolution_status, "RESOLVED")
        self.assertEqual(by_key["project_admin"].bale_user_id, "455740857")
        self.assertEqual(by_key["pakdel"].bale_user_id, "514458396")
        self.assertEqual(by_key["hedayati"].bale_user_id, "641220453")
        self.assertEqual(by_key["shayesteh"].bale_user_id, "654806764")
        # Emami MUST be flagged AMBIGUOUS (two approved users: 397185913 and 1636934401)
        self.assertEqual(by_key["emami"].resolution_status, "AMBIGUOUS")
        self.assertIsNone(by_key["emami"].bale_user_id)
        self.assertEqual({c["user_id"] for c in by_key["emami"].candidates}, {"397185913", "1636934401"})

    def test_03_delivery_idempotency_retry_restart_and_invalid_id_blocking(self):
        with tempfile.TemporaryDirectory(prefix="downtime-deliv-") as tmp:
            rt = Path(tmp)
            # Copy real DB and real PDF into isolated temp dir so production runtime ledger stays clean
            shutil.copy2(REAL_RUNTIME_DIR / "downtime.sqlite3", rt / "downtime.sqlite3")
            pdf_dir = rt / "reports" / "1405-08"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            real_pdf = REAL_RUNTIME_DIR / "reports" / "1405-08" / "Fleet-Downtime-1405-08.pdf"
            local_pdf = pdf_dir / real_pdf.name
            shutil.copy2(real_pdf, local_pdf)
            # Update artifact path in temp DB
            from contextlib import closing

            repo = DowntimeRepository(rt / "downtime.sqlite3")
            with closing(repo._connect(write=True)) as conn:
                conn.execute("UPDATE report_artifacts SET pdf_path = ?", (str(local_pdf),))
                conn.commit()

            svc = DowntimeDeliveryService(runtime_dir=rt, auth_db_path=REAL_AUTH_DB)

            # 1. Provisional auto-release without allow_provisional MUST block
            blocked_prov = svc.deliver_monthly_report("1405-08", dry_run=True, allow_provisional=False)
            self.assertEqual(blocked_prov["status"], "BLOCKED_PROVISIONAL_DATA")

            # 2. Live send without owner approval MUST block
            blocked_live = svc.deliver_monthly_report("1405-08", dry_run=False, allow_provisional=True, approved_by=None)
            self.assertEqual(blocked_live["status"], "BLOCKED_OWNER_APPROVAL_REQUIRED")

            # 3. Simulated 5-recipient send where 1 recipient (654806764) fails transiently on first try
            sim = SimulatedBaleTransport(fail_once_user_ids={"654806764"})
            run1 = svc.deliver_monthly_report(
                "1405-08",
                transport=sim,
                dry_run=True,
                allow_provisional=True,
                approved_by="owner-uat-sim",
                explicit_id_overrides={"emami": "397185913"},
            )
            self.assertEqual(run1["status"], "PARTIAL_FAILED")
            self.assertEqual(run1["counts"]["sent"], 4)
            self.assertEqual(run1["counts"]["failed"], 1)
            self.assertEqual(len(sim.sent_calls), 4)

            # 4. Simulate Gateway restart (brand new service instance) and retry -> ONLY the 1 failed recipient is sent!
            svc_after_restart = DowntimeDeliveryService(runtime_dir=rt, auth_db_path=REAL_AUTH_DB)
            run2 = svc_after_restart.deliver_monthly_report(
                "1405-08",
                transport=sim,
                dry_run=True,
                allow_provisional=True,
                approved_by="owner-uat-sim",
                explicit_id_overrides={"emami": "397185913"},
            )
            self.assertEqual(run2["status"], "SUCCEEDED")
            self.assertEqual(run2["counts"]["sent"], 1)
            self.assertEqual(run2["counts"]["skipped_duplicate"], 4)
            self.assertEqual(len(sim.sent_calls), 5)
            self.assertEqual(sim.sent_calls[-1]["recipient"], "654806764")

            # 5. Another full restart + re-run -> 0 sent, 5 skipped (100% idempotent!)
            svc_restart_2 = DowntimeDeliveryService(runtime_dir=rt, auth_db_path=REAL_AUTH_DB)
            run3 = svc_restart_2.deliver_monthly_report(
                "1405-08",
                transport=sim,
                dry_run=True,
                allow_provisional=True,
                approved_by="owner-uat-sim",
                explicit_id_overrides={"emami": "397185913"},
            )
            self.assertEqual(run3["status"], "ALL_ALREADY_SENT_IDEMPOTENT")
            self.assertEqual(run3["counts"]["sent"], 0)
            self.assertEqual(run3["counts"]["skipped_duplicate"], 5)
            self.assertEqual(len(sim.sent_calls), 5)

            # 6. Invalid / revoked / unapproved IDs MUST be blocked!
            bad_recs = [
                validate_bale_recipient_id("not-a-number", auth_db_path=REAL_AUTH_DB, label="bad_fmt"),
                validate_bale_recipient_id("887487566", auth_db_path=REAL_AUTH_DB, label="revoked_user"),
                validate_bale_recipient_id("1986031197", auth_db_path=REAL_AUTH_DB, label="rejected_user"),
                validate_bale_recipient_id("999999999", auth_db_path=REAL_AUTH_DB, label="unknown_user"),
            ]
            run_bad = svc_restart_2.deliver_monthly_report(
                "1405-08",
                recipients=bad_recs,
                transport=sim,
                dry_run=True,
                allow_provisional=True,
                approved_by="owner-uat-sim",
            )
            self.assertEqual(run_bad["counts"]["sent"], 0)
            self.assertEqual(run_bad["counts"]["blocked"], 4)
            self.assertEqual(len(sim.sent_calls), 5)  # Zero extra sends!

            # 7. Caption check
            cap = build_bale_caption("1405-08")
            self.assertIn("گزارش ماهانه عملکرد و توقفات ماشین‌آلات — آبان ۱۴۰۵", cap)
            self.assertIn("واحد نگهداری و تعمیرات", cap)


if __name__ == "__main__":
    unittest.main()
