"""Narrow owner-authorized diagnostics. Only UUID-scoped private Share files mutate.

No ACL changes, production publication, scheduler changes or Office automation.
"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import uuid
from zoneinfo import ZoneInfo

ROOT = Path('E:/KomatsoAI')
sys.path.insert(0, str(ROOT))
from tools.fleet.repairs.entry_service import replace_source

SHARE = Path(r'\\192.168.20.251\share')
PREFIX = '__komatso_publication_uat_'
OUT = ROOT / 'runtime/maintenance-coordinated-candidate/final-acceptance-20261116'
PROTECTED = (SHARE / 'تعمیرات 1405.xlsx', Path('E:/Function/تعمیرات 1405.xlsx'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def error(exc):
    return dict(error_type=type(exc).__name__, error=str(exc), repr=repr(exc),
                errno=getattr(exc, 'errno', None), winerror=getattr(exc, 'winerror', None),
                filename=str(getattr(exc, 'filename', None)), ntstatus=getattr(exc, 'ntstatus', None),
                traceback=traceback.format_exc())


def step(result, name, call):
    row = dict(operation=name, time_tehran=datetime.now(ZoneInfo('Asia/Tehran')).isoformat())
    with (OUT / 'smb-operations.jsonl').open('a', encoding='utf8') as log:
        log.write(json.dumps(dict(event='start', **row), ensure_ascii=False) + '\n')
    print('START ' + name, flush=True)
    started = time.monotonic()
    try:
        value = call()
        row.update(success=True, value=value)
    except Exception as exc:
        row.update(success=False, **error(exc))
    row['elapsed_seconds'] = round(time.monotonic() - started, 3)
    result['operations'].append(row)
    with (OUT / 'smb-operations.jsonl').open('a', encoding='utf8') as log:
        log.write(json.dumps(dict(event='end', **row), ensure_ascii=False, default=str) + '\n')
    print(('PASS ' if row['success'] else 'FAIL ') + name, flush=True)
    return row


def bounded_hash(path):
    child = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c',
                            'import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())',
                            str(path)], capture_output=True, text=True, timeout=12)
    if child.returncode:
        raise OSError('read-only hash child: ' + child.stderr[-1500:])
    return child.stdout.strip()


def native_create(path):
    """Only diagnose CREATE_NEW with normal Win32 API; never write past a failure."""
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateFileW(str(path), 0x40000000, 1 | 2 | 4, None, 1, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    if not api.CloseHandle(handle):
        raise ctypes.WinError(ctypes.get_last_error())
    return 'empty diagnostic file created and closed; no payload written'


def native_payload(result, folder, name, payload, known):
    """Ordinary same-type WriteFile captures the error hidden by CRT errno mapping."""
    known.add(name)
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    api.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    def native_open():
        handle = api.CreateFileW(str(folder / name), 0x40000000, 1 | 2 | 4, None, 1, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle
    opened = step(result, name + ':Native_Create', native_open)
    if not opened['success']:
        return False
    handle = opened['value']
    def native_write():
        count = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(payload)
        if not api.WriteFile(handle, buffer, len(payload), ctypes.byref(count), None):
            code = ctypes.get_last_error()
            nt = ctypes.WinDLL('ntdll').RtlGetLastNtStatus
            nt.restype = wintypes.LONG
            exc = ctypes.WinError(code)
            exc.ntstatus = hex(nt() & 0xffffffff)
            raise exc
        return dict(bytes_written=count.value, expected=len(payload))
    def native_flush():
        if not api.FlushFileBuffers(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    def native_close():
        if not api.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        wrote = step(result, name + ':Native_WriteFile', native_write)
        flushed = step(result, name + ':Native_FlushFileBuffers', native_flush)
    finally:
        closed = step(result, name + ':Native_CloseHandle', native_close)
    read = step(result, name + ':Native_Readback', lambda: dict(sha256=sha((folder / name).read_bytes()),
                                                              expected_sha256=sha(payload)))
    return (all(row['success'] for row in (wrote, flushed, closed, read))
            and read['value']['sha256'] == sha(payload))



def guard(folder, token):
    if (folder.parent != SHARE or not re.fullmatch(PREFIX + r'[0-9a-f]{32}', folder.name)
            or (folder / 'scope.json').read_text('utf8') != token):
        raise ValueError('private test scope denied')


def probe_file(result, folder, name, payload, known):
    path = folder / name
    known.add(name)
    opened = step(result, name + ':Create', lambda: path.open('xb'))
    if not opened['success']:
        native = 'native-' + name
        known.add(native)
        step(result, name + ':CreateFileW_diagnostic', lambda: native_create(folder / native))
        return False
    stream = opened.pop('value')
    opened['value'] = 'binary exclusive create; no existing file overwritten'
    try:
        wrote = step(result, name + ':Write', lambda: stream.write(payload))
        flushed = step(result, name + ':Flush', lambda: stream.flush())
        synced = step(result, name + ':FlushFileBuffers_os_fsync', lambda: os.fsync(stream.fileno()))
    finally:
        closed = step(result, name + ':Close', lambda: stream.close())
    read = step(result, name + ':Readback', lambda: dict(sha256=sha(path.read_bytes()),
                                                       expected_sha256=sha(payload)))
    if not all(r['success'] for r in (wrote, flushed, synced, closed, read)):
        return False
    if read['value']['sha256'] != sha(payload):
        return False
    renamed = 'renamed-' + name
    known.add(renamed)
    rename = step(result, name + ':Rename', lambda: str(path.rename(folder / renamed)))
    if not rename['success']:
        return False
    # The replacement uses the same approved file type, never a renamed bypass.
    stage = folder / ('stage-' + name)
    known.add(stage.name)
    staged = step(result, name + ':Stage', lambda: stage.write_bytes(payload))
    if not staged['success']:
        return False
    replace = step(result, name + ':ReplaceFileW', lambda: replace_source(stage, folder / renamed))
    after = step(result, name + ':Replace_readback', lambda: sha((folder / renamed).read_bytes()))
    return replace['success'] and after['success'] and after['value'] == sha(payload)


def run(native=False):
    OUT.mkdir(parents=True, exist_ok=True)
    folder = SHARE / (PREFIX + uuid.uuid4().hex)
    token = folder.name
    known = {'scope.json'}
    result = dict(schema='komatso-final-smb-diagnostic-v1', operations=[], test_folder=str(folder), native=native,
                  account=subprocess.check_output(['whoami'], text=True).strip(),
                  time_tehran=datetime.now(ZoneInfo('Asia/Tehran')).isoformat(),
                  production_writes=0, ACL_changes=0, restarts=0)
    def persist():
        (OUT / ('smb-native-diagnostic.json' if native else 'smb-diagnostic.json')).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), 'utf8')
    before = {}
    for path in PROTECTED:
        row = step(result, 'protected_before:' + str(path), lambda p=path: bounded_hash(p))
        if row['success']:
            before[str(path)] = row['value']
    created = False
    try:
        step(result, 'UNC_root_stat', lambda: dict(path=str(SHARE), directory=SHARE.is_dir()))
        made = step(result, 'Create_private_directory', lambda: folder.mkdir())
        created = made['success']
        if not created:
            return
        marker = step(result, 'Create_marker', lambda: (folder / 'scope.json').write_text(token, 'utf8'))
        if not marker['success']:
            return
        guard(folder, token)
        import openpyxl
        book = openpyxl.Workbook()
        book.active['A1'] = 'SYNTHETIC ONLY'
        data = io.BytesIO()
        book.save(data)
        book.close()
        payload = data.getvalue()
        result['valid_synthetic_xlsx_sha256'] = sha(payload)
        if native:
            result['small_plain_ok'] = native_payload(result, folder, 'native-small.txt', b'A' * 64, known)
            result['equal_size_plain_ok'] = native_payload(result, folder, 'native-large.txt', b'A' * len(payload), known)
            result['valid_xlsx_ok'] = native_payload(result, folder, 'native-valid.xlsx', payload, known)
            result['verdict'] = 'NATIVE_IO_PASS' if all(result[key] for key in (
                'small_plain_ok', 'equal_size_plain_ok', 'valid_xlsx_ok')) else 'NATIVE_IO_FAILED'
        else:
            result['plain_ok'] = probe_file(result, folder, 'plain.txt', b'SYNTHETIC ONLY\r\n', known)
            result['xlsx_ok'] = probe_file(result, folder, 'synthetic.xlsx', payload, known)
            if not result['xlsx_ok']:
                # Distinguish a specific basename/Unicode rule; no alternative extension writes.
                result['alternative_xlsx_ok'] = probe_file(result, folder, 'normal.xlsx', payload, known)
            result['unicode_xlsx_ok'] = probe_file(result, folder, 'آزمون.xlsx', payload, known)
            result['verdict'] = 'IO_PASS' if result['plain_ok'] and result['xlsx_ok'] and result['unicode_xlsx_ok'] else 'IO_FAILED'
        persist()
    except Exception as exc:
        result.update(verdict='DIAGNOSTIC_ERROR', **error(exc))
    finally:
        if created:
            try:
                guard(folder, token)
                for name in sorted(known - {'scope.json'}):
                    path = folder / name
                    step(result, 'Cleanup:' + name, lambda p=path: p.unlink(missing_ok=True))
                # Refuse recursive cleanup and leave marker if unknown files appeared.
                if {p.name for p in folder.iterdir()} != {'scope.json'}:
                    raise ValueError('unexpected private-directory contents; no recursive deletion')
                (folder / 'scope.json').unlink()
                folder.rmdir()
                result['private_directory_removed'] = True
            except Exception as exc:
                result.update(private_directory_removed=False, cleanup_error=error(exc))
        result['protected_unchanged'] = {}
        for path in PROTECTED:
            row = step(result, 'protected_after:' + str(path), lambda p=path: bounded_hash(p))
            result['protected_unchanged'][str(path)] = row['success'] and row['value'] == before.get(str(path))
        persist()
        print(json.dumps(dict(verdict=result.get('verdict'), account=result['account'],
                             folder=str(folder), evidence=str(OUT / (
                                 'smb-native-diagnostic.json' if native else 'smb-diagnostic.json')),
                             failures=[r for r in result['operations'] if not r['success']]), ensure_ascii=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute-private-share-diagnostic', action='store_true', required=True)
    parser.add_argument('--native-io', action='store_true')
    args = parser.parse_args()
    run(native=args.native_io)
