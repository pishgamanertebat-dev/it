"""Shared curated technical evidence; legacy tool names preserve active-session compatibility."""
from __future__ import annotations
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
from integrations.hermes.shared_fast_core import (
    Operation, NextAction, normalized, select_policy, coverage_next_action,
    consume_attempt, remember_failure, start_sidecar, issue_receipt, claim_receipt,
    bind_task, register_operations,
)
MARKER = "KOMATSO_MANUAL_TASK_V3 "
PREPARED = "KOMATSO_MANUAL_PREPARED_V3"
TOOLSET = "komatso_technical_docs"
TOOL = "maintenance_manual_evidence"
PART_TOOL = "maintenance_partbook_lookup"
# Expand only after another model index is production-ready.
INDEXED_PART_MODELS = {"HD785-7", "WA600-6", "HD785-5", "HD465-7R", "PC1250SP-8R", "PC800-8"}
PART_ENRICHMENT_BUDGET_SECONDS = 1.0
PART_ENRICHMENT_LIMIT = 4
_request_enrichments = {}
_registry_lock = threading.Lock()
_pending = {}
_prepared_sessions = set()
_child_sessions = set()



def source_intent(question):
    """Source priority for dispatch and rule filtering; no model/PN special cases.

    Parent follows the semantic source contract for component-name requests.
    """
    text = normalized(str(question)).replace("\u200c", " ")
    part = bool(re.search(
        r"شماره\s*(?:فنی|قطعه)|پارت\s*(?:نامبر|بوک)|part\s*(?:number|no\b|book)|"
        r"parts\s*book|\bpn\b|\bfigure\b|\bitem\b|exploded\s*view|نمای\s*انفجاری|"
        r"\b[0-9A-Za-z]{3,5}-[0-9A-Za-z]{2,3}-[0-9A-Za-z]{4,5}\b", text, re.I))
    technical = bool(re.search(
        r"\b(?:fault|symptom|troubleshoot\w*|test\w*|adjust\w*|pressure|voltage|wiring|"
        r"error\s*code|operation|specification\w*|diagnos\w*|fail\w*|weak|leak\w*)\b|"
        r"خراب|عیب|مشکل|علت|تست|آزمایش|تنظیم|فشار|ولتاژ|سیم\s*کشی|خطا|ضعیف|ضعف|نشتی|"
        r"نمی|نقشه\s*برق|نحوه\s*کار|مشخصات\s*فنی", text, re.I))
    return "mixed" if part and technical else "part" if part else "technical"


def select_rules(device_text, existing, question, presentation=False, part_enrichment=False):
    """Select policy sections by general request intent, never manual pages/faults.

    Read the entire source. Keep model/source/variant/safety constraints and the
    applicable test policies; omit duplicate root workflow and manual tool
    mechanics replaced by the batches. Presentation is kept only for the agent
    that writes the user answer. Unknown substantive headings are retained.
    """
    diagnostic = bool(re.search(r"test|fault|fail|symptom|pressure|heavy|weak|slow|start|leak|\bnot\b|تست|خراب|خطا|نمی|علت|فشار|سنگینی|ضعف|ضعیف|کند|استارت|نشتی", question, re.I))
    parts = part_enrichment or source_intent(question) in {"part", "mixed"} or bool(re.search(r"order|سفارش", question, re.I))
    code = bool(re.search(r"code|کد|خطا", question, re.I))
    mechanics = ("FAST ", "MINIMIZE TOOL", "WINDOWS EXECUTION", "WEB SEARCH PERFORMANCE", "انتخاب سریع منبع")
    answer = ("سبک پاسخ", "قالب پاسخ", "تصویر و نقشه", "کامل بودن", "IMAGE DELIVERY")
    skip = mechanics if presentation else mechanics + answer
    test = ("روش توضیح", "تست برقی", "تست فشار", "بررسی ظاهری", "خطاهای سنسوری", "سؤال های غیر")
    def keep_section(heading):
        if any(item.casefold() in heading.casefold() for item in skip):
            return False
        if any(item in heading for item in test) and not diagnostic:
            return False
        if "فقط کد خطا" in heading and not code:
            return False
        if ("PART" in heading.upper() or "قطعه" in heading) and not parts:
            return False
        return True

    def keep_paragraph(paragraph):
        return parts or not re.search(r"part.?number|partbook|پارت بوک|شماره قطعه|اگر شماره قطعه|برای سفارش", paragraph, re.I)

    return select_policy(device_text, existing, keep_section=keep_section, keep_paragraph=keep_paragraph)


