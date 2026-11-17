"""Mine -> local operational copy. Windows standard library, no agent.
Recursive discovery, flat destination, no source deletes, no content hashes. --report is for manual validation.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import re
import random
import math
import unicodedata
import json
import logging
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

SOURCE = Path(r"\\192.168.20.251\share")
DEST = Path(r"E:\Function")
HERMES_HOME = Path(r"C:\Users\win-10\AppData\Local\hermes")
EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".xlsb", ".pdf"}
STALL_SECONDS = 45.0
MAX_RUN_SECONDS = 900.0
LOG = logging.getLogger("mine-file-sync")
NETWORK_ERRORS = {51, 53, 54, 58, 59, 64, 67, 121, 1203, 1231, 1232, 2250}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def eligible(name):
    return not name.startswith("~$") and Path(name).suffix.lower() in EXTENSIONS


def signature(info):
    return info.st_size, info.st_mtime_ns


def reparse(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def safe_directory(path):
    for part in reversed((path, *path.parents)):
        if reparse(part):
            raise OSError(f"refusing reparse destination: {part}")
    path.mkdir(parents=True, exist_ok=True)


def setup_logging(home):
    directory = home / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    for handler in LOG.handlers[:]:
        handler.close()
        LOG.removeHandler(handler)
    handler = RotatingFileHandler(directory / "mine-file-sync.log",
                                 maxBytes=2 * 1024 * 1024, backupCount=4, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)


def acquire_lock(path):
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError as exc:
        handle.close()
        if exc.errno in {13, 11, 36} or getattr(exc, "winerror", None) in {32, 33}:
            return None
        raise


def release_lock(handle):
    if handle:
        import msvcrt
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()


class LocalStateError(OSError):
    pass


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers/filter drivers can briefly deny replacement of local JSON.
        # Retry only publication: four attempts, 350ms total sleep; prior JSON stays intact.
        for attempt, delay in enumerate((0.05, 0.1, 0.2, None)):
            try:
                os.replace(name, path)
                break
            except OSError as exc:
                if delay is None or getattr(exc, 'winerror', None) not in {5, 32, 33}:
                    raise LocalStateError(str(exc)) from exc
                LOG.warning('state_persist_retry path=%s attempt=%s winerror=%s', path, attempt + 1, exc.winerror)
                time.sleep(delay)
    finally:
        Path(name).unlink(missing_ok=True)


def load_state(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(value, dict):
            return value
        raise ValueError("state is not an object")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        LOG.warning("state_invalid: rebuilding state from destination metadata")
        return {}


def reachable(source):
    text = str(source)
    if not text.startswith("\\\\"):
        return
    host = text[2:].split("\\", 1)[0]
    with socket.create_connection((host, 445), timeout=2.0):
        pass


@contextmanager
def readonly_source(path):
    """Deny write/delete sharing only while taking the stable byte snapshot."""
    if os.name != "nt":
        with path.open("rb") as stream:
            yield stream
        return
    import msvcrt
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    create = api.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = api.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 0x1, None, 3, 0x08000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    with os.fdopen(fd, "rb") as stream:
        yield stream


class SourceChanged(Exception):
    pass


FAULT_REPORT = Path("E:/Function/گزارش روزانه رانندگان2.xlsx")
MAINTENANCE_REPORT = Path("E:/Function/تعمیرات 1405.xlsx")


def publish_fault_report(staged, destination, pulse=lambda **kw: None,
                         module="tools.fleet.repairs.sync_service", upstream=None):
    """The single shared fault workbook uses the project journal and atomic writer."""
    project = Path('E:/KomatsoAI')
    # Launch the native project interpreter, not the Windows venv redirector;
    # a timeout must terminate the process that actually performs the merge.
    cfg = dict(line.split(' = ', 1) for line in (project / '.venv/pyvenv.cfg').read_text().splitlines() if ' = ' in line)
    interpreter = Path(cfg['home']) / 'python.exe'
    bootstrap = ('import sys,runpy;sys.path.insert(0,' + repr(str(project / '.venv/Lib/site-packages')) +
                 ');runpy.run_module("' + module + '",run_name="__main__")')
    command = [str(interpreter),
            '-E', '-s', '-B', '-X', 'utf8', '-c', bootstrap,
            '--staged', str(staged), '--destination', str(destination)]
    if upstream is not None:
        command.extend(['--upstream', str(upstream)])
    process = subprocess.Popen(command,
            cwd=project, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 180
    try:
        while True:
            pulse(force=True)
            try:
                stdout, stderr = process.communicate(timeout=min(1, max(.01, deadline-time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise OSError('FAULT_SYNC_TIMEOUT: outcome will be recovered from journal on retry')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=REAP_SECONDS)
    if process.returncode:
        message = stdout.decode('utf8', errors='replace').strip()
        raise OSError(message or 'FAULT_SYNC_WORKER_FAILED: current local file preserved')
    LOG.info('%s %s', 'fault_merge' if module.endswith('.sync_service') else 'maintenance_guard',
             stdout.decode('utf8', errors='replace').strip())
    return json.loads(stdout.decode('utf8'))


def atomic_copy(source, destination, before, pulse):
    safe_directory(destination.parent)
    if reparse(destination):
        raise OSError(f"refusing reparse destination: {destination}")
    publication = None
    temporary = destination.parent / (".mine-sync-" + uuid.uuid4().hex + ".partial")
    try:
        pulse(force=True, temporary=str(temporary))
        with readonly_source(source) as reader:
            if signature(os.fstat(reader.fileno())) != signature(before):
                raise SourceChanged(str(source))
            with temporary.open("xb") as writer:
                while True:
                    pulse()
                    chunk = reader.read(1024 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            pulse()
            after = source.stat()
            if (signature(before) != signature(after)
                    or signature(before) != signature(os.fstat(reader.fileno()))
                    or temporary.stat().st_size != before.st_size):
                raise SourceChanged(str(source))
            os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
        # Parsing/merging can take seconds. The verified snapshot is complete;
        # release Share now so opening Excel does not inherit a read-only session.
        if destination.resolve() == FAULT_REPORT.resolve():
            publish_fault_report(temporary, destination, pulse=pulse)
        elif destination.resolve() == MAINTENANCE_REPORT.resolve():
            publication = publish_fault_report(temporary, destination, pulse=pulse,
                                               module="tools.fleet.repairs.maintenance_sync", upstream=source)
        else:
            os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            LOG.warning("staging_cleanup_retry path=%s", temporary)
            pulse(force=True, cleanup_temporary=str(temporary))

    return publication


def empty_result():
    return dict(status="running", scanned=0, copied=0, updated=0, skipped=0,
                skipped_locked=0, skipped_unstable=0, ignored=0, errors=0, collisions=0, collision_files=0)


def name_key(name):
    return unicodedata.normalize("NFC", str(name)).casefold()


def collision_filename(relative, digits=16, serial=0):
    relative = Path(relative)
    digest = hashlib.sha256(name_key(relative.as_posix()).encode("utf-8")).hexdigest()
    suffix = digest[:digits] + (f"-{serial}" if serial else "")
    return f"{relative.stem[:100]}__mine_{suffix}{relative.suffix}"


def flat_plan(paths, source, bindings=None, outputs=None, protected=None):
    """Stable ownership prevents an incoming duplicate taking another's name.

    Existing canonical ownership is retained. Initially ambiguous nested files
    each receive a deterministic suffix; a source-root file may keep its name.
    These hashes cover relative path strings only, never file contents.
    """
    bindings = dict(bindings) if isinstance(bindings, dict) else {}
    outputs = dict(outputs) if isinstance(outputs, dict) else {}
    groups = {}
    current = {}
    for path in paths:
        relative = path.relative_to(source)
        key = name_key(relative.as_posix())
        current[key] = (path, relative)
        groups.setdefault(name_key(path.name), []).append(key)
    outputs = {k: v for k, v in outputs.items()
               if isinstance(k, str) and isinstance(v, str)
               and Path(v).name == v and eligible(v)}
    historical = {}
    for key, value in outputs.items():
        historical.setdefault(name_key(value), set()).add(key)
    protected = {name_key(n) for n in (protected or []) if isinstance(n, str)}
    reserved = set(groups) | set(historical) | protected
    used = set()
    planned = {}
    collisions = sum(len(group) > 1 for group in groups.values())
    collision_files = 0

    # Allocate canonical names before generated names.
    for filename, keys in sorted(groups.items()):
        keys.sort()
        owner = bindings.get(filename)
        cached = outputs.get(keys[0])
        if owner not in keys:
            owner = None
            if len(keys) == 1 and filename not in protected and not (cached and "__mine_" in cached):
                # Folder renames of an unambiguous file do not affect its name.
                blockers = historical.get(filename, set())
                generated_blocker = any("__mine_" in outputs[k] for k in blockers)
                if not generated_blocker:
                    owner = keys[0]
            elif filename not in bindings and filename not in protected:
                owner = next((key for key in keys if len(current[key][1].parts) == 1), None)
        if owner:
            output = current[owner][0].name
            if name_key(output) not in used:
                planned[owner] = output
                used.add(name_key(output))
                bindings[filename] = owner

    for key, (path, relative) in sorted(current.items()):
        if key in planned:
            continue
        collision_files += 1
        cached = outputs.get(key)
        serial_match = re.search(r"__mine_[0-9a-f]{64}-([1-9][0-9]*)[.][^.]+$", cached or "")
        valid_cached = (cached and (
            any(cached == collision_filename(relative, digits) for digits in (16, 24, 32, 64))
            or (serial_match and cached == collision_filename(relative, 64, int(serial_match[1])))))
        if valid_cached and name_key(cached) not in used:
            output = cached
        else:
            output = None
            for digits in (16, 24, 32, 64):
                candidate = collision_filename(relative, digits)
                if name_key(candidate) not in reserved | used:
                    output = candidate
                    break
            serial = 0
            while output is None:
                serial += 1
                candidate = collision_filename(relative, 64, serial)
                if name_key(candidate) not in reserved | used:
                    output = candidate
        planned[key] = output
        used.add(name_key(output))
    outputs.update(planned)
    return [(current[k][0], v) for k, v in sorted(planned.items())], bindings, outputs, collisions, collision_files


def sync_tree(source, destination, result, pulse, dry_run=False, bindings=None, outputs=None, protected=None):
    details = 0

    def event(kind, path, exc=None):
        nonlocal details
        if details < 20:
            LOG.info("%s relative_path=%s%s", kind, path,
                     " exception=" + str(exc) if exc else "")
            details += 1

    def walk_error(exc):
        raise exc

    # Complete discovery before publishing anything, so collisions anywhere in
    # the tree are known. Directory names never filter eligible source files.
    result["phase"] = "source_enumeration"
    pulse(force=True)
    with os.scandir(source) as listing:
        next(listing, None)
    paths = []
    for directory, directories, files in os.walk(source, followlinks=False, onerror=walk_error):
        pulse()
        allowed = []
        for name in directories:
            pulse()
            if not reparse(Path(directory) / name):
                allowed.append(name)
        directories[:] = sorted(allowed, key=str.casefold)
        for name in sorted(files, key=str.casefold):
            pulse()
            if not eligible(name):
                result["ignored"] += 1
                continue
            src = Path(directory) / name
            result["scanned"] += 1
            if reparse(src):
                result["skipped"] += 1
            else:
                paths.append(src)
    result.update(source_enumerated=True, phase="copy")
    pulse(force=True)
    plan, bindings, outputs, collisions, collision_files = flat_plan(paths, source, bindings, outputs, protected)
    result.update(flatten_bindings=bindings, flatten_outputs=outputs,
                  collisions=collisions, collision_files=collision_files)
    pulse(force=True)
    for src, output_name in plan:
        pulse()
        relative = src.relative_to(source)
        dst = destination / output_name
        if name_key(output_name) != name_key(src.name):
            event("collision_resolved", f"{relative} -> {output_name}")
        try:
            before = src.stat()
            try:
                local = dst.stat()
            except FileNotFoundError:
                local = None
            if (local is not None and signature(local) == signature(before)
                    and dst.resolve() not in {FAULT_REPORT.resolve(), MAINTENANCE_REPORT.resolve()}):
                result["skipped"] += 1
                continue
            kind = "updated" if local is not None else "copied"
            if not dry_run:
                publication = atomic_copy(src, dst, before, pulse)
                if dst.resolve() == MAINTENANCE_REPORT.resolve() and isinstance(publication, dict):
                    result["maintenance_guard"] = publication
                    if publication.get("status") in ("unchanged", "identical_noop"):
                        kind = "skipped"
            result[kind] += 1
            pulse(force=True, temporary=None)
            event("would_" + kind if dry_run else kind, f"{relative} -> {output_name}")
        except SourceChanged as exc:
            result["skipped_unstable"] += 1
            event("skipped_unstable", relative, exc)
        except OSError as exc:
            if dst.resolve() == MAINTENANCE_REPORT.resolve():
                result["maintenance_guard"] = {"status": "blocked", "error": str(exc)}
                LOG.error("maintenance_sync_blocked relative_path=%s exception=%s", relative, exc)
            if getattr(exc, "winerror", None) in NETWORK_ERRORS:
                raise
            if isinstance(exc, (PermissionError, FileNotFoundError)) or getattr(exc, "winerror", None) in {32, 33}:
                result["skipped_locked"] += 1
                event("skipped_locked", relative, exc)
            else:
                result["errors"] += 1
                event("exception", relative, exc)
        pulse(temporary=None)
    result["status"] = "partial" if (result["errors"] or result["skipped_locked"]
                                            or result["skipped_unstable"]) else "success"


def worker(args):
    lock = acquire_lock(args.home / "state" / "mine-file-sync-worker.lock")
    if lock is None:
        atomic_json(args.worker_result, dict(empty_result(), status="already_running"))
        return 0
    result = empty_result()
    last_pulse = 0.0

    def pulse(force=False, **values):
        nonlocal last_pulse
        pending = values.pop('cleanup_temporary', None)
        if pending:
            result.setdefault('cleanup_pending', []).append(pending)
        result.update(values)
        now = time.monotonic()
        if force or now - last_pulse >= 1.0:
            atomic_json(args.worker_result, result)
            last_pulse = now
    try:
        setup_logging(args.home)
        pulse(force=True)
        state = load_state(args.home / "state" / "mine-file-sync.json")
        retained = state.get("local_reserved_names", [])
        retained = retained if isinstance(retained, list) else []
        # Retain archive collision protection even if the mutable state corrupts.
        try:
            reserved = json.loads((args.home / "state" / "mine-file-sync-local-reserved.json").read_text(encoding="utf-8"))
            fallback = reserved.get("filenames", []) if isinstance(reserved, dict) else []
            if isinstance(fallback, list):
                retained = retained + fallback
        except (OSError, ValueError):
            pass
        sync_tree(args.source, args.dest, result, pulse, args.dry_run,
                  state.get("flatten_bindings"), state.get("flatten_outputs"), retained)
    except OSError as exc:
        if isinstance(exc, LocalStateError):
            classification, status = 'local_state_io', 'local_state_error'
        else:
            classification = "source_outage" if (getattr(exc, "winerror", None) in NETWORK_ERRORS
                               or (isinstance(exc, FileNotFoundError) and not result.get("source_enumerated"))) else "source_access"
            status = 'source_unavailable'
        result.update(status=status, exception=str(exc), failure_classification=classification)
    except Exception as exc:
        LOG.exception("exception in sync worker")
        result.update(status="exception", exception=str(exc), errors=result["errors"] + 1)
    finally:
        pulse(force=True)
        release_lock(lock)
    return 0


# Standard-library lifecycle journal, separate from rebuildable copy metadata.
RELIABILITY_VERSION = 1
BACKOFF_MINUTES = (2, 4, 8, 10)
JITTER_FRACTION = 0.1
REAP_SECONDS = 5


def parse_time(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.timestamp() if stamp.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def backoff_delay(count, steps, jitter, sample=None):
    # Downward jitter keeps the configured cap a hard ceiling.
    sample = random.random() if sample is None else sample
    return steps[min(max(count - 1, 0), len(steps) - 1)] * 60 * (1 - jitter * sample)


def retry_remaining(state, now, cap):
    next_at = parse_time(state.get('next_attempt_at'))
    if next_at is None:
        return 0
    last_clock = parse_time(state.get('last_clock_at'))
    if last_clock is not None and now < last_clock:
        # Backward jumps must not suspend freshness indefinitely.
        remaining = min(max(next_at - last_clock, 0), cap)
    else:
        remaining = min(max(next_at - now, 0), cap)
    state['next_attempt_at'] = datetime.fromtimestamp(now + remaining, timezone.utc).isoformat() if remaining else None
    return remaining


def lifecycle_path(args):
    return args.home / 'state' / 'mine-file-sync-lifecycle.json'


def save_lifecycle(args, record, event):
    record.update(event=event, updated_at=utcnow())
    atomic_json(lifecycle_path(args), record)
    LOG.info('worker_lifecycle %s', json.dumps(record, ensure_ascii=True, sort_keys=True))


def windows_process_api():
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    api.GetProcessTimes.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    return api


def creation_identity(api, handle):
    from ctypes import wintypes
    times = [wintypes.FILETIME() for _ in range(4)]
    if not api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)


def process_identity(process):
    # Popen uses a native interpreter, so this handle is the actual worker.
    return creation_identity(windows_process_api(), process._handle)


def prior_worker_status(record):
    if record.get('termination_confirmed') is True:
        return 'terminated'
    if not record.get('worker_pid') or not record.get('creation_identity'):
        return 'unknown'
    api = windows_process_api()
    handle = api.OpenProcess(0x100000 | 0x1000, False, record['worker_pid'])
    if not handle:
        return 'terminated' if ctypes.get_last_error() == 87 else 'unknown'
    try:
        if creation_identity(api, handle) != record['creation_identity']:
            return 'identity_replaced'
        waited = api.WaitForSingleObject(handle, 0)
        return {0: 'terminated', 258: 'alive'}.get(waited, 'unknown')
    except OSError:
        return 'unknown'
    finally:
        api.CloseHandle(handle)


def cleanup_report(args, record):
    """Remove only this proven-ended attempt's registered, contained staging files.

    On sharing violations retain both report and lifecycle reference for retry.
    No wildcard deletes and no cleanup while worker identity is unknown/alive.
    """
    if prior_worker_status(record) not in {'terminated', 'identity_replaced'}:
        return False
    if not record.get('report_path'):
        return True
    report = Path(record.get('report_path', ''))
    if (report.parent != (args.home / 'state') or
            not re.fullmatch(r'mine-sync-run-[a-z0-9_]+\.json', report.name) or
            any(reparse(p) for p in (report, *report.parents))):
        LOG.error('staging_cleanup_refused report=%s', report)
        return False
    try:
        if not report.exists():
            return True
        # The initial mkstemp is empty; a durable staging pointer is published
        # before any partial is created. Atomic JSON writes never empty it.
        if record.get('worker_exit_outcome') == 'spawn_failed' or report.stat().st_size == 0:
            report.unlink()
            return True
        saved = json.loads(report.read_text(encoding='utf8'))
        if not isinstance(saved, dict):
            raise ValueError('worker report is not an object')
        paths = saved.get('cleanup_pending', []) + ([saved['temporary']] if saved.get('temporary') else [])
        for name in set(paths):
            path = Path(name)
            if (path.parent != args.dest or not re.fullmatch(r'\.mine-sync-[a-f0-9]{32}\.partial', path.name)
                    or any(reparse(p) for p in (path, *path.parents))):
                LOG.error('staging_cleanup_refused path=%s', path)
                return False
            path.unlink(missing_ok=True)
            LOG.info('staging_cleanup_completed path=%s', path)
        report.unlink(missing_ok=True)
        return True
    except (OSError, ValueError, TypeError) as exc:
        LOG.warning('staging_cleanup_retry report=%s exception=%s', report, exc)
        return False


def worker_guard(args):
    try:
        record = json.loads(lifecycle_path(args).read_text(encoding='utf-8'))
        if not isinstance(record, dict):
            raise ValueError('invalid lifecycle journal')
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return dict(empty_result(), status='worker_blocked', failure_classification='lifecycle_unreadable')
    outcome = prior_worker_status(record)
    if outcome in {'terminated', 'identity_replaced'}:
        if not record.get('termination_confirmed'):
            record.update(termination_confirmed=True, bounded_wait_result=outcome,
                          worker_exit_outcome='prior_process_gone')
            save_lifecycle(args, record, 'prior_worker_reconciled')
        if not cleanup_report(args, record):
            return dict(empty_result(), status='worker_blocked', failure_classification='staging_cleanup_pending',
                        worker_lifecycle=record)
        return None
    LOG.error('worker_blocked prior_worker_status=%s record=%s', outcome, json.dumps(record))
    return dict(empty_result(), status='worker_blocked', failure_classification='termination_unconfirmed',
                worker_lifecycle=record)


def stop_worker(process, args, record, reason):
    record.update(timeout_reason=reason, kill_requested=True, termination_confirmed=False)
    save_lifecycle(args, record, 'kill_requested')
    try:
        process.kill()
        record['kill_result'] = 'request_succeeded'
    except OSError as exc:
        record['kill_result'] = 'request_failed: ' + str(exc)
    try:
        record['worker_exit_code'] = process.wait(timeout=REAP_SECONDS)
        record.update(bounded_wait_result='signaled_and_reaped', termination_confirmed=True)
    except subprocess.TimeoutExpired:
        record['bounded_wait_result'] = 'deadline_exceeded'
    except OSError as exc:
        record['bounded_wait_result'] = 'wait_failed: ' + str(exc)
    record['worker_exit_outcome'] = 'terminated' if record['termination_confirmed'] else 'termination_unconfirmed'
    save_lifecycle(args, record, 'bounded_wait_finished')


def supervise(args):
    args.home.joinpath('state').mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='mine-sync-run-', suffix='.json', dir=args.home / 'state')
    os.close(fd)
    report = Path(name)
    process = None
    result = empty_result()
    started = time.monotonic()
    record = dict(attempt_id=uuid.uuid4().hex, attempt_started_at=utcnow(), worker_pid=None,
                  creation_identity=None, termination_confirmed=False, kill_requested=False,
                  kill_result='not_requested', bounded_wait_result='not_waited', bounded_wait_seconds=REAP_SECONDS, timeout_reason=None,
                  worker_exit_outcome='spawning', report_path=name)
    # A crash between intent and PID publication leaves a visible, fail-closed guard.
    save_lifecycle(args, record, 'spawn_intent')
    try:
        command = [sys._base_executable or sys.executable, str(Path(__file__).resolve()), '--worker-result', name,
                   '--source', str(args.source), '--dest', str(args.dest), '--home', str(args.home)]
        if args.dry_run:
            command.append('--dry-run')
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        identity = process_identity(process)
        record.update(worker_pid=process.pid, creation_identity=identity, worker_started_at=utcnow(),
                      worker_creation_time_at=datetime.fromtimestamp(int(identity) / 10000000 - 11644473600, timezone.utc).isoformat(),
                      worker_exit_outcome='running')
        save_lifecycle(args, record, 'worker_started')
        last_activity = time.monotonic()
        last_stamp = None
        while process.poll() is None:
            time.sleep(0.25)
            stamp = report.stat().st_mtime_ns
            if stamp != last_stamp:
                last_stamp = stamp
                last_activity = time.monotonic()
            idle = time.monotonic() - last_activity > STALL_SECONDS
            if idle or time.monotonic() - started > MAX_RUN_SECONDS:
                stop_worker(process, args, record, 'inactivity' if idle else 'run_budget')
                result.update(status='source_unavailable' if idle else 'time_budget',
                              failure_classification='io_timeout_unknown' if idle else 'run_budget',
                              exception='worker inactivity deadline' if idle else 'worker run budget reached')
                break
        if record['timeout_reason'] is None:
            code = process.wait(timeout=REAP_SECONDS)
            record.update(worker_exit_code=code, termination_confirmed=True, bounded_wait_result='signaled_and_reaped',
                          worker_exit_outcome='normal_exit' if code == 0 else 'nonzero_exit')
        try:
            saved = json.loads(report.read_text(encoding='utf-8'))
            if not isinstance(saved, dict):
                raise ValueError('worker report is not an object')
            if record['timeout_reason'] == 'inactivity' and saved.get('phase') == 'source_enumeration':
                result['failure_classification'] = 'source_timeout'
            if result['status'] != 'running':
                saved.update({key: result[key] for key in ['status', 'exception', 'failure_classification']})
            result = saved
        except (OSError, ValueError):
            if result['status'] == 'running':
                result.update(status='exception', failure_classification='worker_report', exception='worker did not write a readable result')
        if result['status'] == 'running' or (process.returncode not in {0, None} and record['timeout_reason'] is None):
            result.update(status='exception', failure_classification='worker_exit', exception=f'worker exited unexpectedly: {process.returncode}')
        if not record['termination_confirmed']:
            result.update(status='worker_blocked', failure_classification='termination_unconfirmed')
        result.pop('temporary', None)
        return result
    finally:
        if process is not None and record['timeout_reason'] is None and not record['termination_confirmed']:
            stop_worker(process, args, record, 'supervisor_exception')
        elif process is None:
            record.update(termination_confirmed=True, worker_exit_outcome='spawn_failed')
        record.update(attempt_finished_at=utcnow(), elapsed_seconds=round(time.monotonic() - started, 3))
        save_lifecycle(args, record, 'attempt_end')
        result['worker_lifecycle'] = dict(record)
        if record['termination_confirmed']:
            cleanup_report(args, record)


def publication_notice_tick(args, result):
    """Same Cron, notice-only subprocess, including offline/backoff ticks.

    Deployment remains disabled. No new scheduler, sender, or remote I/O.
    """
    config = Path('E:/KomatsoAI/settings/maintenance_entry.json')
    if args.dry_run or args.dest.resolve() != DEST.resolve() or args.source != SOURCE:
        return
    value = json.loads(config.read_text('utf8')).get('outbound_publication', {})
    if value.get('enabled') is not True or value.get('policy') != 'daily_1230':
        return
    project = Path('E:/KomatsoAI')
    cfg = dict(line.split(' = ',1) for line in (project/'.venv/pyvenv.cfg').read_text().splitlines() if ' = ' in line)
    bootstrap = ('import sys,runpy;sys.path.insert(0,'+repr(str(project/'.venv/Lib/site-packages'))+
                 ');runpy.run_module("tools.fleet.repairs.maintenance_publication",run_name="__main__")')
    outcome = subprocess.run([str(Path(cfg['home'])/'python.exe'), '-E','-s','-B','-X','utf8','-c',bootstrap,
                              '--poll','--sync-status',result['status']], cwd=project,
                             capture_output=True, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
    if outcome.returncode:
        raise OSError('PUBLICATION_NOTICE_TICK_FAILED')


def run(args):
    started = time.monotonic()
    setup_logging(args.home)
    path = args.home / 'state' / 'mine-file-sync.json'
    state = load_state(path)
    now_text = utcnow()
    now = parse_time(now_text)
    same_scope = (state.get('source') == str(args.source) and state.get('destination') == str(args.dest))
    # Old state may use a trailing slash for this same source.
    same_scope = same_scope or (str(state.get('source', '')).rstrip('\\/') == str(args.source).rstrip('\\/')
                               and state.get('destination') == str(args.dest))
    steps = getattr(args, 'backoff_minutes', BACKOFF_MINUTES)
    jitter = getattr(args, 'backoff_jitter', JITTER_FRACTION)
    remaining = retry_remaining(state, now, steps[-1] * 60) if same_scope else 0
    attempted = False
    if remaining > 0:
        result = dict(empty_result(), status='backoff_skip', failure_classification=state.get('last_failure_classification', 'source_outage'))
    else:
        result = worker_guard(args)
        if result is None:
            attempted = True
            state['last_attempt_at'] = now_text
            LOG.info('attempt_start at=%s source=%s destination=%s', now_text, args.source, args.dest)
            # Persist the attempted time before any network I/O; no optimistic success stamp.
            if not args.dry_run:
                state.update(sync_healthy=False, health_status='attempt_running', reliability_schema_version=RELIABILITY_VERSION)
                atomic_json(path, state)
            try:
                reachable(args.source)
            except OSError as exc:
                result = dict(empty_result(), status='source_unavailable', exception=str(exc), failure_classification='source_outage')
            else:
                result = supervise(args)
    result.update(started_at=now_text, finished_at=utcnow(), duration_seconds=round(time.monotonic() - started, 3),
                  dry_run=args.dry_run, source=str(args.source), destination=str(args.dest), attempted=attempted)
    bindings = result.pop('flatten_bindings', None)
    outputs = result.pop('flatten_outputs', None)
    if not args.dry_run:
        if bindings is not None:
            state.update(flatten_bindings=bindings, flatten_outputs=outputs)
        state.update(schema_version=3, reliability_schema_version=RELIABILITY_VERSION, layout='flat',
                     source=str(args.source), destination=str(args.dest), last_clock_at=result['finished_at'])
        unavailable = result.get('failure_classification') in {'source_outage', 'source_timeout'} and attempted
        previous_outage = bool(state.get('outage'))
        if unavailable:
            if not previous_outage:
                state['outage_since'] = result['finished_at']
            count = state.get('consecutive_source_failures', 0) if same_scope else 0
            count = count if isinstance(count, int) and count >= 0 else 0
            count += 1
            delay = backoff_delay(count, steps, jitter)
            state.update(outage=True, consecutive_source_failures=count, retry_delay_seconds=delay,
                         next_attempt_at=datetime.fromtimestamp(parse_time(result['finished_at']) + delay, timezone.utc).isoformat())
        elif result.get('source_enumerated') and result['status'] in {'success', 'partial'}:
            if previous_outage or state.get('consecutive_source_failures'):
                state['last_recovery_at'] = result['finished_at']
                LOG.info('recovered source=%s', args.source)
            state.update(outage=False, outage_since=None, consecutive_source_failures=0, next_attempt_at=None, retry_delay_seconds=0)
        if attempted and result['status'] not in {'success', 'partial'}:
            state.update(last_failure_reason=result.get('exception', result['status']),
                         last_failure_classification=result.get('failure_classification', result['status']))
        if result['status'] == 'success':
            state.update(last_successful_run=result['finished_at'], last_success_at=result['finished_at'])
        else:
            state.setdefault('last_success_at', state.get('last_successful_run'))
        if result.get('source_enumerated') and result['status'] in {'success', 'partial'}:
            state['last_completed_scan'] = result['finished_at']
        state.setdefault('next_attempt_at', None)
        state.setdefault('last_failure_reason', None)
        state.setdefault('last_attempt_at', None)
        state.setdefault('outage_since', None)
        state.setdefault('consecutive_source_failures', 0)
        result.update(sync_healthy=result['status'] == 'success', next_attempt_at=state['next_attempt_at'])
        state.update(sync_healthy=result['sync_healthy'], health_status=result['status'], last_run=result)
        if 'worker_lifecycle' in result:
            state['worker_lifecycle'] = result['worker_lifecycle']
        history = state.get('recent_runs', [])
        state['recent_runs'] = (history if isinstance(history, list) else [])[-19:] + [result]
        atomic_json(path, state)
    LOG.info("run status=%s scanned=%s copied=%s updated=%s skipped=%s skipped_locked=%s skipped_unstable=%s errors=%s collisions=%s collision_files=%s duration=%.3fs dry_run=%s",
             result["status"], result["scanned"], result["copied"], result["updated"], result["skipped"],
             result["skipped_locked"], result["skipped_unstable"], result["errors"],
             result["collisions"], result["collision_files"], result["duration_seconds"], args.dry_run)
    if result["status"] in {"exception", "time_budget"}:
        LOG.error("exception: %s", result.get("exception"))
    LOG.info('attempt_end status=%s attempted=%s sync_healthy=%s classification=%s elapsed=%s next_attempt_at=%s',
             result['status'], attempted, result['status'] == 'success', result.get('failure_classification'),
             result['duration_seconds'], state.get('next_attempt_at'))
    try:
        publication_notice_tick(args, result)
    except Exception as exc:
        LOG.error('publication_notice_tick failed error=%s', type(exc).__name__)
        result['publication_notice_error'] = type(exc).__name__
    if args.report:
        print(json.dumps(result, ensure_ascii=True))
    # Preserve cron's existing executable contract. Health consumers must use health_status/sync_healthy.
    return 1 if result['status'] == 'exception' or result['errors'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--dest", type=Path, default=DEST)
    parser.add_argument("--home", type=Path, default=HERMES_HOME)
    parser.add_argument("--worker-result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--backoff-minutes', type=lambda text: tuple(float(v) for v in text.split(',')), default=BACKOFF_MINUTES)
    parser.add_argument('--backoff-jitter', type=float, default=JITTER_FRACTION)
    args = parser.parse_args(argv)
    if (not args.backoff_minutes or any(not math.isfinite(v) or v <= 0 or v > 10 for v in args.backoff_minutes)
            or list(args.backoff_minutes) != sorted(args.backoff_minutes)
            or not math.isfinite(args.backoff_jitter) or not 0 <= args.backoff_jitter <= 0.2):
        parser.error('backoff must be increasing positive minutes <=10; jitter must be 0..0.2')
    if args.worker_result:
        return worker(args)
    setup_logging(args.home)
    lock = acquire_lock(args.home / "state" / "mine-file-sync.lock")
    if lock is None:
        LOG.info("skip status=already_running sync_healthy=false reason=parent_lock_busy")
        if args.report:
            print('{"status":"already_running"}')
        return 0
    try:
        probe = acquire_lock(args.home / "state" / "mine-file-sync-worker.lock")
        if probe is None:
            LOG.info("skip status=already_running sync_healthy=false reason=worker_lock_busy")
            if args.report:
                print('{"status":"already_running"}')
            return 0
        release_lock(probe)
        return run(args)
    except Exception:
        setup_logging(args.home)
        LOG.exception("exception in supervisor")
        return 1
    finally:
        release_lock(lock)


if __name__ == "__main__":
    sys.exit(main())
