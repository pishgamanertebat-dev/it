"""Coordinated publication for the bound maintenance workbook in the existing Sync job."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo
from contextlib import contextmanager

from . import maintenance_identity as identity
from .entry_service import (EntryError, ROOT, open_audit, publish_candidate,
                            replace_source, source_reader, writer_lock)
from .maintenance_baseline import (KIND, digest, encoded, journal_branch,
                                   snapshot, states, validate_workbook)
from .maintenance_sync import retain_snapshot

CANDIDATE_ROOT = ROOT / 'runtime/maintenance-coordinated-candidate'
WORKBOOK = 'تعمیرات 1405.xlsx'
OWNER_LUNCH_CONTRACT = 'owner_lunch_1200_1300_v1'
SMB_UAT_ROOT = Path(r'\\192.168.20.251\share\_smb_test_20261116_155948')


def contract_test_upstream(path, value):
    import re
    return (value.get('operating_contract') == OWNER_LUNCH_CONTRACT
            and value.get('policy') == 'daily_1230'
            and path.name == WORKBOOK and path.parent.parent == SMB_UAT_ROOT
            and re.fullmatch(r'komatso_contract_[0-9a-f]{32}', path.parent.name) is not None)


def production_local(settings, config, runtime):
    from .maintenance_service import CONFIG, RUNTIME
    source = Path(settings['source'])
    for actual, expected in ((source, Path('E:/Function') / WORKBOOK),
                             (Path(config), CONFIG), (Path(runtime), RUNTIME)):
        if actual != expected or actual.resolve(strict=True) != expected:
            raise EntryError('PUBLICATION_PRODUCTION_PATH_DENIED')
    return source


def options(settings):
    value = settings.get('outbound_publication', {})
    if value.get('enabled', False) is False:
        return None
    if (value.get('enabled') is not True or value.get('mode') not in ('isolated_candidate', 'production')
            or set(value) - {'enabled', 'mode', 'isolation_root', 'publication_window', 'policy', 'alert_owner', 'operating_contract'}
            or not {'enabled', 'mode', 'isolation_root', 'publication_window'} <= set(value)):
        raise EntryError('PUBLICATION_CONFIG_INVALID')
    if value.get('policy') not in (None, 'daily_1230'):
        raise EntryError('PUBLICATION_CONFIG_INVALID')
    if ('operating_contract' in value
            and (value['operating_contract'] != OWNER_LUNCH_CONTRACT or value.get('policy') != 'daily_1230')):
        raise EntryError('PUBLICATION_CONFIG_INVALID')
    if value.get('policy') == 'daily_1230' and value['publication_window'] != {'start':'12:30', 'end':'12:55'}:
        raise EntryError('PUBLICATION_WINDOW_INVALID')
    if value['mode'] == 'production' and (value.get('operating_contract') != OWNER_LUNCH_CONTRACT
            or value.get('isolation_root') is not None or value.get('alert_owner') != '455740857'):
        raise EntryError('PUBLICATION_CONFIG_INVALID')
    return value


def isolated_paths(settings, upstream, runtime, staged, config):
    value = options(settings)
    if value['mode'] == 'production':
        source = production_local(settings, config, runtime)
        upstream, staged = Path(upstream), Path(staged)
        if (upstream != SMB_UAT_ROOT.parent / WORKBOOK or upstream.resolve(strict=True) != upstream
                or staged.parent != source.parent or staged.resolve(strict=True) != staged
                or not re.fullmatch(r'\.mine-sync-[0-9a-f]{32}\.partial', staged.name)):
            raise EntryError('PUBLICATION_PRODUCTION_PATH_DENIED')
        return source, upstream, value
    root = Path(value['isolation_root']).resolve(strict=True)
    if not root.is_relative_to(CANDIDATE_ROOT.resolve()) or root == CANDIDATE_ROOT.resolve():
        raise EntryError('PUBLICATION_ISOLATION_REQUIRED')
    paths = [Path(p) for p in (settings['source'], upstream, runtime, staged, config)]
    for path in paths:
        if (not path.resolve(strict=True).is_relative_to(root)
                and not (path == Path(upstream) and contract_test_upstream(path, value))):
            raise EntryError('PUBLICATION_PRODUCTION_PATH_DENIED')
        if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
               for p in (path, *path.parents) if p != root.parent):
            raise EntryError('PUBLICATION_REPARSE_DENIED')
    source, upstream = paths[0].resolve(), paths[1].resolve()
    if source == upstream or source.name != WORKBOOK or upstream.name != WORKBOOK:
        raise EntryError('PUBLICATION_SINGLE_WORKBOOK_REQUIRED')
    return source, upstream, value


def window_open(value):
    if value.get('policy') == 'daily_1230':
        from .maintenance_publication_alerts import phase
        return phase() == 'open'
    window = value['publication_window']
    try:
        start, end = window['start'], window['end']
        import re
        if (set(window) != {'start', 'end'} or any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t)
                                                  for t in (start, end)) or start > end):
            raise ValueError()
        now = datetime.now(ZoneInfo('Asia/Tehran')).strftime('%H:%M')
        return start <= now <= end
    except (KeyError, TypeError, ValueError):
        raise EntryError('PUBLICATION_WINDOW_INVALID')


def writer_codec(config, runtime):
    """Select the preserving codec for the configured daily or inbound-only Writer."""
    settings = json.loads(Path(config).read_text('utf8'))
    value = options(settings)
    if value is None and settings.get('sync_policy') == 'upstream_copy':
        from .maintenance_history_sync import writer_codec as inbound_writer_codec
        return inbound_writer_codec(settings['source'], runtime)
    if value is None or value.get('policy') != 'daily_1230':
        return {}
    paths = (Path(config), Path(runtime), Path(settings['source']))
    if value['mode'] == 'production':
        production_local(settings, config, runtime)
    else:
        root = Path(value['isolation_root']).resolve(strict=True)
        if root == CANDIDATE_ROOT.resolve() or not root.is_relative_to(CANDIDATE_ROOT.resolve()):
            raise EntryError('PUBLICATION_ISOLATION_REQUIRED')
        for path in paths:
            if not path.resolve(strict=True).is_relative_to(root):
                raise EntryError('PUBLICATION_PRODUCTION_PATH_DENIED')
            if any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in (path,*path.parents)):
                raise EntryError('PUBLICATION_REPARSE_DENIED')
    from .maintenance_ooxml import read, dump

    def read_for_writer(data):
        book = read(data)
        if not book._maintenance_excel_notes:
            return book
        try:
            # The Writer holds the same lock as Sync. Accept native identities
            # only on the exact local version already proven by the Journal.
            with closing(open_audit(runtime)) as db:
                if journal_branch(db, paths[-1].resolve(), data, runtime,
                                  upstream_copy=True, coordinated=True) is None:
                    raise EntryError('PUBLICATION_APPROVED_BASELINE_REQUIRED')
            return book
        except BaseException:
            book.close()
            raise

    return dict(workbook_reader=read_for_writer, workbook_dumper=dump)


def schema(db):
    # Additive candidate state. Original edits, baseline events and snapshots
    # remain intact. No INSERT OR REPLACE on publication receipts.
    with db:
        db.execute('''CREATE TABLE IF NOT EXISTS maintenance_publications_v1 (
            publication TEXT PRIMARY KEY, before_hash TEXT NOT NULL,
            candidate_hash TEXT NOT NULL, identities TEXT NOT NULL,
            status TEXT NOT NULL, error TEXT NOT NULL, created TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS maintenance_publication_events_v1 (
            sequence INTEGER PRIMARY KEY, publication TEXT, status TEXT NOT NULL,
            detail TEXT NOT NULL, created TEXT NOT NULL)''')


def event(db, publication, status, detail=''):
    with db:
        db.execute('INSERT INTO maintenance_publication_events_v1 VALUES (NULL,?,?,?,?)',
                   (publication, status, detail, datetime.now(timezone.utc).isoformat()))


def transition(db, publication, status, detail=''):
    with db:
        db.execute('UPDATE maintenance_publications_v1 SET status=?,error=? WHERE publication=?',
                   (status, detail, publication))
        event(db, publication, status, detail)


def replay_branch(db, source, original, runtime, *, preserve=False):
    branch = journal_branch(db, source, original, runtime, upstream_copy=True, coordinated=True)
    if branch is None:
        raise EntryError('PUBLICATION_APPROVED_BASELINE_REQUIRED')
    state, mine_hash, protected, batches = branch
    base = proof_workbook(snapshot(runtime, mine_hash), preserve=preserve)
    local = identity.load(original, preserve=preserve)
    specs = []
    try:
        from .maintenance_report import apply_records
        # A local-only inbound merge can migrate the pending rows' identities
        # before the daily outbound window. Reproduce that same exact branch.
        scoped = identity.historical(local)
        if scoped:
            from .maintenance_history_sync import receipts, inventory, append_batches
            expected = receipts(db, source)
            inventory(local, expected)
            positions = append_batches(base, batches, expected)
        if preserve and not scoped and identity.active(local) and not identity.active(base):
            identity.enable(base)
        # Reproduce the full local branch before assigning any legacy identity.
        for operation, saved in batches:
            results = positions[operation] if scoped else apply_records(base, [s['record'] for s in saved], operation=operation)
            specs.extend((operation, group, s['record'], position)
                         for group, (s, position) in enumerate(zip(saved, results)))
        if states(base) != states(local) or dict(base.defined_names) != dict(local.defined_names):
            raise EntryError('PUBLICATION_REPLAY_NOT_EXACT')
        if scoped:
            inventory(base, expected)
        legacy = not identity.active(base)
        if legacy:
            identity.enable(base)
        for operation, group, record, position in specs:
            if legacy or (scoped and operation not in expected):
                identity.attach(base, position, operation, group)
        known = identity.inventory(base, preserve_notes=preserve)
        return state, mine_hash, known, specs, identity.active(local)
    finally:
        base.close()
        local.close()


def proof_workbook(data, *, preserve=False):
    """Historical state comparison only; never serialize an unsupported package.

    Old accepted mine snapshots contain Excel revision/extensions that the old
    Writer had already normalized. They are evidence, not output candidates.
    All bytes actually imported/published still require identity.load().
    """
    try:
        return identity.load(data, preserve=preserve)
    except EntryError as exc:
        if not str(exc).startswith(('UNSUPPORTED_PACKAGE_FEATURE', 'UNSUPPORTED_PACKAGE_ATTRIBUTE')):
            raise
        book = validate_workbook(data)
        if identity.active(book):
            book.close()
            raise
        return book


# Exact private-share method accepted in chunked-20261117. This only writes a
# new Stage. It grants no UNC path, exclusion, publication or retry authority.
SMB_STAGE_TIMEOUT = 120
SMB_STAGE_WORKER = r"""
import hashlib, os, pathlib, sys
payload = sys.stdin.buffer.read()
if hashlib.sha256(payload).hexdigest() != sys.argv[2]:
    raise SystemExit(2)