from integrations.hermes.technical_docs_boundary import technical_docs_enabled, runtime_home
# Import by path: Hermes owns a different top-level `tools` package.
import importlib.util
_resolver_spec = importlib.util.spec_from_file_location(__name__ + "_fleet_resolver", ROOT / "tools/fleet/partbook_resolver.py")
_resolver = importlib.util.module_from_spec(_resolver_spec)
_resolver_spec.loader.exec_module(_resolver)
resolve_model, load_registry = _resolver.resolve_model, _resolver.load_registry

# Historical symbol retained for integrations; permission is the explicit technical surface.
is_maintenance = technical_docs_enabled


def manual_models():
    return json.loads((ROOT / "tools/manual_models.json").read_text(encoding="utf-8"))


def verified_device(model, question):
    models = manual_models()
    if model not in models or not isinstance(question, str) or not question.strip():
        raise ValueError("Verified model and original question required")
    return ROOT / models[model] / "AGENTS.md"


def part_model_names():
    """Part tool models. Shop Manual registration stays in manual_models.json."""
    return set(manual_models()) | set(INDEXED_PART_MODELS)


def indexed_part_available(model):
    resolution = resolve_model(model)
    if resolution["resolution_status"] != "RESOLVED":
        return False
    registry = load_registry()
    asset = resolution["fleet_asset"]
    return any(registry.get("sources", {}).get(book, {}).get("expected_model", asset["display_model"])
               in INDEXED_PART_MODELS for book in asset["book_ids"])


def write_request(home, seed, model, question):
    request_dir = ROOT / "runtime/manual-worker-requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(seed.encode()).hexdigest()[:24]
    request_file = request_dir / (digest + ".json")
    request_file.write_text(json.dumps({"model":model,"question":question,"profile_home":str(home)},ensure_ascii=False),encoding="utf-8")
    return digest, request_file


def prepare_args(args, home, session_id=""):
    if not is_maintenance(home):
        return None
    tasks = args.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 2:
        return None
    matches = [i for i, task in enumerate(tasks) if isinstance(task, dict)
               and str(task.get("context", "")).startswith(MARKER)]
    if len(matches) != 1:
        return None
    index = matches[0]
    task = tasks[index]
    first, _, original = task["context"].partition("\n")
    request = json.loads(first[len(MARKER):])
    model, question = request["model"], request["question"]
    device = verified_device(model, question)
    # Root is already in the native child system prompt; compare, do not inject.
    root_rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    rules, headings = select_rules(device.read_text(encoding="utf-8"), root_rules + "\n" + original, question)
    contract = (Path(__file__).parent / "MANUAL_WORKER.md").read_text(encoding="utf-8")
    # Load the matching orchestration skill before any dependent retrieval.
    skill = Path(__file__).parent / "TECHNICAL_PREPARATION.md"
    if not skill.is_file():
        raise ValueError("Required shared Technical workflow is missing")
    skill_text = skill.read_text(encoding="utf-8")
    if not skill_text.startswith("1. **Technical"):
        raise ValueError("Unsupported shared Technical workflow structure")
    skill_delta = skill_text.strip()
    skill_delta = re.sub(r"Load the relevant device .*? before device sources\.", "Device instructions were read in full by preparation before source access.", skill_delta, flags=re.S)
    _, request_file = write_request(home, session_id + task.get("goal", "") + question, model, question)
    # The question already present in metadata/original context is never repeated.
    question_context = "" if question in original else "Original question: " + question
    context = "\n\n".join(part for part in (
        PREPARED + "\nVerified model: " + model,
        "Device AGENTS was read IN FULL by the dispatcher before source retrieval; applicable unique policy follows: " + str(device) + "\n" + rules if rules else "Device instructions already present; no reinjection.",
        "Shared Technical workflow loaded by dispatcher; its Technical scope follows. No separate skill_view is needed.\n" + skill_delta,
        contract if normalized(contract) not in normalized(original) else "",
        "Prepared request file: " + request_file.as_posix(), question_context, "Background machine facts (preserve reported symptoms/codes; do not broaden the original question):\n" + original.strip() if original.strip() else "") if part)
    token = issue_receipt(_pending, lock=_registry_lock, ttl=3600)
    changed = bind_task(task, context=context, goal=(
        "Technical/Manual evidence for " + model + ". Answer the exact original question in context. "
        "Use the prepared indexed retrieve/finish batches. Fleet facts are background, not additional "
        "diagnostic questions. Preserve incomplete reported codes as uncertainty; require their full "
        "displayed failure code before code-specific diagnosis. Return safe documented checks, "
        "supported values, precise sources and validated page images.\nPreparation receipt: " + token))
    result_tasks = list(tasks)
    result_tasks[index] = changed
    return dict(args, tasks=result_tasks)


