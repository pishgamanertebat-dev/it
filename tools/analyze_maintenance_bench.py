"""Aggregate real Hermes Maintenance benchmark timing without printing source records."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

STAMP = "%Y-%m-%d %H:%M:%S,%f"
SESSION = re.compile(r"\[([0-9]{8}_[0-9]{6}_[0-9a-f]+)\]")
API = re.compile(r"API call #(\d+): .*? in=(\d+) out=(\d+).*?latency=([0-9.]+)s")
TOOLS = re.compile(r"agent.tool_executor: tool ([a-z_]+) completed \(([0-9.]+)s")
ERROR_TOOLS = re.compile(r"Tool ([a-z_]+) returned error \(([0-9.]+)s")
ROUNDS = re.compile(r"tool_turns=(\d+)")
APPROX_CONTEXT = re.compile(r"context=~([0-9,]+) tokens")

def stamp(line):
    return datetime.strptime(line[:23], STAMP)

def session_messages(database, session, transcripts_dir):
    """(role, tool_calls, content) rows from the Gateway state DB or a private harness transcript."""
    rows = []
    if database.is_file():
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
            rows = [(role, json.loads(calls or "[]"), content) for role, calls, content in connection.execute(
                "select role, tool_calls, content from messages where session_id=? order by id", (session,))]
    transcript = transcripts_dir / f"{session}.json" if transcripts_dir else None
    if not rows and transcript and transcript.is_file():
        payload = json.loads(transcript.read_text(encoding="utf-8"))
        rows = [(m.get("role"), m.get("tool_calls") or [], m.get("content")) for m in payload.get("messages") or []]
    return rows

def fast_path(rows):
    """Parent fast-path phases and backend components, without questions or evidence text."""
    calls = []
    for role, tool_calls, content in rows:
        for call in tool_calls if role == "assistant" else []:
            function = call.get("function", call)
            if function.get("name") == "maintenance_manual_evidence":
                args = function.get("arguments") or "{}"
                args = json.loads(args) if isinstance(args, str) else args
                calls.append({"phase": args.get("phase"), "keywords": args.get("keywords"),
                              "broad": bool(args.get("broad")),
                              "render_pages": len(args.get("render_pages") or []),
                              "read_pages": len(args.get("read_pages") or []),
                              "web_url": bool(args.get("web_url"))})
    results = [json.loads(content) for role, _, content in rows
               if role == "tool" and isinstance(content, str) and content.lstrip().startswith("{")
               and ('"retrieval"' in content or '"batch_metrics"' in content or '"success": false' in content)]
    for call, result in zip(calls, results):
        batch = result.get("retrieval", result)
        packet = batch.get("manual_packet") or {}
        call.update(success=result.get("success", True) is not False,
                    device_policy_chars=len((result.get("prepared") or {}).get("applicable_device_policy", "")),
                    operations=sorted(k for k in batch if k != "batch_metrics"),
                    batch_wall_seconds=(batch.get("batch_metrics") or {}).get("wall_seconds"),
                    indexed_sections=[s["key"] for s in packet.get("searched_sections", [])],
                    full_manual_fallback=packet.get("full_manual_fallback"),
                    packet_text_chars=packet.get("text_chars"))
    return calls

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parent_session")
    ap.add_argument("--log", type=Path, default=Path.home() / "AppData/Local/hermes/profiles/maintenance/logs/agent.log")
    ap.add_argument("--transcripts-dir", type=Path,
                    help="Private benchmark JSON transcripts, needed for untagged parallel-tool logs")
    args = ap.parse_args()
    rows = []
    for line in args.log.read_text(encoding="utf-8", errors="replace").splitlines():
        sid_match = SESSION.search(line)
        if sid_match:
            rows.append((stamp(line), sid_match.group(1), line))
    parent = [row for row in rows if row[1] == args.parent_session]
    if not parent:
        raise SystemExit("Parent session not found")
    start = next(t for t, _, s in parent if "conversation turn:" in s)
    end = next(t for t, _, s in reversed(parent) if "Turn ended:" in s)
    child_ids = []
    for t, sid, s in rows:
        if start <= t <= end and sid != args.parent_session and "platform=subagent" in s and sid not in child_ids:
            child_ids.append(sid)
    if len(child_ids) not in (0, 2):
        raise SystemExit(f"Expected Parent-direct or two subagents; found {len(child_ids)}")

    def metrics(sid):
        subset = [row for row in rows if row[1] == sid]
        api = [m for _, _, s in subset if (m := API.search(s))]
        tool = [m for _, _, s in subset if (m := TOOLS.search(s))]
        failed_tools = [m for _, _, s in subset if (m := ERROR_TOOLS.search(s))]
        first = next(t for t, _, s in subset if "conversation turn:" in s)
        last = next(t for t, _, s in reversed(subset) if "Turn ended:" in s)
        rounds_line = next(s for _, _, s in reversed(subset) if "Turn ended:" in s)
        context = [int(m.group(1).replace(",", "")) for _, _, s in subset if (m := APPROX_CONTEXT.search(s))]
        transcript_names = None
        if args.transcripts_dir:
            transcript = args.transcripts_dir / f"{sid}.json"
            if transcript.is_file():
                payload = json.loads(transcript.read_text(encoding="utf-8"))
                transcript_names = [call.get("function", {}).get("name", "unknown")
                                    for message in payload.get("messages") or []
                                    for call in message.get("tool_calls") or []]
        attempts = len(transcript_names) if transcript_names is not None else len(tool) + len(failed_tools)
        return {
            "session_id": sid,
            "start": first.isoformat(timespec="milliseconds"),
            "end": last.isoformat(timespec="milliseconds"),
            "duration_seconds": round((last - first).total_seconds(), 2),
            "api_calls": len(api),
            "model_seconds_sum": round(sum(float(m.group(4)) for m in api), 2),
            "model_seconds_each": [float(m.group(4)) for m in api],
            "first_input_tokens": int(api[0].group(2)) if api else None,
            "last_input_tokens": int(api[-1].group(2)) if api else None,
            "initial_context_approx_tokens": context[0] if context else None,
            "last_context_approx_tokens": context[-1] if context else None,
            "tool_calls": attempts - len(failed_tools),
            "tool_count_source": "transcript" if transcript_names is not None else "session-tagged log",
            "failed_tool_calls": len(failed_tools),
            "tool_attempts": attempts,
            "failed_tool_seconds_sum": round(sum(float(m.group(2)) for m in failed_tools), 2),
            "tool_seconds_sum": round(sum(float(m.group(2)) for m in tool), 2),
            "tool_names": transcript_names if transcript_names is not None else [m.group(1) for m in tool],
            "tool_rounds": int(ROUNDS.search(rounds_line).group(1)),
            "final_model_seconds": float(api[-1].group(4)) if api else None,
        }

    children = [metrics(sid) for sid in child_ids]
    overlap = None
    if children:
        child_start = [datetime.fromisoformat(c["start"]) for c in children]
        child_end = [datetime.fromisoformat(c["end"]) for c in children]
        overlap = round(max(0.0, (min(child_end) - max(child_start)).total_seconds()), 2)
    print(json.dumps({
        "parent_session": args.parent_session,
        "route": "two-stream" if children else "parent-direct",
        "wall_seconds": round((end - start).total_seconds(), 2),
        "parent": metrics(args.parent_session),
        "fast_path": fast_path(session_messages(args.log.parents[1] / "state.db", args.parent_session,
                                                args.transcripts_dir)),
        "children": children,
        "child_overlap_seconds": overlap,
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