path = pathlib.Path(sys.argv[1])
with path.open('xb', buffering=0) as stream:
    for offset in range(0, len(payload), 1024):
        chunk = payload[offset:offset + 1024]
        if stream.write(chunk) != len(chunk):
            raise SystemExit(3)
        stream.flush()
        os.fsync(stream.fileno())
if hashlib.sha256(path.read_bytes()).hexdigest() != sys.argv[2]:
    raise SystemExit(4)
"""


def stage_bytes(path, data):
    if str(path).startswith('\\\\'):
        import re
        if not re.fullmatch(r'\.maintenance-publish-[0-9a-f]{32}\.partial', path.name):
            raise EntryError('PUBLICATION_STAGE_NAME_DENIED')
        # The child bounds Create/Write/Flush/Close AND full readback. A killed
        # child leaves an untrusted private Stage; Replace is never reached.
        try:
            result = subprocess.run([sys.executable, '-B', '-c', SMB_STAGE_WORKER,
                                     str(path), digest(data)], input=data,
                                    capture_output=True, timeout=SMB_STAGE_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise EntryError('PUBLICATION_STAGE_TIMEOUT') from exc
        if result.returncode:
            code = ('PUBLICATION_STAGE_READBACK_FAILED' if result.returncode == 4
                    else 'PUBLICATION_STAGE_WRITE_FAILED')
            raise EntryError(code)
        return
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if digest(path.read_bytes()) != digest(data):
        raise EntryError('PUBLICATION_STAGE_READBACK_FAILED')


def checkpoint(stage):
    """Fault injection boundary for isolated crash/race tests; no production hook."""


def public_error_code(exc):
    code = getattr(exc, 'winerror', None)
    if code in {32,33}: return 'PUBLICATION_EXCEL_LOCKED'
    if code == 5 or isinstance(exc, PermissionError): return 'PUBLICATION_SHARE_PERMISSION_DENIED'
    if code in {51,53,54,58,59,64,67,121,1203,1231,1232,2250}: return 'SOURCE_UNAVAILABLE'
    import re
    text = str(exc).split(':',1)[0]
    return text if re.fullmatch('[A-Z][A-Z0-9_]{0,95}',text) else 'PUBLICATION_REVIEW_REQUIRED'


def observe_pending(db, operations, reason='', *, critical=False):
    """Late reports belong to tomorrow, never today's missed-window alert."""
    from . import maintenance_publication_alerts as notices
    moment = notices.now()
    cutoff = moment.astimezone(notices.TEHRAN).replace(hour=12, minute=55, second=0, microsecond=0)
    eligible = 0
    for operation in set(operations):
        row = db.execute('SELECT created FROM edits WHERE operation=?', (operation,)).fetchone()
        if row is None: raise EntryError('PUBLICATION_OPERATION_MISSING')
        created = datetime.fromisoformat(row[0])
        if created.utcoffset() is None: raise EntryError('PUBLICATION_OPERATION_TIME_INVALID')
        if created < cutoff: eligible += 1
    notices.observe(db, eligible if eligible or not critical else -1, reason, moment=moment)
    return eligible