def pre_tool_call(tool_name, args, session_id="", **kwargs):
    if tool_name != "delegate_task":
        return None
    from hermes_constants import get_hermes_home
    try:
        updated = prepare_args(args, get_hermes_home().resolve(), session_id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"action":"block", "message":"Shared Technical preparation failed: " + str(exc)}
    return {"action":"modify", "args":updated} if updated else None


def subagent_start(child_session_id="", child_goal="", **kwargs):
    receipt = re.search(r"\nPreparation receipt: ([0-9a-f]{32})$", child_goal)
    if child_session_id:
        with _registry_lock:
            _child_sessions.add(child_session_id)
            if claim_receipt(_pending, receipt.group(1) if receipt else None):
                _prepared_sessions.add(child_session_id)


def subagent_stop(child_session_id="", session_id="", **kwargs):
    with _registry_lock:
        _prepared_sessions.discard(child_session_id or session_id)
        _child_sessions.discard(child_session_id or session_id)


def prepared_policy(info):
    if info.get("platform") != "subagent" or not technical_docs_enabled(Path(info.get("profile_home") or __import__("hermes_constants").get_hermes_home())):
        return ""
    with _registry_lock:
        ready = info.get("session_id") in _prepared_sessions
    if not ready:
        return ""
    return (
        "This Technical child has a trusted Technical Docs preparation receipt. "
        "Before the child started, the dispatcher read the complete selected device AGENTS.md "
        "and the shared Technical workflow. All applicable unique device policies and "
        "the relevant Technical workflow are in task context; root rules are already loaded. "
        "The instruction-loading prerequisite is satisfied. Do not call read_file to reload "
        "AGENTS or skill_view for the already loaded orchestration skill. Start the supplied "
        "single retrieve batch, then one finish batch with all needed follow-ups. "
        "Follow the original question, not incidental fleet codes or Parent goal expansions. "
        "Incomplete action codes do not identify a unique failure: request the complete displayed "
        "failure code; do not sweep code chapters for guesses. Use indexed probe retrieval; "
        "whole-manual fallback belongs to the probe only when its index cannot resolve the request. "
        "Retain source, safety and evidence requirements."
    )


MAX_RETRIEVES = 3
_requests = {}
_request_keys = {}
_retrieves = {}
_web_failures = set()

TOOL_DESCRIPTION = (
    "Shared fast Shop Manual path for diagnosis, tests, adjustments or specifications. "
    "Determine source intent first: Part Number/Parts Book/figure/item/identification requests use "
    "maintenance_partbook_lookup FIRST. Verified Part-only needs no Shop Manual/web. Mixed requests "
    "use Part Book identification plus this tool for technical evidence. "
    "phase=retrieve first reads the selected device AGENTS.md IN FULL and returns its applicable policy; "
    "this satisfies the root requirement to load machine-specific rules before source access, so do not "
    "read that file again. It then concurrently runs the manual_sections-routed evidence packet (bounded "
    "actual Shop Manual topic text; the index is routing only, never evidence) and the configured web_search. "
    "Optional part_query: short English component/assembly explicitly identified in the request, "
    "supplied with these keywords in this SAME retrieve call (e.g. steering pump). No guessed PN, "
    "separate model call or separate Part tool for technical enrichment. Omit/null for generic symptoms "
    "or a pure error code without an identified component. Local verified Part Book enrichment runs "
    "concurrently for indexed models, with a 1s budget and NO wait after manual retrieval. Only include "
    "ready VERIFIED candidates that help the question; candidates are alternatives, not a diagnosis "
    "or guaranteed fit. Skip failures/misses/ambiguity silently for optional enrichment; do not retry "
    "or delay the technical answer. No enrichment rendering by default. "
    "phase=finish concurrently renders and validates the chosen genuine pages, reads bounded missing page "
    "text and web_extracts one URL taken from the retrieve results; it returns MEDIA paths for delivery. "
    "Normal path: retrieve, one evaluation, finish, answer. Read evidence_coverage.status on the result. "
    "complete: finish and do not retrieve again. truncated: finish with read_pages for the listed resume pages, "
    "do not retrieve again. incomplete: retrieve again with refined keywords, then broad=true if still incomplete. "
    "Do not call web_search, web_extract, PDF scripts or render_page separately for this question. Delegated children may use only inherited parent tools."
)


