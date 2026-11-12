"""Isolated regression tests. No business files or real SMB writes."""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("mine_file_sync.next.py")
if not SCRIPT.exists():
    SCRIPT = SCRIPT.with_name("mine_file_sync.py")
spec = importlib.util.spec_from_file_location("mine_sync", SCRIPT)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class MineSyncTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[3] / 'runtime/mine-sync-tests'
        scratch.mkdir(parents=True, exist_ok=True)
        self.sandbox = tempfile.TemporaryDirectory(prefix="mine-sync-test-", dir=scratch)
        self.root = Path(self.sandbox.name)
        self.source = self.root / "source"
        self.destination = self.root / "destination"
        self.home = self.root / "home"
        self.source.mkdir()
        self.destination.mkdir()
        self.home.mkdir()
        sync.setup_logging(self.home)

    def tearDown(self):
        for handler in sync.LOG.handlers[:]:
            handler.close()
            sync.LOG.removeHandler(handler)
        self.sandbox.cleanup()

    def add(self, name, contents=b"initial"):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def scan(self):
        result = sync.empty_result()
        sync.sync_tree(self.source, self.destination, result, lambda **kw: None)
        return result

    def cli(self, *extra):
        result = subprocess.run([sys.executable, str(SCRIPT), "--source", str(self.source),
                               "--dest", str(self.destination), "--home", str(self.home), *extra],
                              capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            log = self.home / "logs/mine-file-sync.log"
            print("FAILED CLI", result.stdout, result.stderr,
                  log.read_text(encoding="utf-8") if log.exists() else "no log")
        return result

    def test_recursive_filters_and_colliding_names(self):
        for name in ["one.xlsx", "two.xls", "three.xlsm", "four.xlsb", "nested/one.pdf",
                     "a/same.xlsx", "b/same.xlsx", "فارسی/گزارش.PDF"]:
            self.add(name)
        for name in ["~$hidden.xlsx", "nested/~$lock.xlsm", "skip.txt", "skip.xltx"]:
            self.add(name)
        result = self.scan()
        self.assertEqual((result["scanned"], result["copied"], result["ignored"]), (8, 8, 4))
        self.assertTrue((self.destination / "گزارش.PDF").exists())
        self.assertEqual(len(list(self.destination.glob("same__mine_*.xlsx"))), 2)
        self.assertFalse(any(p.is_dir() for p in self.destination.iterdir()))
        self.assertFalse((self.destination / "same.xlsx").exists())

    def test_idempotent_size_and_nanosecond_mtime(self):
        src = self.add("one.xlsx")
        self.scan()
        dest = self.destination / src.name
        before = dest.stat()
        with patch.object(sync, "atomic_copy", side_effect=AssertionError("unneeded copy")):
            result = self.scan()
        self.assertEqual((result["copied"], result["updated"], result["skipped"]), (0, 0, 1))
        self.assertEqual(sync.signature(src.stat()), sync.signature(dest.stat()))
        self.assertEqual(before.st_mtime_ns, dest.stat().st_mtime_ns)

    def test_source_is_writable_during_slow_maintenance_publication(self):
        src = self.add('maintenance.xlsx')
        dst = self.destination / src.name
        def publish(staged, destination, **kwargs):
            # Windows denies this if Sync still holds its deny-write handle.
            with src.open('r+b') as writer:
                self.assertEqual(writer.read(), b'initial')
            destination.write_bytes(staged.read_bytes())
            return {'status': 'imported'}
        with patch.object(sync, 'MAINTENANCE_REPORT', dst), patch.object(sync, 'publish_fault_report', publish):
            sync.atomic_copy(src, dst, src.stat(), lambda **kw: None)
        self.assertEqual(dst.read_bytes(), b'initial')

    def test_update_size_and_same_size_small_mtime_change(self):
        src = self.add("one.xlsx", b"AAAA")
        self.scan()
        stamp = src.stat().st_mtime_ns
        src.write_bytes(b"BBBB")
        os.utime(src, ns=(stamp, stamp + 500_000_000))
        result = self.scan()
        self.assertEqual(result["updated"], 1)
        self.assertEqual((self.destination / src.name).read_bytes(), b"BBBB")
        src.write_bytes(b"larger")
        self.assertEqual(self.scan()["updated"], 1)

    def test_no_delete_and_no_reverse_sync(self):
        src = self.add("one.pdf")
        self.scan()
        src.unlink()
        local = self.destination / "local-only.pdf"
        local.write_bytes(b"local")
        self.assertEqual(self.scan()["copied"], 0)
        self.assertTrue((self.destination / "one.pdf").exists())
        self.assertFalse((self.source / "local-only.pdf").exists())

    def test_change_before_open_keeps_old(self):
        src = self.add("one.xlsx")
        self.scan()
        before = src.stat()
        src.write_bytes(b"changed")
        dest = self.destination / src.name
        with self.assertRaises(sync.SourceChanged):
            sync.atomic_copy(src, dest, before, lambda **kw: None)
        self.assertEqual(dest.read_bytes(), b"initial")
        self.assertEqual(list(self.destination.glob(".mine-sync-*.partial")), [])

    def test_change_after_copy_keeps_old(self):
        src = self.add("one.pdf")
        self.scan()
        src.write_bytes(b"new-version")
        before = src.stat()
        original = Path.stat

        def changed(path, *args, **kwargs):
            if path == src and kwargs.get("follow_symlinks", True):
                return types.SimpleNamespace(st_size=before.st_size,
                                             st_mtime_ns=before.st_mtime_ns + 1000)
            return original(path, *args, **kwargs)

        with patch.object(Path, "stat", changed):
            with self.assertRaises(sync.SourceChanged):
                sync.atomic_copy(src, self.destination / src.name, before, lambda **kw: None)
        self.assertEqual((self.destination / src.name).read_bytes(), b"initial")
        self.assertEqual(list(self.destination.glob(".mine-sync-*.partial")), [])

    def test_failed_replace_keeps_old(self):
        src = self.add("one.xls")
        self.scan()
        src.write_bytes(b"new-version")
        with patch.object(sync.os, "replace", side_effect=PermissionError("destination locked")):
            result = self.scan()
        self.assertEqual(result["skipped_locked"], 1)
        self.assertEqual((self.destination / src.name).read_bytes(), b"initial")
        self.assertEqual(list(self.destination.glob(".mine-sync-*.partial")), [])

    def test_real_windows_source_writer_lock_and_retry(self):
        src = self.add("one.xlsx")
        self.scan()
        src.write_bytes(b"updated")
        with src.open("r+b"):
            result = self.scan()
        self.assertEqual(result["skipped_locked"], 1)
        self.assertEqual((self.destination / src.name).read_bytes(), b"initial")
        self.assertEqual(self.scan()["updated"], 1)

    def test_real_windows_destination_reader_lock_and_retry(self):
        src = self.add("one.xlsx")
        self.scan()
        src.write_bytes(b"updated")
        dest = self.destination / src.name
        with sync.readonly_source(dest):
            result = self.scan()
        self.assertEqual(result["skipped_locked"], 1)
        self.assertEqual(dest.read_bytes(), b"initial")
        self.assertEqual(self.scan()["updated"], 1)

    def test_partial_destination_never_published(self):
        src = self.add("one.pdf", b"initial")
        self.scan()
        src.write_bytes(b"new" * 1024 * 1024)
        observed = []
        def observe(**kwargs):
            observed.append((self.destination / src.name).read_bytes())
        sync.atomic_copy(src, self.destination / src.name, src.stat(), observe)
        self.assertTrue(all(value == b"initial" for value in observed))
        self.assertEqual((self.destination / src.name).read_bytes(), src.read_bytes())

    def test_supervised_cli_silent_and_state(self):
        self.add("nested/one.xlsx")
        first = self.cli()
        self.assertEqual((first.returncode, first.stdout, first.stderr), (0, b"", b""))
        second = self.cli("--report")
        report = json.loads(second.stdout)
        self.assertEqual((report["scanned"], report["copied"], report["skipped"]), (1, 0, 1))
        state = json.loads((self.home / "state/mine-file-sync.json").read_text())
        self.assertTrue(state["last_successful_run"])
        self.assertEqual(len(state["recent_runs"]), 2)
        self.assertEqual(list((self.home / "state").glob("mine-sync-run-*")), [])

    def test_corrupt_state_rebuild_and_unknown_keys_preserved(self):
        self.add("one.pdf")
        state = self.home / "state/mine-file-sync.json"
        state.parent.mkdir()
        state.write_text("{broken")
        self.assertEqual(self.cli().returncode, 0)
        recovered = json.loads(state.read_text())
        recovered["custom_legacy_key"] = {"keep": 1}
        state.write_text(json.dumps(recovered))
        self.assertEqual(self.cli().returncode, 0)
        self.assertEqual(json.loads(state.read_text())["custom_legacy_key"], {"keep": 1})

    def test_missing_share_outage_recovery_and_no_delete(self):
        src = self.add("one.pdf")
        self.cli()
        missing = self.root / "missing"
        process = self.cli("--source", str(missing), "--report")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stdout)["status"], "source_unavailable")
        state = json.loads((self.home / "state/mine-file-sync.json").read_text())
        self.assertTrue(state["outage"])
        self.assertTrue((self.destination / src.name).exists())
        process = self.cli("--report")
        self.assertEqual(json.loads(process.stdout)["status"], "success")
        state = json.loads((self.home / "state/mine-file-sync.json").read_text())
        self.assertFalse(state["outage"])
        self.assertTrue(state["last_recovery_at"])

    def test_two_process_lock_and_orphan_worker_lock(self):
        for name in ["mine-file-sync.lock", "mine-file-sync-worker.lock"]:
            lock = sync.acquire_lock(self.home / "state" / name)
            try:
                started = time.monotonic()
                process = self.cli("--report")
                self.assertEqual(process.returncode, 0)
                self.assertEqual(json.loads(process.stdout)["status"], "already_running")
                self.assertLess(time.monotonic() - started, 3)
            finally:
                sync.release_lock(lock)

    def test_bounded_tcp_probe(self):
        with patch.object(sync.socket, "create_connection", side_effect=TimeoutError("offline")) as probe:
            with self.assertRaises(TimeoutError):
                sync.reachable(Path(r"\\192.168.20.251\share"))
        probe.assert_called_once_with(("192.168.20.251", 445), timeout=2.0)

    def test_stalled_worker_is_killed_and_unlocks(self):
        real_popen = subprocess.Popen
        def stalled(command, **kwargs):
            report = command[command.index("--worker-result") + 1]
            payload = "import pathlib,time; pathlib.Path(" + repr(report) + ").write_text(" + repr(json.dumps(sync.empty_result())) + "); time.sleep(60)"
            return real_popen([sys.executable, "-c", payload], **kwargs)
        args = argparse.Namespace(home=self.home, source=self.source,
                                  dest=self.destination, dry_run=False)
        started = time.monotonic()
        with patch.object(sync.subprocess, "Popen", stalled), patch.object(sync, "STALL_SECONDS", 0.5):
            result = sync.supervise(args)
        self.assertEqual(result["status"], "source_unavailable")
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(list((self.home / "state").glob("mine-sync-run-*")), [])

    def test_reparse_destination_refused(self):
        src = self.add("one.pdf")
        self.scan()
        src.write_bytes(b"updated")
        with patch.object(sync, "reparse", side_effect=lambda path: path == self.destination):
            result = self.scan()
        self.assertEqual(result["errors"], 1)
        self.assertEqual((self.destination / src.name).read_bytes(), b"initial")

    def test_folder_rename_does_not_change_destination_name(self):
        src = self.add("New folder/file.xlsx")
        self.assertEqual(self.cli().returncode, 0)
        self.source.joinpath("New folder").rename(self.source / "New folder 2")
        process = self.cli("--report")
        result = json.loads(process.stdout)
        self.assertEqual((result["copied"], result["updated"], result["skipped"]), (0, 0, 1))
        self.assertTrue((self.destination / src.name).exists())
        self.assertFalse(any(p.is_dir() for p in self.destination.iterdir()))

    def test_initial_collision_preserves_ambiguous_existing_file(self):
        self.add("a/report.xlsx", b"A")
        self.add("b/report.xlsx", b"B")
        (self.destination / "report.xlsx").write_bytes(b"old operational copy")
        result = self.scan()
        self.assertEqual((result["collisions"], result["collision_files"], result["copied"]), (1, 2, 2))
        self.assertEqual((self.destination / "report.xlsx").read_bytes(), b"old operational copy")
        self.assertEqual({p.read_bytes() for p in self.destination.glob("report__mine_*.xlsx")}, {b"A", b"B"})

    def test_root_collision_keeps_original_and_distinct_nested_copy(self):
        self.add("Report.xlsx", b"root")
        self.add("a/report.xlsx", b"nested")
        result = self.scan()
        self.assertEqual(result["collisions"], 1)
        self.assertEqual((self.destination / "Report.xlsx").read_bytes(), b"root")
        suffixed = list(self.destination.glob("*__mine_*.xlsx"))
        self.assertEqual(len(suffixed), 1)
        self.assertEqual(suffixed[0].read_bytes(), b"nested")

    def test_existing_owner_retained_when_duplicate_appears_or_disappears(self):
        self.add("a/report.xlsx", b"A")
        self.assertEqual(self.cli().returncode, 0)
        self.add("b/report.xlsx", b"B")
        result = json.loads(self.cli("--report").stdout)
        self.assertEqual((self.destination / "report.xlsx").read_bytes(), b"A")
        self.assertEqual((result["copied"], result["updated"], result["skipped"]), (1, 0, 1))
        filename = next(self.destination.glob("*__mine_*.xlsx"))
        self.source.joinpath("a/report.xlsx").unlink()
        self.add("b/report.xlsx", b"B changed")
        result = json.loads(self.cli("--report").stdout)
        self.assertEqual((self.destination / "report.xlsx").read_bytes(), b"A")
        self.assertEqual(filename.read_bytes(), b"B changed")
        self.assertEqual(result["updated"], 1)

    def test_generated_name_cannot_overwrite_real_source_filename(self):
        self.add("a/report.xlsx", b"A")
        self.add("b/report.xlsx", b"B")
        generated = sync.collision_filename(Path("a/report.xlsx"))
        self.add(generated, b"literal")
        result = self.scan()
        self.assertEqual(result["copied"], 3)
        self.assertEqual({p.read_bytes() for p in self.destination.glob("*.xlsx")}, {b"A", b"B", b"literal"})

    def test_new_literal_name_cannot_take_persisted_generated_name(self):
        self.add("a/report.xlsx", b"A")
        self.add("b/report.xlsx", b"B")
        self.assertEqual(self.cli().returncode, 0)
        generated = sync.collision_filename(Path("a/report.xlsx"))
        self.add(generated, b"new literal")
        result = json.loads(self.cli("--report").stdout)
        self.assertEqual((self.destination / generated).read_bytes(), b"A")
        self.assertEqual({p.read_bytes() for p in self.destination.glob("*.xlsx")}, {b"A", b"B", b"new literal"})
        self.assertEqual(result["copied"], 1)

    def test_corrupt_state_collision_does_not_overwrite_existing_canonical(self):
        self.add("a/report.xlsx", b"A")
        self.assertEqual(self.cli().returncode, 0)
        self.add("b/report.xlsx", b"B")
        (self.home / "state/mine-file-sync.json").write_text("{broken")
        result = json.loads(self.cli("--report").stdout)
        self.assertEqual((self.destination / "report.xlsx").read_bytes(), b"A")
        self.assertEqual(result["collision_files"], 2)

    def test_collision_short_digest_clash_uses_stable_serial(self):
        for number in range(6):
            self.add(f"{number}/report.xlsx", str(number).encode())
        digest = types.SimpleNamespace(hexdigest=lambda: "1" * 64)
        with patch.object(sync.hashlib, "sha256", return_value=digest):
            result = self.scan()
            self.assertEqual(result["copied"], 6)
            second = sync.empty_result()
            sync.sync_tree(self.source, self.destination, second, lambda **kw: None,
                           bindings=result["flatten_bindings"], outputs=result["flatten_outputs"])
            self.assertEqual((second["copied"], second["updated"], second["skipped"]), (0, 0, 6))

    def test_retained_local_archive_is_not_overwritten_by_new_mine_name(self):
        self.add("new/archive.pdf", b"mine")
        (self.destination / "archive.pdf").write_bytes(b"local archive")
        result = sync.empty_result()
        sync.sync_tree(self.source, self.destination, result, lambda **kw: None,
                       protected=["archive.pdf"])
        self.assertEqual((self.destination / "archive.pdf").read_bytes(), b"local archive")
        outputs = list(self.destination.glob("archive__mine_*.pdf"))
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].read_bytes(), b"mine")
        self.assertEqual(result["collision_files"], 1)

    def test_corrupt_state_recovers_local_archive_protection(self):
        self.add("new/archive.pdf", b"mine")
        (self.destination / "archive.pdf").write_bytes(b"archive")
        directory = self.home / "state"
        directory.mkdir()
        (directory / "mine-file-sync.json").write_text("{broken")
        (directory / "mine-file-sync-local-reserved.json").write_text(json.dumps({"filenames": ["archive.pdf"]}))
        result = json.loads(self.cli("--report").stdout)
        self.assertEqual(result["collision_files"], 1)
        self.assertEqual((self.destination / "archive.pdf").read_bytes(), b"archive")
        self.assertEqual(next(self.destination.glob("*__mine_*.pdf")).read_bytes(), b"mine")

    def test_rotation_limit(self):
        handler = sync.LOG.handlers[0]
        self.assertEqual((handler.maxBytes, handler.backupCount), (2 * 1024 * 1024, 4))


if __name__ == "__main__":
    unittest.main(verbosity=2)