@contextmanager
def publication_exclusion(upstream, *, value=None):
    """Owner accepted no human writers, closed Excel, noon to 13:00.

    This explicit operating contract is not a server-side exclusion claim.
    Existing Writer lock, owner-file, destination hash, window and readback
    checks still apply. Without the contract, retain the original guard.
    The configured mode also binds the permitted source, Share, staging path and Journal.
    """
    if (not value or value.get('operating_contract') != OWNER_LUNCH_CONTRACT
            or value.get('policy') != 'daily_1230'):
        raise EntryError('PUBLICATION_SMB_EXCLUSION_UNVERIFIED')
    if not window_open(value):
        raise EntryError('PUBLICATION_DEFERRED_WINDOW_CLOSED')
    yield


def cycle(staged, destination, upstream, *, config, runtime):
    settings = json.loads(Path(config).read_text('utf8'))
    source, upstream, value = isolated_paths(settings, upstream, runtime, staged, config)
    if Path(destination).resolve(strict=True) != source:
        raise EntryError('PUBLICATION_DESTINATION_DENIED')
    runtime = Path(runtime)
    with writer_lock(runtime):
        if not (runtime / 'audit.sqlite3').is_file():
            raise EntryError('PUBLICATION_JOURNAL_MISSING')
        with closing(open_audit(runtime)) as db:
            schema(db)
            try:
                return cycle_locked(db, source, upstream, Path(staged).read_bytes(), runtime, value)
            except Exception as exc:
                event(db, None, 'blocked', str(exc))
                if value.get('policy') == 'daily_1230':
                    from .maintenance_publication_alerts import observe
                    # Re-derive only from a verified Journal; corrupt evidence
                    # is an actionable error, never a false zero-pending result.
                    try:
                        branch = journal_branch(db, source, source.read_bytes(), runtime, upstream_copy=True, coordinated=True)
                        operations = {s[0] for s in branch[3]} if branch else set()
                        observe_pending(db, operations, public_error_code(exc), critical=True)
                    except Exception:
                        observe(db, -1, public_error_code(exc))
                raise


