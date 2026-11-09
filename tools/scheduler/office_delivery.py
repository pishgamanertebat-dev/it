"""Same office generators, independent recipient/artifact delivery receipts."""
import asyncio
import hashlib
from pathlib import Path
import tempfile
from tools.authorization.office_delivery import OFFICE_TASKS


def deliver(task, params, *, authorization, on_receipt=None, closed=None, on_artifact_receipt=None):
    from . import tasks
    from .no_data import notify_missing
    from .misfire import outcome_from_receipts
    from tools.fleet.overflow.report import validate_date
    from integrations.hermes.function_domain.driver_report import build_driver_pdf, SECTIONS
    tasks.validate_overflow(params)
    capability = OFFICE_TASKS[task]
    closed = closed or {}
    def recipients():
        result = authorization.resolve_active_recipients(capability)
        if result.status == 'store_unavailable':
            raise tasks.TransportUnavailable('Office authorization temporarily unavailable')
        return result.recipients if result.status == 'ready' else ()
    users = tuple(recipients())
    if not users:
        return {'status': 'skipped', 'reason': 'store_unavailable' if authorization.resolve_active_recipients(capability).status == 'store_unavailable' else 'no_active_holder'}
    sent, uncertain = tasks.closed_sets(closed)
    prior = closed.get('artifacts', {})
    target = validate_date(params['date']) if params.get('date') else tasks.overflow_report_date()
    runtime = tasks.ROOT / 'runtime/scheduler'
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='office-', dir=runtime) as directory:
        failures={}
        if task == 'overflow':
            result = asyncio.run(tasks.build_report(target, directory))
            if not result.get('ok'):
                reason = result.get('reason') or tasks.classify_source_message(result.get('message', ''))
                if reason == 'date_missing':
                    return notify_missing('گزارش سرریز روزانه', target, result.get('latest_date'), recipients, on_receipt=on_receipt, closed=closed)
                return {'status': 'failed' if reason == 'invalid_source' else 'skipped', 'reason': reason}
            if not result.get('images'):
                raise ValueError('Overflow contains no images')
            caption = tasks.overflow_report_caption(result['report']['date'], target)
            artifacts = [(f'page:{i}', Path(p), caption if i == 0 else '', False) for i, p in enumerate(result['images'])]
        else:
            result = build_driver_pdf(target, directory, missing_result=True, allow_partial=True)
            report = result['report']
            if report['date'] != target:
                raise ValueError('Driver output date mismatch')
            if report.get('sections') and all(v['status'] == 'date_missing' for v in report['sections'].values()):
                return notify_missing('گزارش روزانه رانندگان', target, report.get('latest_date'), recipients, on_receipt=on_receipt, closed=closed, notify=False)
            failures=result.get('failures',{})
            if len(result['documents'])+len(failures) != 2:
                raise ValueError('Driver output must contain two PDFs')
            paths=result.get('artifacts',dict(zip(SECTIONS,result['documents'])))
            artifacts = [(s, Path(paths[s]) if s in paths else None,
                          report['sections'][s]['title']+' '+target,True) for s in SECTIONS]
        # Persist every obligation before transport, including the second PDF.
        states = {};blocked=set()
        for user in users:
            for artifact, path, caption, pdf in artifacts:
                state = prior.get((user, artifact), 'pending')
                if user in sent:
                    state = 'sent'  # Honor legacy aggregate receipts on upgrade.
                elif user in uncertain and not any(u == user for u, a in prior):
                    state = 'uncertain'
                if state not in {'sent','uncertain','revoked'} and artifact in failures:
                    state='failed';blocked.add(artifact)
                states[user, artifact] = state
                if on_artifact_receipt:
                    on_artifact_receipt(user, artifact, state, None, None)
        sender = None
        try:
            for user in users:
                for artifact, path, caption, pdf in artifacts:
                    if states[user, artifact] in {'sent', 'uncertain', 'revoked'}:
                        continue
                    if artifact in failures:
                        continue
                    def receipt(state, message_id=None, digest=None):
                        states[user, artifact] = state
                        if on_artifact_receipt:
                            on_artifact_receipt(user, artifact, state, message_id, digest)
                    if user not in recipients():
                        receipt('revoked')
                        continue
                    attempted = False
                    try:
                        data = path.read_bytes()
                        if pdf and not data.startswith(b'%PDF-'):
                            raise ValueError('Invalid office PDF')
                        digest = hashlib.sha256(data).hexdigest()
                        expected_hash=result.get('validation',{}).get(artifact,{}).get('pdf_sha256')
                        if expected_hash and digest!=expected_hash:
                            from tools.fleet.repairs.pdf_validation import DriverPDFInvalid
                            failures[artifact]=DriverPDFInvalid(report,artifact,'visibility_uncertain')
                            blocked.add(artifact)
                            receipt('failed')
                            continue
                        if sender is None:
                            sender = tasks.BaleSender()
                        sender.check_connection()
                        if user not in recipients():
                            receipt('revoked')
                            continue
                        receipt('sending', digest=digest)
                        attempted = True
                        mid = sender.document(user, path, caption) if pdf else sender.photo(user, path, caption)
                    except tasks.TransportUnavailable:
                        receipt('failed')
                    except Exception:
                        receipt('uncertain' if attempted else 'failed')
                    else:
                        receipt('sent', None if mid is None else str(mid), digest)
                values = [v for (u, a), v in states.items() if u == user]
                aggregate = outcome_from_receipts(values)
                state = 'failed' if aggregate in {'partial', 'retry_wait'} else {'succeeded': 'sent', 'skipped': 'revoked'}.get(aggregate, aggregate)
                tasks.note_receipt(on_receipt, user, state)
        finally:
            if sender is not None:
                sender.close()
        outcome = outcome_from_receipts(states.values())
        if blocked:
            failure=next((failures[s] for s in blocked if failures[s].code=='driver_pdf_content'),
                         failures[next(iter(blocked))])
            return {'status':'failed','reason':failure.code,'failure_details':failure.details,
                    'sent_count':sum(v=='sent' for v in states.values())}
        retry = [u for u in users if any(states[u, a] in {'pending', 'failed'} for a, p, c, pdf in artifacts)]
        unknown = [u for u in users if any(states[u, a] == 'uncertain' for a, p, c, pdf in artifacts)]
        if retry:
            return {'status': 'failed', 'reason': 'transport_unavailable', 'retry_recipients': retry, 'uncertain_recipients': unknown,
                    'sent_count': sum(v == 'sent' for v in states.values())}
        if outcome == 'uncertain':
            return {'status': 'uncertain', 'reason': 'uncertain', 'uncertain_recipients': unknown}
        return {'status': 'skipped', 'reason': 'recipient_revoked'} if outcome == 'skipped' else {'status': 'succeeded'}
