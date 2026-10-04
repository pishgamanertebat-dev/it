"""Small callback-based execution primitives. No domain or authorization policy.

Operation deadlines belong to callbacks: no Future timeout, cancellation, or
executor shutdown behavior is imposed on a caller. Hosts enforce capabilities.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
import re
import secrets
import subprocess
import threading
import time
from typing import Callable, Mapping


def normalized(value):
    return re.sub(r"\s+", " ", value).strip().casefold()


def select_policy(source, existing, *, keep_section, keep_paragraph):
    """Read a source fully; select/deduplicate without interpreting its domain.

    Callbacks receive original headings/paragraphs. Unknown headings are retained
    when the adapter's predicate permits them; order and substring dedup match
    the existing prepared-context mechanism.
    """
    seen = normalized(existing)
    output, headings = [], []
    for section in re.split(r"(?m)(?=^#{2,3} )", source):
        heading = section.splitlines()[0] if section.strip() else ""
        if not keep_section(heading):
            continue
        kept = []
        for paragraph in re.split(r"\n\s*\n", section.strip()):
            key = normalized(paragraph)
            if not key or key in seen or not keep_paragraph(paragraph):
                continue
            kept.append(paragraph)
            seen += " " + key
        if kept and any(not item.startswith("#") for item in kept):
            output.append("\n\n".join(kept))
            headings.append(heading)
    return "\n\n".join(output), headings


def timed(operation, function, *, recoverable_errors=(Exception,)):
    start = time.perf_counter()
    try:
        result = function()
        return operation, result, round(time.perf_counter() - start, 3)
    except recoverable_errors:
        return operation, {"success": False, "error": operation + " failed; required evidence must be reported missing."}, round(time.perf_counter() - start, 3)


def run_parallel(operations, *, recoverable_errors=(Exception,)):
    """Run named callbacks concurrently; aggregate in submission order.

    Adapters can retain a narrower recoverable-error boundary. Unhandled
    exceptions propagate after executor cleanup; adapters own process deadlines
    and timeout translation. There is no model call or retry here.
    """
    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        futures = [pool.submit(timed, name, function, recoverable_errors=recoverable_errors) for name, function in operations]
        completed = [future.result() for future in futures]
    return {name: result for name, result, _ in completed}, {name: seconds for name, _, seconds in completed}


class NextAction(str, Enum):
    FINISH = "finish"
    FINISH_READ = "finish_read"
    REFINE = "refine"
    STOP = "stop"
    JUDGE = "judge"


def coverage_next_action(coverage, *, exhausted=False):
    status = (coverage or {}).get("status")
    if status == "complete":
        return NextAction.FINISH
    if status == "truncated":
        return NextAction.FINISH_READ
    if status == "incomplete":
        return NextAction.STOP if exhausted else NextAction.REFINE
    return NextAction.JUDGE


def consume_attempt(counts, key, *, limit, lock, error):
    """Caller-owned budget and scope; count before dispatch as before."""
    with lock:
        if counts.get(key, 0) >= limit:
            raise ValueError(error)
        counts[key] = counts.get(key, 0) + 1


def remember_failure(failures, key, error, *, pattern, lock):
    """Caller decides what is definitive and when turn-scoped state is cleared."""
    if re.search(pattern, error):
        with lock:
            failures.add(key)


def start_sidecar(operation, *, ready, slot, on_timeout, on_error, name):
    """Publish once from a daemon thread; never join or wait on its result.

    Event publication follows the complete slot update. A caller may snapshot
    at its existing evidence boundaries. Callback owns process deadline/reaping.
    """
    def work():
        started = time.perf_counter()
        try:
            result = operation()
        except subprocess.TimeoutExpired:
            result = on_timeout()
        except Exception:
            result = on_error()
        slot.update(result=result, started=started, finished=time.perf_counter())
        ready.set()
    try:
        threading.Thread(target=work, name=name, daemon=True).start()
    except RuntimeError:
        return False
    return True


def issue_receipt(pending, *, lock, ttl):
    """Opaque one-use attestation; TTL and state are caller-owned."""
    token = secrets.token_hex(16)
    with lock:
        now = time.monotonic()
        for stale in [key for key, when in pending.items() if now - when > ttl]:
            pending.pop(stale, None)
        pending[token] = now
    return token


def claim_receipt(pending, token):
    """Call while holding the caller's registry lock. Never grants tools."""
    return bool(token and pending.pop(token, None) is not None)


def bind_task(task, *, context, goal):
    """Preserve the adapter's original goal verbatim and other task fields."""
    return dict(task, context=context, goal=goal)


@dataclass(frozen=True)
class Operation:
    """A host registration descriptor; receives policy/schema from its adapter."""
    name: str
    toolset: str
    schema: Mapping
    handler: Callable
    description: str
    check_fn: Callable | None = None


def register_operations(ctx, operations):
    """Register direct schemas with the existing host, without widening tools.

    Inline schema selection and capability ceilings remain host policy. This
    function adds no discovery tools, toolsets, prompts or delegated workflow.
    """
    for op in operations:
        options = dict(name=op.name, toolset=op.toolset, schema=op.schema,
                       handler=op.handler, description=op.description)
        if op.check_fn is not None:
            options['check_fn'] = op.check_fn
        ctx.register_tool(**options)