def cycle_locked(db, source, upstream, incoming, runtime, value):
    daily = value.get('policy') == 'daily_1230'
    with source_reader(source) as reader:
        original = reader.read()
    retain_snapshot(runtime, original)
    retain_snapshot(runtime, incoming)
    # Until the first eligible lunch tick, keep the existing inbound path. It
    # can accept a fresh mine Save while preserving the proven local branch.
    # Reconcile a changed input first; the next tick uses its verified hash.
    if value.get('mode') == 'production':
        branch = journal_branch(db, source, original, runtime, upstream_copy=True, coordinated=True)
        if branch:
            with closing(proof_workbook(snapshot(runtime,branch[1]),preserve=True)) as base:
                untagged = not identity.active(base)
            uncertain = db.execute("SELECT 1 FROM maintenance_publications_v1 WHERE status IN ('uncertain','prepared') LIMIT 1").fetchone()
            if untagged and not uncertain and (not window_open(value) or digest(incoming) != branch[1]):
                from .maintenance_baseline import sync_from_baseline
                result = sync_from_baseline(db,source,original,incoming,runtime,upstream_copy=True)
                observe_pending(db,set(result.get('protected_operations',[])))
                return result
    state, mine_hash, known, specs, local_active = replay_branch(db, source, original, runtime, preserve=daily)
    base = proof_workbook(snapshot(runtime, mine_hash), preserve=daily)
    acknowledged = identity.inventory(base, preserve_notes=daily)
    base_active, sheetnames = identity.active(base), base.sheetnames
    base.close()
    if daily and not known and not specs and not base_active:
        # No pending reports: retain the ordinary exact inbound behavior and
        # avoid adding markers or publishing merely to migrate an empty file.
        from .maintenance_baseline import sync_from_baseline
        result = sync_from_baseline(db, source, original, incoming, runtime, upstream_copy=True)
        from .maintenance_publication_alerts import observe
        observe(db, 0)
        return result
    mine = identity.load(incoming, preserve=daily)
    try:
        if mine.sheetnames != sheetnames:
            raise EntryError('PUBLICATION_SHEET_LIST_CHANGED')
        if base_active and not identity.active(mine):
            raise EntryError('IDENTITY_SCHEMA_MISSING')
        if not base_active:
            # Initial untagged migration is hash bound. Arbitrarily saved/edited
            # copies of a previously exported untagged branch need owner review.
            recorded = db.execute("SELECT identities FROM maintenance_publications_v1 WHERE before_hash=? "
                                  "AND candidate_hash=? AND status IN ('uncertain','prepared')",
                                  (mine_hash,digest(incoming))).fetchall()
            exact_recovery = (len(recorded)==1 and json.loads(recorded[0][0]) == known
                              and identity.inventory(mine,preserve_notes=daily)==known)
            if digest(incoming) not in {mine_hash, digest(original)} and not exact_recovery:
                raise EntryError('LEGACY_MIGRATION_MINE_VERSION_AMBIGUOUS')
            if digest(incoming) == digest(original) and not identity.active(mine):
                for operation, group, record, position in specs:
                    identity.attach(mine, position, operation, group)
            identity.enable(mine)
        present = identity.inventory(mine, preserve_notes=daily)
        if any(known.get(key) != item for key, item in present.items()):
            raise EntryError('IDENTITY_UNKNOWN_OR_CHANGED')
        if not set(acknowledged) <= set(present):
            raise EntryError('IDENTITY_PUBLISHED_RECORD_MISSING')
        pending = db.execute("SELECT * FROM maintenance_publications_v1 WHERE status IN ('prepared','uncertain')").fetchall()
        for attempt in pending:
            expected = json.loads(attempt['identities'])
            if any(present.get(key) != item for key, item in expected.items()):
                raise EntryError('PUBLICATION_UNCERTAIN_REVIEW_REQUIRED')
        from .maintenance_service import append_record
        for operation, group, record, position in specs:
            key = uuid.uuid5(identity.NAMESPACE, f"{operation}:{group}:{record['sheet']}").hex
            if key not in present:
                saved = append_record(mine, record, recorder=record['recorder'], independent=True)
                item = identity.attach(mine, saved, operation, group)
                if known.get(key) != item:
                    raise EntryError('IDENTITY_APPEND_NOT_EXACT')
        final_ids = identity.inventory(mine, preserve_notes=daily)
        if final_ids != known:
            raise EntryError('IDENTITY_CANDIDATE_INCOMPLETE')
        # Avoid reserializing an ordinary mine Save or an already published file.
        candidate = (incoming if identity.active(identity_book := identity.load(incoming, preserve=daily))
                     and identity.inventory(identity_book, preserve_notes=daily) == final_ids else identity.dump(mine))
        identity_book.close()
    finally:
        mine.close()
    remote_change = candidate != incoming
    if daily:
        from .maintenance_publication_alerts import observe
        observe_pending(db, {s[0] for s in specs} if remote_change else set())
        if remote_change and not window_open(value):
            result = dict(status='publication_pending', reason='PUBLICATION_DEFERRED_WINDOW_CLOSED',
                          records=len(final_ids), pending_operations=len({s[0] for s in specs}),
                          hash=digest(candidate), mine_hash=digest(incoming),
                          coordinated_publication=True, publication_pending=True,
                          protected_operations=list(dict.fromkeys(s[0] for s in specs)), baseline=state['event_id'])
            if candidate != original:
                # Preserve ordinary inbound reconciliation while outbound is
                # waiting. This is a local Writer transition, not a remote ack;
                # journal_branch retains the exact protected operation batches.
                retain_snapshot(runtime, candidate)
                with source_reader(source) as local, source_reader(upstream) as remote:
                    if digest(local.read()) != digest(original) or digest(remote.read()) != digest(incoming):
                        raise EntryError('PUBLICATION_DEFERRED_INPUT_CHANGED')
                    publish_candidate(source, original, candidate, db, 'mine-file-sync',
                                      dict(_kind=KIND, operation=uuid.uuid4().hex), result, runtime=runtime)
            return result
        if remote_change:
            # Explicit owner contract or the original fail-closed exclusion gate.
            with publication_exclusion(upstream, value=value):
                return publish_locked(db, source, upstream, original, incoming, candidate, runtime,
                                      value, specs, final_ids, pending, state, remote_change)
    return publish_locked(db, source, upstream, original, incoming, candidate, runtime,
                          value, specs, final_ids, pending, state, remote_change)


