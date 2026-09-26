"""Maintenance-only Technical preparation: delegate hook and Parent fast-path tool; no source writes."""
from __future__ import annotations
import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path("E:/KomatsoAI")
MARKER = "KOMATSO_MANUAL_TASK_V3 "
PREPARED = "KOMATSO_MANUAL_PREPARED_V3"
TOOL = "maintenance_manual_evidence"
_registry_lock = threading.Lock()
_pending = {}
_prepared_sessions = set()
_child_sessions = set()



def normalized(value):
    return re.sub(r"\s+", " ", value).strip().casefold()


def select_rules(device_text, existing, question, presentation=False):
    """Select policy sections by general request intent, never manual pages/faults.

    Read the entire source. Keep model/source/variant/safety constraints and the
    applicable test policies; omit duplicate root workflow and manual tool
    mechanics replaced by the batches. Presentation is kept only for the agent
    that writes the user answer. Unknown substantive headings are retained.
    """
    sections = re.split(r"(?m)(?=^#{2,3} )", device_text)
    diagnostic = bool(re.search(r"test|fault|fail|symptom|pressure|heavy|weak|slow|start|leak|\bnot\b|تست|خراب|خطا|نمی|علت|فشار|سنگینی|ضعف|ضعیف|کند|استارت|نشتی", question, re.I))
    parts = bool(re.search(r"part|order|شماره.*قطعه|پارت|سفارش", question, re.I))
    code = bool(re.search(r"code|کد|خطا", question, re.I))
    mechanics = ("FAST ", "MINIMIZE TOOL", "WINDOWS EXECUTION", "WEB SEARCH PERFORMANCE", "انتخاب سریع منبع")
    answer = ("سبک پاسخ", "قالب پاسخ", "تصویر و نقشه", "کامل بودن", "IMAGE DELIVERY")
    skip = mechanics if presentation else mechanics + answer
    test = ("روش توضیح", "تست برقی", "تست فشار", "بررسی ظاهری", "خطاهای سنسوری", "سؤال های غیر")
    seen = normalized(existing)
    output = []
    headings = []
    for section in sections:
        heading = section.splitlines()[0] if section.strip() else ""
        if any(item.casefold() in heading.casefold() for item in skip):
            continue
        if any(item in heading for item in test) and not diagnostic:
            continue
        if "فقط کد خطا" in heading and not code:
            continue
        if ("PART" in heading.upper() or "قطعه" in heading) and not parts:
            continue
        paragraphs = re.split(r"\n\s*\n", section.strip())
        kept = []
        for paragraph in paragraphs:
            key = normalized(paragraph)
            if not key or key in seen:
                continue
            # Strip part-order instructions from a source section for diagnosis.
            if not parts and re.search(r"part.?number|partbook|پارت بوک|شماره قطعه|اگر شماره قطعه|برای سفارش", paragraph, re.I):
                continue
            kept.append(paragraph)
            seen += " " + key
        if kept and any(not item.startswith("#") for item in kept):
            output.append("\n\n".join(kept))
            headings.append(heading)
    return "\n\n".join(output), headings


def is_maintenance(home):
    return home.name.casefold() == "maintenance" and home.parent.name.casefold() == "profiles"


