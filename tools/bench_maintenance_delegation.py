"""Read-only Bale-surface Maintenance benchmark with real model and tools."""
import argparse
import json
import time
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--question-file", type=Path, required=True)
    parser.add_argument("--transcripts-dir", type=Path,
                        help="Private directory for the run transcript, e.g. runtime/bench/transcripts")
    parser.add_argument("--history-file", type=Path, help="Private conversation history for session model identity")
    args = parser.parse_args()
    question = args.question_file.read_text(encoding="utf-8").strip()
    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_with_fallback
    from hermes_cli.tools_config import _get_platform_tools
    from agent.skill_utils import parse_config_string_list
    from run_agent import AIAgent
    from tools.delegate_tool import _strip_model_hidden_task_fields, delegate_task

    cfg = load_config()
    model = cfg["model"]["default"]
    provider = cfg["model"]["provider"]
    runtime, fallback = resolve_runtime_with_fallback(
        cfg, requested=provider, target_model=model
    )
    if fallback is not None:
        raise RuntimeError("Benchmark requires the configured parent model")
    toolsets = sorted(_get_platform_tools(cfg, "bale"))
    expected = {
        "browser", "code_execution", "connections", "delegation", "file",
        "komatso_maintenance", "skills", "terminal", "web",
    }
    if set(toolsets) != expected:
        raise RuntimeError(f"Bale toolsets changed: {toolsets}")
    agent = AIAgent(
        api_key=runtime.get("api_key"),
        base_url=runtime.get("base_url"),
        provider=runtime.get("provider"),
        requested_provider=runtime.get("requested_provider"),
        api_mode=runtime.get("api_mode"),
        model=model,
        enabled_toolsets=toolsets,
        disabled_toolsets=parse_config_string_list(
            (cfg.get("agent") or {}).get("disabled_toolsets")
        ) or None,
        max_iterations=(cfg.get("agent") or {}).get("max_turns", 25),
        quiet_mode=True,
        platform="bale",
        cwd=str(Path.cwd()),
        skip_background_review=True,
    )

    task_contracts = []

    # Join the real delegate_task batch inline: this harness has no Gateway
    # adapter to deliver the detached result to a subsequent parent turn.
    def join_delegate(function_args):
        tasks = _strip_model_hidden_task_fields(function_args.get("tasks"))
        for task in tasks or []:
            body = json.dumps(task, ensure_ascii=False, default=str)
            task_contracts.append({
                "mentions_probe": "manual_evidence_probe.py" in body,
                "mentions_no_skill_view": "skill_view" in body,
                "mentions_device_agents": "AGENTS.md" in body,
                "chars": len(body),
            })
        return delegate_task(
            goal=function_args.get("goal"),
            context=function_args.get("context"),
            tasks=tasks,
            role=function_args.get("role"),
            background=False,
            images=function_args.get("images"),
            action=function_args.get("action"),
            subagent_id=function_args.get("subagent_id"),
            message=function_args.get("message"),
            parent_agent=agent,
        )

    agent._dispatch_delegate_task = join_delegate
    names = sorted(
        t.get("function", {}).get("name", t.get("name", "")) for t in agent.tools
    )
    if (len(names) != 21 or not {"delegate_task", "maintenance_manual_evidence", "maintenance_partbook_lookup"} <= set(names)
            or "tool_search" in names):
        raise RuntimeError(f"Unexpected Bale tool surface: {len(names)} tools")
    started = time.perf_counter()
    try:
        history = json.loads(args.history_file.read_text(encoding="utf-8")) if args.history_file else None
        result = agent.run_conversation(question, conversation_history=history)
        elapsed = time.perf_counter() - started
        answer = result.get("final_response") or ""
        tool_sequence = []
        part_candidates = []
        part_enrichments = []
        enrichment_metrics = []
        manual_calls = []
        for message in result.get("messages") or []:
            for call in message.get("tool_calls") or []:
                function = call.get("function", call)
                tool_sequence.append(function.get("name"))
                if function.get("name") == "maintenance_manual_evidence":
                    arguments = function.get("arguments") or "{}"
                    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                    manual_calls.append({key: arguments.get(key) for key in ("phase", "keywords", "part_query")})
            if message.get("role") == "tool":
                try:
                    packet = json.loads(message.get("content") or "{}")
                except (ValueError, TypeError):
                    continue
                if packet.get("part_enrichment") is not None:
                    part_enrichments.append(packet["part_enrichment"])
                if packet.get("enrichment_metrics") is not None:
                    enrichment_metrics.append(packet["enrichment_metrics"])
                for candidate in packet.get("candidates", []):
                    part_candidates.append({key: candidate.get(key) for key in
                                            ("part_number", "figure", "item", "description", "quantity", "pdf_verification")})
        if args.transcripts_dir:
            args.transcripts_dir.mkdir(parents=True, exist_ok=True)
            (args.transcripts_dir / f"{agent.session_id}.json").write_text(json.dumps(
                {"messages": result.get("messages") or []}, ensure_ascii=False, default=str), encoding="utf-8")
        media_paths = [line[6:].strip() for line in answer.splitlines() if line.startswith("MEDIA:")]
        report = {
            "elapsed_seconds": round(elapsed, 2),
            "session_id": agent.session_id,
            "model": model,
            "provider": provider,
            "platform": agent.platform,
            "tool_count": len(names),
            "tool_sequence": tool_sequence,
            "part_candidates": part_candidates,
            "manual_calls": manual_calls,
            "part_enrichments": part_enrichments,
            "enrichment_metrics": enrichment_metrics,
            "source_priority_in_prompt": "Determine SOURCE INTENT" in (agent._cached_system_prompt or ""),
            "api_calls": result.get("api_calls"),
            "task_contracts": task_contracts,
            "answer_chars": len(answer),
            "media_count": len(media_paths),
            "media_files_exist": all(Path(path).is_file() for path in media_paths),
            "has_as_document": "[[as_document]]" in answer,
        }
        if args.transcripts_dir:
            (args.transcripts_dir / f"{agent.session_id}.summary.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        agent.close()

if __name__ == "__main__":
    main()