def tool_schema(models):
    pages = {"type": "array", "items": {"type": "integer", "minimum": 1}, "maxItems": 8}
    return {"name": TOOL, "description": TOOL_DESCRIPTION, "parameters": {
        "type": "object", "required": ["phase"], "properties": {
            "phase": {"type": "string", "enum": ["retrieve", "finish"]},
            "model": {"type": "string", "enum": sorted(models),
                      "description": "retrieve: verified model; ask the user when it cannot be safely identified"},
            "question": {"type": "string", "description": "retrieve: exact original user question, verbatim"},
            "keywords": {"type": "string", "description": (
                "retrieve: English Shop Manual terms for the affected system/component and symptom, "
                "normalizing colloquial, abbreviated or misspelled user wording. Do not add guessed causes.")},
            "part_query": {"type": ["string", "null"], "maxLength": 80, "description": (
                "retrieve: optional short English component/assembly from the request, e.g. boom foot pin "
                "or steering pump, produced with keywords in this same call. Omit/null when no component "
                "is identified (generic overheating or error code alone). Never guess a PN or cause.")},
            "fault_code": {"type": "string", "description": "retrieve: complete displayed failure code only; never an incomplete action code"},
            "broad": {"type": "boolean", "description": (
                "retrieve fallback only: scan the whole Shop Manual (10-30 s) after an indexed packet "
                "missed the relevant topic")},
            "request_id": {"type": "string", "description": "finish: request_id returned by retrieve"},
            "render_pages": dict(pages, description="finish: smallest sufficient image set, usually 1-4"),
            "read_pages": dict(pages, description="finish: only pages with a material text gap"),
            "web_url": {"type": "string", "description": "finish: one directly relevant URL from retrieve web_search results"},
        }}}