def verified_device(model, question):
    models = json.loads((ROOT / "tools/manual_models.json").read_text(encoding="utf-8"))
    if model not in models or not isinstance(question, str) or not question.strip():
        raise ValueError("Verified model and original question required")
    return ROOT / models[model] / "AGENTS.md"


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
    skill = home / "skills/maintenance-two-stream-evidence/SKILL.md"
    if not skill.is_file():
        raise ValueError("Required Maintenance workflow skill is missing")
    skill_text = skill.read_text(encoding="utf-8")
    match = re.search(r"1\. \*\*Technical.*?(?=\n2\.)", skill_text, re.S)
    if not match:
        raise ValueError("Unsupported workflow skill structure; read instructions directly")
    skill_delta = match.group().strip()
    skill_delta = re.sub(r"Load the relevant device .*? before device sources\.", "Device instructions were read in full by preparation before source access.", skill_delta, flags=re.S)
    _, request_file = write_request(home, session_id + task.get("goal", "") + question, model, question)
    # The question already present in metadata/original context is never repeated.
    question_context = "" if question in original else "Original question: " + question
    context = "\n\n".join(part for part in (
        PREPARED + "\nVerified model: " + model,
        "Device AGENTS was read IN FULL by the dispatcher before source retrieval; applicable unique policy follows: " + str(device) + "\n" + rules if rules else "Device instructions already present; no reinjection.",
        "Required matching skill maintenance-two-stream-evidence loaded by dispatcher; its Technical scope follows. No separate skill_view is needed.\n" + skill_delta,
        contract if normalized(contract) not in normalized(original) else "",
        "Prepared request file: " + request_file.as_posix(), question_context, "Background machine facts (preserve reported symptoms/codes; do not broaden the original question):\n" + original.strip() if original.strip() else "") if part)
    token = secrets.token_hex(16)
    with _registry_lock:
        now = time.monotonic()
        for stale in [key for key, when in _pending.items() if now - when > 3600]:
            _pending.pop(stale, None)
        _pending[token] = now
    changed = dict(task, context=context, goal=(
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
        return {"action":"block", "message":"Maintenance Technical preparation failed: " + str(exc)}
    return {"action":"modify", "args":updated} if updated else None


def subagent_start(child_session_id="", child_goal="", **kwargs):
    receipt = re.search(r"\nPreparation receipt: ([0-9a-f]{32})$", child_goal)
    if child_session_id:
        with _registry_lock:
            _child_sessions.add(child_session_id)
            if receipt and _pending.pop(receipt.group(1), None) is not None:
                _prepared_sessions.add(child_session_id)


def subagent_stop(child_session_id="", session_id="", **kwargs):
    with _registry_lock:
        _prepared_sessions.discard(child_session_id or session_id)
        _child_sessions.discard(child_session_id or session_id)


def prepared_policy(info):
    if info.get("platform") != "subagent" or info.get("profile_name") != "maintenance":
        return ""
    with _registry_lock:
        ready = info.get("session_id") in _prepared_sessions
    if not ready:
        return ""
    return (
        "This Technical child has a trusted Maintenance preparation receipt. "
        "Before the child started, the dispatcher read the complete selected device AGENTS.md "
        "and maintenance-two-stream-evidence skill. All applicable unique device policies and "
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
_retrieves = {}

TOOL_DESCRIPTION = (
    "Maintenance Parent fast Technical path for a technical/manual question about a supported model. "
    "phase=retrieve first reads the selected device AGENTS.md IN FULL and returns its applicable policy; "
    "this satisfies the root requirement to load machine-specific rules before source access, so do not "
    "read that file again. It then concurrently runs the manual_sections-routed evidence packet (bounded "
    "actual Shop Manual topic text; the index is routing only, never evidence) and the configured web_search. "
    "phase=finish concurrently renders and validates the chosen genuine pages, reads bounded missing page "
    "text and web_extracts one URL taken from the retrieve results; it returns MEDIA paths for delivery. "
    "Normal path: retrieve, one evaluation, finish, answer. Read evidence_coverage.status on the result. "
    "complete: finish and do not retrieve again. truncated: finish with read_pages for the listed resume pages, "
    "do not retrieve again. incomplete: retrieve again with refined keywords, then broad=true if still incomplete. "
    "Do not call web_search, web_extract, PDF scripts or render_page separately for this question. Not for delegated workers."
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
    status = (coverage or {}).get("status")
    reason = (coverage or {}).get("reason") or ""
    if status == "complete":
        return (
            "evidence_coverage.status is complete. A documented troubleshooting or test topic in this packet "
            "already names the requested component and its text is complete. Call phase=finish with this "
            "request_id and the smallest sufficient render_pages. Do not retrieve again. Other index hits, "
            "adjacent faults, and a cross-reference already written in that topic are not missing evidence. "
            "Use read_pages only when a required value, test condition, or safety step is not already in the "
            "complete topic. If that text does not contain the specific procedure, say it is not documented; "
            "do not guess. " + reason
        )
    if status == "truncated":
        return (
            "evidence_coverage.status is truncated. The matching topic is already in this packet but the page "
            "limit cut it off. Do not retrieve again. Call phase=finish and pass read_pages for "
            "evidence_coverage.resume_at_pdf_pages. " + reason
        )
    if status == "incomplete" and broad:
        return (
            "evidence_coverage.status is incomplete after a whole-manual search. Do not retrieve again. "
            "Answer from the relevant pages only, and state what the manual does not document. "
            "Never invent values, procedures, or safety conditions. " + reason
        )
    if status == "incomplete":
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


def retrieve(args, home, session_id):
    model, question = args.get("model"), args.get("question")
    device = verified_device(model, question)
    keywords = str(args.get("keywords") or "").strip()
    if not re.search(r"[A-Za-z]{3}", keywords):
        raise ValueError("English Shop Manual keywords for the system/component and symptom are required")
    count_key = (session_id, question.strip())
    with _registry_lock:
        if _retrieves.get(count_key, 0) >= MAX_RETRIEVES:
            raise ValueError("Retrieval limit reached; answer from the evidence found, state the missing evidence or ask for clarification")
        _retrieves[count_key] = _retrieves.get(count_key, 0) + 1
    root_rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    rules, _ = select_rules(device.read_text(encoding="utf-8"), root_rules, question, presentation=True)
    fault_code = str(args.get("fault_code") or "").strip()
    seed = "|".join((session_id, question, keywords, fault_code, str(bool(args.get("broad"))), str(time.time_ns())))
    request_id, request_file = write_request(home, seed, model, question)
    with _registry_lock:
        _requests[request_id] = session_id
    command = ["retrieve", "--request-file", request_file, "--component", keywords]
    if fault_code:
        command += ["--fault-code", fault_code]
    if args.get("broad"):
        command.append("--broad")
    retrieval = run_batch(command, session_id)
    coverage = (retrieval.get("manual_packet") or {}).get("evidence_coverage")
    return {
        "evidence_coverage": coverage or {"status": "unknown", "action": "judge"},
        "next": evidence_followup(coverage, broad=bool(args.get("broad"))),
        "prepared": {
            "model": model, "request_id": request_id,
            "device_agents_read_in_full": str(device),
            "applicable_device_policy": rules or "No unique device policy beyond the loaded root rules.",
        },
        "retrieval": retrieval,
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
    return run_batch(command, session_id)


def manual_evidence(args, session_id="", **kwargs):
    from hermes_constants import get_hermes_home
    home = get_hermes_home().resolve()
    try:
        if not is_maintenance(home):
            raise ValueError("Available only in the Maintenance profile")
        with _registry_lock:
            child = session_id in _child_sessions
        if child:
            raise ValueError("Parent-only tool; delegated workers use their supplied contract")
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


def register(ctx):
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("subagent_start", subagent_start)
    ctx.register_hook("subagent_stop", subagent_stop)
    ctx.register_system_prompt_section("komatso.maintenance.prepared-manual", prepared_policy, max_chars=1400)
    models = json.loads((ROOT / "tools/manual_models.json").read_text(encoding="utf-8"))
    ctx.register_tool(name=TOOL, toolset="komatso_maintenance", schema=tool_schema(models),
                      handler=manual_evidence, description=TOOL_DESCRIPTION)
