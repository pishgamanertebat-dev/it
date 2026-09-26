"""Maintenance-only native pre_tool_call hook; no network or source writes."""
from __future__ import annotations
import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path

ROOT = Path("E:/KomatsoAI")
MARKER = "KOMATSO_MANUAL_TASK_V3 "
PREPARED = "KOMATSO_MANUAL_PREPARED_V3"
_registry_lock = threading.Lock()
_pending = {}
_prepared_sessions = set()



def normalized(value):
    return re.sub(r"\s+", " ", value).strip().casefold()


def select_rules(device_text, existing, question):
    """Select policy sections by general request intent, never manual pages/faults.

    Read the entire source. Keep model/source/variant/safety constraints and the
    applicable test policies; omit presentation and duplicate root workflow.
    Unknown substantive headings are retained rather than silently discarded.
    """
    sections = re.split(r"(?m)(?=^#{2,3} )", device_text)
    diagnostic = bool(re.search(r"test|fault|fail|symptom|pressure|heavy|weak|slow|start|leak|\bnot\b|تست|خراب|خطا|نمی|علت|فشار|سنگینی|ضعف|کند|استارت|نشتی", question, re.I))
    parts = bool(re.search(r"part|order|شماره.*قطعه|پارت|سفارش", question, re.I))
    code = bool(re.search(r"code|کد|خطا", question, re.I))
    skip = ("سبک پاسخ", "قالب پاسخ", "تصویر و نقشه", "کامل بودن", "FAST ",
            "MINIMIZE TOOL", "WINDOWS EXECUTION", "IMAGE DELIVERY", "WEB SEARCH PERFORMANCE", "انتخاب سریع منبع")
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


def prepare_args(args, home, session_id=""):
    if home.name.casefold() != "maintenance" or home.parent.name.casefold() != "profiles":
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
    models = json.loads((ROOT / "tools/manual_models.json").read_text(encoding="utf-8"))
    model, question = request["model"], request["question"]
    if model not in models or not isinstance(question, str) or not question.strip():
        raise ValueError("Verified model and original question required")
    device = ROOT / models[model] / "AGENTS.md"
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
    request_dir = ROOT / "runtime/manual-worker-requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256((session_id + task.get("goal", "") + question).encode()).hexdigest()[:24]
    request_file = request_dir / (digest + ".json")
    request_file.write_text(json.dumps({"model":model,"question":question,"profile_home":str(home)},ensure_ascii=False),encoding="utf-8")
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
    if receipt and child_session_id:
        with _registry_lock:
            if _pending.pop(receipt.group(1), None) is not None:
                _prepared_sessions.add(child_session_id)


def subagent_stop(child_session_id="", session_id="", **kwargs):
    with _registry_lock:
        _prepared_sessions.discard(child_session_id or session_id)


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


def register(ctx):
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("subagent_start", subagent_start)
    ctx.register_hook("subagent_stop", subagent_stop)
    ctx.register_system_prompt_section("komatso.maintenance.prepared-manual", prepared_policy, max_chars=1400)
