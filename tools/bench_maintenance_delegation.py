"""Read-only Bale-surface Maintenance benchmark with real model and tools."""
import argparse
import json
import time
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--question-file", type=Path, required=True)
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
        "skills", "terminal", "web",
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
    if len(names) != 21 or "delegate_task" not in names:
        raise RuntimeError(f"Unexpected Bale tool surface: {len(names)} tools")
    started = time.perf_counter()
    try:
        result = agent.run_conversation(question)
        elapsed = time.perf_counter() - started
        answer = result.get("final_response") or ""
        media_paths = [line[6:].strip() for line in answer.splitlines() if line.startswith("MEDIA:")]
        print(json.dumps({
            "elapsed_seconds": round(elapsed, 2),
            "session_id": agent.session_id,
            "model": model,
            "provider": provider,
            "platform": agent.platform,
            "tool_count": len(names),
            "api_calls": result.get("api_calls"),
            "task_contracts": task_contracts,
            "answer_chars": len(answer),
            "media_count": len(media_paths),
            "media_files_exist": all(Path(path).is_file() for path in media_paths),
            "has_as_document": "[[as_document]]" in answer,
        }, ensure_ascii=False))
    finally:
        agent.close()

if __name__ == "__main__":
    main()