def run_batch(arguments, session_id):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if session_id:
        env["HERMES_SESSION_ID"] = session_id
    completed = subprocess.run(
        [str(ROOT / ".venv/Scripts/python.exe"), str(ROOT / "tools/manual_worker_batch.py"), *map(str, arguments)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240, env=env, cwd=str(ROOT),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ValueError(detail[-1] if detail else "Manual batch failed")
    return json.loads(completed.stdout)


def evidence_followup(coverage, broad=False):
    """Tell the parent the next tool call. A complete topic is not a second retrieve."""
    action = coverage_next_action(coverage, exhausted=broad)
    reason = (coverage or {}).get("reason") or ""
    if action == NextAction.FINISH:
        return (
            "evidence_coverage.status is complete. A documented troubleshooting or test topic in this packet "
            "already covers the requested component or generic symptom and its text is complete. Call phase=finish with this "
            "request_id and the smallest sufficient render_pages. Do not retrieve again. Other index hits, "
            "adjacent faults, and a cross-reference already written in that topic are not missing evidence. "
            "Use read_pages only when a required value, test condition, or safety step is not already in the "
            "complete topic. If that text does not contain the specific procedure, say it is not documented; "
            "do not guess. " + reason
        )
    if action == NextAction.FINISH_READ:
        return (
            "evidence_coverage.status is truncated. The matching topic is already in this packet but the page "
            "limit cut it off. Do not retrieve again. Call phase=finish and pass read_pages for "
            "evidence_coverage.resume_at_pdf_pages. " + reason
        )
    if action == NextAction.STOP:
        return (
            "evidence_coverage.status is incomplete after a whole-manual search. Do not retrieve again. "
            "Answer from the relevant pages only, and state what the manual does not document. "
            "Never invent values, procedures, or safety conditions. " + reason
        )
    if action == NextAction.REFINE:
        missing = ", ".join((coverage or {}).get("missing_component_terms") or []) or "the requested component"
        return (
            "evidence_coverage.status is incomplete. No complete troubleshooting or test topic covers: "
            + missing + ". Retrieve again with refined English Shop Manual keywords for those missing terms. "
            "If that packet is still incomplete, retrieve with broad=true. Do not finish as if the missing "
            "procedure were documented. " + reason
        )
    return (
        "Evaluate once, then call phase=finish with this request_id and every needed follow-up together. "
        "If the packet lacks the relevant topic, retrieve again with refined keywords, then broad=true. "
        "If evidence is still missing, state the gap or ask for the missing detail; never fill it by guesswork."
    )


def valid_part_query(query):
    """Accept a bounded English name, not a symptom dump, PN or invented model."""
    return (isinstance(query, str) and 0 < len(query.strip()) <= 80
            and len(query.split()) <= 8
            and bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9 '/().-]*", query.strip()))
            and not re.search(r"[0-9A-Za-z]{3,5}-[0-9A-Za-z]{2,3}-[0-9A-Za-z]{4,5}", query))


def enrichment_lookup(model, query, session_id):
    """One isolated read-only CLI; no Hermes context/model call or rendering."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if session_id:
        env["HERMES_SESSION_ID"] = session_id
    command = [str(ROOT / ".venv/Scripts/python.exe"), str(ROOT / "tools/fleet/partbook_lookup.py"),
               "--model", model, "--query", query.strip(), "--verify", "--limit", str(PART_ENRICHMENT_LIMIT)]
    if os.environ.get("PARTBOOK_TEST_DB"):
        command += ["--db", os.environ["PARTBOOK_TEST_DB"]]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=PART_ENRICHMENT_BUDGET_SECONDS,
        cwd=str(ROOT), env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode:
        raise ValueError("Optional Part Book lookup failed")
    return json.loads(completed.stdout)


def compact_enrichment(packet, query):
    """Only verified, named rows anchored to the component query are exposed.

    The final noun in the short English query must anchor the row description.
    A figure hit on incidental piping/bolts is not identification of its filter.
    Parent still checks requested context and distinguishes variants/assemblies.
    """
    terms = re.findall(r"[a-z]+", query.casefold())
    candidates = []
    for row in packet.get("candidates") or []:
        description = row.get("description") or ""
        words = re.findall(r"[a-z]+", description.casefold())
        if ((row.get("pdf_verification") or {}).get("status") != "VERIFIED"
                or not row.get("part_number") or not words
                or not terms or not any(word.startswith(terms[-1]) for word in words)):
            continue
        candidates.append({key: row.get(key) for key in (
            "book_id", "actual_source_model", "cover_model", "model_match_status", "source_citation",
            "source_serial_coverage", "part_found_status", "part_applicability_confirmed",
            "applicability_status", "applicability_reasons", "figure", "figure_title", "item",
            "part_number", "description", "quantity", "applicability")}
                          | {"verification": "VERIFIED"})
    return {"status": "verified" if candidates else "miss" if not packet.get("found") else "unusable",
            "query": query, "candidates": candidates[:PART_ENRICHMENT_LIMIT],
            "resolution": packet.get("resolution"), "selected_part_books": packet.get("selected_part_books"),
            "coverage_complete": bool(packet.get("coverage_complete")),
            "coverage_note": packet.get("coverage_note", "Coverage unknown; index miss is not evidence of absence."),
            "lookup_timing_ms": packet.get("timing_ms"),
            "use": "Optional identification only if useful to this question. Rows may be different assemblies/variants; "
                   "VERIFIED means PDF row verified, not confirmed fit or fault. Preserve applicability and partial "
                   "coverage. Do not retry optional enrichment, guess a PN, or render by default. Miss is not absence."}


def run_retrieval(command, session_id, model, part_query=None, request_id=""):
    """Manual/web batch is authoritative; optional daemon work never joins it.

    subprocess.run kills/reaps an over-budget child in the worker. At the manual
    completion boundary only an already published result is read: no future wait,
    executor shutdown/join, model round trip, or late result after final delivery.
    """
    origin = time.perf_counter()
    ready = threading.Event()
    slot = {}
    reason = ("unindexed_model" if not indexed_part_available(model) else
              "no_query" if part_query is None or part_query == "" else
              "invalid_query" if not valid_part_query(part_query) else "")
    enrichment = {"status": "skipped", "reason": reason, "candidates": []}
    if not reason:
        launched = start_sidecar(
            lambda: compact_enrichment(enrichment_lookup(model, part_query, session_id), part_query),
            ready=ready, slot=slot,
            on_timeout=lambda: {"status": "timeout", "candidates": []},
            on_error=lambda: {"status": "error", "candidates": []},
            name="maintenance-part-enrichment")
        if not launched:
            enrichment = {"status": "error", "candidates": []}
            reason = "worker_unavailable"
    manual_started = time.perf_counter()
    retrieval = run_batch(command, session_id)
    manual_finished = time.perf_counter()
    # Event publication makes the snapshot atomic; it is never waited on.
    part_ready = ready.is_set()
    if part_ready:
        enrichment = slot["result"]
    elif not reason:
        enrichment = {"status": "not_ready", "candidates": []}
    # Keep the compact result available at the existing final-evidence boundary.
    if not reason and request_id:
        with _registry_lock:
            for stale in [key for key, job in _request_enrichments.items() if time.monotonic() - job[3] > 300]:
                _request_enrichments.pop(stale, None)
            _request_enrichments[request_id] = (session_id, ready, slot, time.monotonic())
    finished = time.perf_counter()
    metrics = {"manual_batch_ms": round((manual_finished - manual_started) * 1000, 2),
               "wall_ms": round((finished - origin) * 1000, 2),
               "budget_ms": PART_ENRICHMENT_BUDGET_SECONDS * 1000, "waiting_ms": 0,
               "part_launched": not bool(reason)}
    if part_ready:
        metrics.update(part_duration_ms=round((slot["finished"] - slot["started"]) * 1000, 2),
                       part_started_ms=round((slot["started"] - origin) * 1000, 2),
                       part_finished_ms=round((slot["finished"] - origin) * 1000, 2),
                       overlap_ms=round(max(0, min(manual_finished, slot["finished"])
                                           - max(manual_started, slot["started"])) * 1000, 2))
    return retrieval, enrichment, metrics


def retrieve(args, home, session_id):
    model, question = args.get("model"), args.get("question")
    device = verified_device(model, question)
    if source_intent(question) == "part":
        raise ValueError("Part Book intent: call maintenance_partbook_lookup first; no Shop Manual/web search was run. "
                         "For a coverage miss use Part Book fallback; never infer absence from a Shop Manual miss.")
    keywords = str(args.get("keywords") or "").strip()
    if not re.search(r"[A-Za-z]{3}", keywords):
        raise ValueError("English Shop Manual keywords for the system/component and symptom are required")
    count_key = (session_id, question.strip())
    consume_attempt(_retrieves, count_key, limit=MAX_RETRIEVES, lock=_registry_lock,
                    error="Retrieval limit reached; answer from the evidence found, state the missing evidence or ask for clarification")
    root_rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    rules, _ = select_rules(device.read_text(encoding="utf-8"), root_rules, question, presentation=True,
                            part_enrichment=indexed_part_available(model) and valid_part_query(args.get("part_query")))
    fault_code = str(args.get("fault_code") or "").strip()
    seed = "|".join((session_id, question, keywords, fault_code, str(bool(args.get("broad"))), str(time.time_ns())))
    request_id, request_file = write_request(home, seed, model, question)
    with _registry_lock:
        _requests[request_id] = session_id
        _request_keys[request_id] = count_key
    command = ["retrieve", "--request-file", request_file, "--component", keywords]
    if fault_code:
        command += ["--fault-code", fault_code]
    if args.get("broad"):
        command.append("--broad")
    with _registry_lock:
        skip_web = count_key in _web_failures
    if skip_web:
        command.append("--skip-web")
    retrieval, enrichment, metrics = run_retrieval(command, session_id, model, args.get("part_query"), request_id)
    web = retrieval.get("web_search") or {}
    web_error = str(web.get("backend_error") or web.get("error") or "")
    remember_failure(_web_failures, count_key, web_error,
                     pattern=r"(?<![0-9])403(?![0-9])", lock=_registry_lock)
    coverage = (retrieval.get("manual_packet") or {}).get("evidence_coverage")
    return {
        "evidence_coverage": coverage or {"status": "unknown", "action": "judge"},
        "next": evidence_followup(coverage, broad=bool(args.get("broad"))) + (
            " Ready VERIFIED Part Book rows are in part_enrichment. If their assembly identity helps this "
            "repair, include one compact identification line with applicability/coverage in the same final "
            "answer; do not omit useful identification solely because no PN was explicitly requested. "
            "Different assembly variants remain alternatives, not confirmed fit. No extra lookup or render."
            if enrichment.get("status") == "verified" else ""),
        "prepared": {
            "model": model, "request_id": request_id,
            "device_agents_read_in_full": str(device),
            "applicable_device_policy": rules or "No unique device policy beyond the loaded root rules.",
        },
        "retrieval": retrieval,
        "part_enrichment": enrichment,
        "enrichment_metrics": metrics,
    }


def finish(args, session_id):
    request_id = str(args.get("request_id") or "")
    with _registry_lock:
        owner = _requests.get(request_id)
    if not re.fullmatch(r"[0-9a-f]{24}", request_id) or owner != session_id:
        raise ValueError("Unknown request_id; call phase=retrieve for this question first")
    command = ["finish", "--request-file", ROOT / "runtime/manual-worker-requests" / (request_id + ".json")]
    for option, key in (("--render-pages", "render_pages"), ("--read-pages", "read_pages")):
        pages = args.get(key) or []
        if pages:
            command += [option, *(int(page) for page in pages)]
    if args.get("web_url"):
        command += ["--web-url", args["web_url"]]
    if len(command) == 3:
        raise ValueError("Select image pages, needed text pages or a relevant URL")
    result = run_batch(command, session_id)
    with _registry_lock:
        count_key = _request_keys.pop(request_id, None)
        if count_key:
            _web_failures.discard(count_key)
        enrichment = _request_enrichments.pop(request_id, None)
    if enrichment and enrichment[0] == session_id:
        result["part_enrichment"] = enrichment[2]["result"] if enrichment[1].is_set() else {"status": "not_ready", "candidates": []}
    return result


def manual_evidence(args, session_id="", **kwargs):
    from hermes_constants import get_hermes_home
    home = get_hermes_home().resolve()
    try:
        if not is_maintenance(home):
            raise ValueError("Technical Docs surface is not enabled in this profile")
        phase = args.get("phase")
        if phase == "retrieve":
            result = retrieve(args, home, session_id)
        elif phase == "finish":
            result = finish(args, session_id)
        else:
            raise ValueError("phase must be retrieve or finish")
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


PART_DESCRIPTION = (
    "FIRST tool for Part Number / شماره فنی / شماره قطعه / پارت نامبر, bare PN with model, "
    "Parts Book component identification, Figure/Item or exploded view. Reads device AGENTS.md IN FULL "
    "before one targeted partbook_lookup.py --verify; verifies actual PDF rows. Use part_number for "
    "a supplied PN, query for English component name, or figure/item. HD785-7 B1, HD785-5, WA600-6 2010, "
    "HD465-7R, PC1250SP-8R and PC800-8 have indexed text-layer books; HD785-7 B2 remains unindexed. "
    "Fleet aliases (Persian/Arabic digits, بیل, خط as dash) resolve through the approved registry. "
    "PC1250-8R uses the PC1250SP-8R source; PC800-8R uses PC800-8 with visible variant limits. "
    "PART_FOUND_IN_SOURCE is independent of PART_APPLICABILITY_CONFIRMED; never claim fit from a verified row alone. "
    "Report only VERIFIED source candidates "
    "with quantity and applicability. Simple verified "
    "local lookup needs no Shop Manual/web. Mixed diagnosis + PN needs this FIRST plus "
    "maintenance_manual_evidence. Miss, incomplete coverage, serial outside coverage, ambiguity, "
    "supersession or unavailable/replacement permit fallback; index miss NEVER proves nonexistence."
)


def part_schema(models):
    return {"name": PART_TOOL, "description": PART_DESCRIPTION, "parameters": {
        "type": "object", "required": ["model", "question"], "properties": {
            "model": {"type": "string", "description": "Approved fleet model/alias or source name, e.g. ۱۲۵۰, بیل ۸۰۰, 1250 خط 8; exact historical model names remain accepted. Resolver rejects unknown/ambiguous models."},
            "question": {"type": "string", "description": "Exact original question; model may come from session"},
            "part_number": {"type": "string", "description": "Exact user PN; never guess"},
            "query": {"type": "string", "description": "English Part Book component name; no guessed PN"},
            "figure": {"type": "string"}, "item": {"type": "integer", "minimum": 0},
            "serial": {"type": "string", "description": "Machine serial only if supplied/verified"},
            "render": {"type": "boolean", "description": "Optional explicit render for mixed requests; verified direct Part-only identification renders automatically"},
        }}}


def part_lookup(args, session_id="", **kwargs):
    from hermes_constants import get_hermes_home
    try:
        if not is_maintenance(get_hermes_home().resolve()):
            raise ValueError("Technical Docs surface is not enabled in this profile")
        requested_model, question = args.get("model"), args.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Original question required")
        resolution = resolve_model(requested_model)
        if resolution["resolution_status"] != "RESOLVED":
            return json.dumps(dict(resolution, found=0, candidates=[], coverage_complete=False,
                                   indexed_lookup_available=False), ensure_ascii=False)
        asset = resolution["fleet_asset"]
        model = asset["display_model"]
        # A literal source name retains the historical source-only preparation;
        # fleet aliases load the fleet device policy before reading the linked PDF.
        if resolution["matched_identity"] == "partbook_source" and requested_model == "PC800-8":
            model = "PC800-8"
        shop_models = manual_models()
        if model in shop_models:
            device = verified_device(model, question)
            # Complete rule load BEFORE lookup, including Part rules for name-only queries.
            root_rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
            rules, _ = select_rules(device.read_text(encoding="utf-8"), root_rules,
                                    question + " Part Number", presentation=True)
            prepared = {"model": model, "device_agents_read_in_full": str(device),
                        "applicable_device_policy": rules}
        elif model in INDEXED_PART_MODELS:
            # An indexed Parts Book model is not a Shop Manual identity.
            if not isinstance(question, str) or not question.strip():
                raise ValueError("Verified model and original question required")
            prepared = {"model": model, "device_agents_read_in_full": None,
                        "applicable_device_policy": "", "shop_manual_identity": None}
        else:
            raise ValueError("Verified model and original question required")
        if not asset["book_ids"]:
            return json.dumps({"resolution": resolution, "requested_model": requested_model,
                               "resolved_fleet_asset": asset, "selected_part_books": [],
                               "part_found_in_source": False, "part_applicability_confirmed": False,
                               "applicability_status": "PART_APPLICABILITY_UNCONFIRMED",
                               "found": 0, "candidates": [],
                               "prepared": prepared, "coverage_complete": False,
                               "indexed_lookup_available": False,
                               "next": "No production Part Book index for this model. Use its permitted local Part Book fallback; do not infer absence or borrow another model PN."}, ensure_ascii=False)
        pn = str(args.get("part_number") or "").strip()
        if not pn and not args.get("query") and not args.get("figure"):
            matches = re.findall(r"\b[0-9A-Za-z]{3,5}-[0-9A-Za-z]{2,3}-[0-9A-Za-z]{4,5}\b", question)
            if len(matches) == 1:
                pn = matches[0]
        if not any((pn, args.get("query"), args.get("figure"))):
            raise ValueError("Supply user PN, English component query or figure/item; never guess a PN")
        command = [str(ROOT / ".venv/Scripts/python.exe"),
                   str(ROOT / "tools/fleet/partbook_lookup.py"), "--model", requested_model, "--verify"]
        if os.environ.get("PARTBOOK_TEST_DB"):
            command += ["--db", os.environ["PARTBOOK_TEST_DB"]]
        for option, value in (("--part-number", pn), ("--query", args.get("query")),
                              ("--figure", args.get("figure")), ("--item", args.get("item")),
                              ("--serial", args.get("serial"))):
            if value is not None and str(value).strip():
                command += [option, str(value)]
        intent = source_intent(question)
        if intent == "part":
            command.append("--auto-render-verified")
        elif args.get("render"):
            command.append("--render")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        if session_id:
            env["HERMES_SESSION_ID"] = session_id
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=60, cwd=str(ROOT), env=env,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode:
            raise ValueError("Part Book lookup failed: " + completed.stderr.decode("utf-8", "replace")[-500:])
        result = json.loads(completed.stdout)
        result["prepared"] = prepared
        result["source_intent"] = intent
        media = [item["output"] for item in result.get("rendered", [])
                 if item.get("ok") and str(item.get("output", "")).startswith("MEDIA:")]
        result["media"] = media
        result["next"] = (
            "Report requested model, resolved fleet asset, actual book model and model_match_status separately. "
            "PART_FOUND_IN_SOURCE means the PDF row was verified; PART_APPLICABILITY_UNCONFIRMED means "
            "fit for ordering/installation is NOT established. Unknown/outside serial or variant differences "
            "do not suppress documented source facts. Preserve source_citation and applicability_reasons. "
            "Answer identification only from VERIFIED PDF candidates with figure/item/quantity/applicability. "
            "Simple verified local lookup needs no Shop Manual/web. Mixed intent also needs Shop Manual technical "
            "evidence. Coverage is partial: miss/MISMATCH/outside serial/ambiguity needs appropriate Part Book "
            "fallback; supersession/replacement/unavailable permit targeted web. Index miss is NOT evidence "
            "that the part does not exist. For direct Part-only identification, include every returned "
            "MEDIA: path on its own line in the same final response.")
        return json.dumps(result, ensure_ascii=False)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        return json.dumps({"success": False, "coverage_complete": False, "error": str(exc)}, ensure_ascii=False)


def model_aliases():
    """Curated numeric aliases come from the same authoritative fleet registry."""
    result = {}
    for asset in load_registry()["assets"]:
        for alias in asset["aliases"]:
            if alias.isascii() and alias.isdigit():
                resolution = resolve_model(alias)
                if resolution["resolution_status"] == "RESOLVED":
                    result[alias] = asset["display_model"]
    return result


def shared_routing(info):
    from hermes_constants import get_hermes_home
    if not technical_docs_enabled(get_hermes_home()):
        return ""
    routing = (Path(__file__).parent / "ROUTING.md").read_text(encoding="utf-8")
    aliases = ", ".join(key + "=" + value for key, value in model_aliases().items())
    return routing + ("\nApproved fleet Part Book aliases (Persian/Arabic/English digits; بیل; خط means -): " + aliases + ". Use these for Part Book evidence without an extra model-confirmation turn. Fleet identity and actual source model remain separate; disclose PC1250 cover/internal SP discrepancy and PC800-8 versus PC800-8R. Never treat an alias or verified source row as confirmed fleet fit. For Shop Manual evidence retain its separate device policy. For an ambiguous family ask for the model/variant. Missing optional serial alone does not block Manual retrieval.")


def register(ctx):
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("subagent_start", subagent_start)
    ctx.register_hook("subagent_stop", subagent_stop)
    ctx.register_system_prompt_section("komatso.technical.prepared-manual", prepared_policy, max_chars=1400)
    ctx.register_system_prompt_section("komatso.technical.routing", shared_routing, max_chars=3000)
    models = manual_models()
    register_operations(ctx, [
        Operation(TOOL, TOOLSET, tool_schema(models), manual_evidence, TOOL_DESCRIPTION),
        Operation(PART_TOOL, TOOLSET, part_schema(part_model_names()), part_lookup, PART_DESCRIPTION),
    ])