def publish_locked(db, source, upstream, original, incoming, candidate, runtime,
                   value, specs, final_ids, pending, state, remote_change):
    daily = value.get('policy') == 'daily_1230'
    if remote_change and not window_open(value):
        raise EntryError('PUBLICATION_DEFERRED_WINDOW_CLOSED')
    if upstream.with_name('~$' + upstream.name).exists():
        raise EntryError('PUBLICATION_DEFERRED_EXCEL_OWNER_FILE')
    publication = None
    if candidate == original and not remote_change:
        with source_reader(source) as local, source_reader(upstream) as remote:
            if digest(local.read()) != digest(original) or digest(remote.read()) != digest(incoming):
                raise EntryError('PUBLICATION_NOOP_INPUT_CHANGED')
        if pending and specs:
            recovered = dict(status='merged', hash=digest(original), mine_hash=digest(incoming),
                             records=len(final_ids), coordinated_publication=True, protected_operations=[],
                             publication=pending[-1]['publication'], baseline=state['event_id'])
            publish_candidate(source,original,original,db,'mine-file-sync',
                              dict(_kind=KIND,operation=uuid.uuid4().hex),recovered,runtime=runtime)
        for attempt in pending:
            transition(db, attempt['publication'], 'acked', 'identity readback recovery')
        return dict(status='unchanged', hash=digest(original), mine_hash=digest(incoming),
                    records=len(final_ids), coordinated_publication=True)
    retain_snapshot(runtime, candidate)
    publication = uuid.uuid4().hex
    with db:
        db.execute('INSERT INTO maintenance_publications_v1 VALUES (?,?,?,?,?,?,?)',
                   (publication, digest(incoming), digest(candidate), encoded(final_ids),
                    'prepared', '', datetime.now(timezone.utc).isoformat()))
    event(db, publication, 'prepared')
    temporary = upstream.with_name('.maintenance-publish-' + publication + '.partial')
    replace_attempted = False
    try:
        checkpoint('before_stage')
        if remote_change:
            stage_bytes(temporary, candidate)
        checkpoint('after_stage')
        # A short deny-write handle, exact destination recheck, owner-file check
        # and window check reduce races. They do NOT establish SMB/Excel CAS.
        with source_reader(source) as local, source_reader(upstream) as remote:
            if digest(local.read()) != digest(original):
                raise EntryError('PUBLICATION_LOCAL_CHANGED')
            if digest(remote.read()) != digest(incoming):
                raise EntryError('PUBLICATION_DESTINATION_CHANGED')
            if remote_change:
                if not window_open(value) or upstream.with_name('~$' + upstream.name).exists():
                    raise EntryError('PUBLICATION_DEFERRED_WINDOW_OR_OWNER')
                checkpoint('before_replace')
                replace_attempted = True
                replace_source(temporary, upstream)
            checkpoint('after_replace')
            with source_reader(upstream) as readback:
                published = readback.read()
            checked = identity.load(published, preserve=daily)
            try:
                if digest(published) != digest(candidate) or identity.inventory(checked, preserve_notes=daily) != final_ids:
                    raise EntryError('PUBLICATION_READBACK_UNCERTAIN')
            finally:
                checked.close()
            checkpoint('after_readback')
            result = dict(status='merged' if specs else 'imported', hash=digest(candidate),
                          mine_hash=digest(candidate), publication=publication,
                          coordinated_publication=True, records=len(final_ids),
                          protected_operations=[], baseline=state['event_id'])
            if candidate != original or remote_change:
                publish_candidate(source, original, candidate, db, 'mine-file-sync',
                                  dict(_kind=KIND, operation=uuid.uuid4().hex), result, runtime=runtime)
            checkpoint('before_ack')
            transition(db, publication, 'acked', 'exact bytes and row identities read back')
            for attempt in pending:
                transition(db, attempt['publication'], 'acked', 'recovered by row identity')
            if daily:
                from .maintenance_publication_alerts import observe
                observe(db, 0, success=remote_change)
            return result
    except Exception as exc:
        # In this LOCAL-ONLY transport, these Win32 sharing/access failures
        # establish that ReplaceFileW did not publish. Network errors never do.
        definite_lock_failure = isinstance(exc, OSError) and getattr(exc, 'winerror', None) in {5, 32, 33}
        transition(db, publication, 'uncertain' if replace_attempted and not definite_lock_failure else 'deferred', str(exc))
        raise
    finally:
        # UNC failures retain only the UUID Stage for explicit recovery. Do not
        # perform an unbounded SMB unlink after a timeout/unknown network result.
        # Successful Replace consumes Stage. Existing local cleanup is unchanged.
        if not str(temporary).startswith('\\\\'):
            temporary.unlink(missing_ok=True)


def poll_existing_tick(sync_status, *, config=None, runtime=None):
    """Called by the existing two-minute Cron even on outage/backoff ticks.

    Notification failure has no effect on Excel receipts or retry authority.
    This function never opens the remote workbook and never publishes it.
    """
    from .maintenance_service import CONFIG, RUNTIME
    from . import maintenance_publication_alerts as notices
    config, runtime = Path(config or CONFIG), Path(runtime or RUNTIME)
    settings = json.loads(config.read_text('utf8'))
    value = options(settings)
    if value is None or value.get('policy') != 'daily_1230':
        return dict(status='disabled')
    if value['mode'] == 'production':
        source = production_local(settings, config, runtime)
    else:
        root = Path(value['isolation_root']).resolve(strict=True)
        if not root.is_relative_to(CANDIDATE_ROOT.resolve()) or root == CANDIDATE_ROOT.resolve():
            raise EntryError('PUBLICATION_ISOLATION_REQUIRED')
        source = Path(settings['source']).resolve(strict=True)
        for path in (source, runtime.resolve(strict=True), config.resolve(strict=True)):
            if not path.is_relative_to(root) or any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in (path,*path.parents)):
                raise EntryError('PUBLICATION_PRODUCTION_PATH_DENIED')
    try:
        with writer_lock(runtime), closing(open_audit(runtime)) as db:
            schema(db)
            try:
                branch = journal_branch(db, source, source.read_bytes(), runtime, upstream_copy=True, coordinated=True)
                count = len({s[0] for s in branch[3]}) if branch else 0
                reason = str(sync_status).upper() if sync_status in {'source_unavailable','backoff_skip'} else ''
                # Keep the detailed guard reason recorded by the worker.
                notices.ensure(db)
                row = db.execute('SELECT reason,pending FROM maintenance_publication_watch_v1 ORDER BY updated DESC LIMIT 1').fetchone()
                previous_reason = reason or (row[0] if row and (count or row['pending']==-1) else '')
                count = observe_pending(db, {s[0] for s in branch[3]} if branch else set(), previous_reason,
                                        critical=bool(row and row['pending']==-1))
            except Exception:
                count = -1
                notices.observe(db, -1, 'PUBLICATION_INTEGRITY_REVIEW_REQUIRED')
            notices.notify(db, value)
    except sqlite3.DatabaseError:
        # An unreadable Writer journal must not silence its own integrity
        # alarm. Reuse the existing notifier's schema/claim/receipt/sender in
        # an isolated fallback ledger; never repair or replace that Journal.
        with closing(sqlite3.connect(runtime/'publication-notice-fallback.sqlite3')) as db:
            db.row_factory = sqlite3.Row
            notices.observe(db, -1, 'PUBLICATION_INTEGRITY_REVIEW_REQUIRED')
        count = -1
    fallback = runtime/'publication-notice-fallback.sqlite3'
    if fallback.exists():
        # Pending notices must also retry if the Writer journal recovers before
        # Bale does. The existing receipt prevents a duplicate on both paths.
        with closing(sqlite3.connect(fallback)) as db:
            db.row_factory = sqlite3.Row
            notices.notify(db, value)
    return dict(status='observed', pending_operations=count if 'count' in locals() else -1)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Notice-only hook for existing mine-file-sync')
    parser.add_argument('--poll', action='store_true', required=True)
    parser.add_argument('--sync-status', required=True)
    args = parser.parse_args()
    print(json.dumps(poll_existing_tick(args.sync_status)))
