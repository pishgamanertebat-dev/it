"""Paired real retrieval benchmark; no LLM calls, Gateway changes or source writes.

Cold = fresh harness interpreter (OS file cache is not forcibly evicted).
Warm = repeated harness process; production lookup still uses its normal fresh CLI.
Live web/PDF timing varies: report raw overhead AND manual-batch timing differences.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
HOME = Path("C:/Users/win-10/AppData/Local/hermes/profiles/maintenance")
CASES = {
    "pin": ("HD785-7 پین ته دکل لق میزنه", "boom foot pin excessive play", "boom foot pin"),
    "pump": ("HD785-7 پمپ فرمان فشار نداره", "steering pump low pressure", "steering pump"),
    "filter": ("HD785-7 فیلتر روغن ترمز گرفته", "brake oil filter restriction", "brake filter"),
}


def load_plugin():
    spec = importlib.util.spec_from_file_location("bench_enrichment", ROOT / "integrations/hermes/plugins/komatso-maintenance-manual/__init__.py")
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    return plugin


def stable_web_batch(command, session_id):
    """Reuse real manual_worker_batch/probe; keep optional web timing constant.

    Network-free control models an unavailable web provider, NOT a replacement
    production provider. This removes network randomness from local overhead.
    """
    import manual_worker_batch as worker
    request_file = Path(command[command.index("--request-file") + 1])
    probe_args = ["--request-file", request_file, "--packet"]
    for flag in ("--component", "--fault-code"):
        if flag in command:
            probe_args += [flag, command[command.index(flag) + 1]]
    if "--broad" in command:
        probe_args.append("--broad")
    started = time.perf_counter()
    results, timings = worker.batch([
        ("manual_packet", lambda: worker.run_probe(*probe_args)),
        ("web_search", lambda: {"success": False, "error": "Constant unavailable web response for benchmark control"}),
    ])
    results["batch_metrics"] = {"operation_seconds": timings, "wall_seconds": time.perf_counter() - started}
    return results


def measure(plugin, case, enriched):
    question, keywords, query = CASES[case]
    args = dict(model="HD785-7", question=question, keywords=keywords)
    if enriched:
        args["part_query"] = query
    started = time.perf_counter()
    result = plugin.retrieve(args, HOME, "part-bench-" + uuid.uuid4().hex)
    elapsed = (time.perf_counter() - started) * 1000
    return {"total_ms": round(elapsed, 2), "metrics": result["enrichment_metrics"],
            "backend_ms": {key: round(seconds * 1000, 2) for key, seconds in
                           result["retrieval"].get("batch_metrics", {}).get("operation_seconds", {}).items()},
            "part_status": result["part_enrichment"]["status"],
            "part_lookup_ms": result["part_enrichment"].get("lookup_timing_ms"),
            "verified_count": len(result["part_enrichment"].get("candidates", []))}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--output", type=Path, default=ROOT / "runtime/part-enrichment-bench/results.json")
    ap.add_argument("--sample", choices=CASES, help=argparse.SUPPRESS)
    ap.add_argument("--stable-web", action="store_true", help="Real PDF/index work with constant web-unavailable control; report separately from live web")
    ap.add_argument("--enriched", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.sample:
        plugin = load_plugin()
        if args.stable_web:
            plugin.run_batch = stable_web_batch
        print(json.dumps(measure(plugin, args.sample, args.enriched)))
        return
    plugin = load_plugin()
    if args.stable_web:
        plugin.run_batch = stable_web_batch
    rows = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for temperature in ("cold", "warm"):
        for case in CASES:
            for run in range(args.runs):
                pair = {}
                # Alternate order rather than systematically warming only enrichment.
                for enabled in ([False, True] if run % 2 == 0 else [True, False]):
                    if temperature == "cold":
                        cmd = [sys.executable, str(Path(__file__).resolve()), "--sample", case]
                        if args.stable_web:
                            cmd.append("--stable-web")
                        if enabled:
                            cmd.append("--enriched")
                        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                                              env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=260,
                                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        if proc.returncode:
                            raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-500:])
                        value = json.loads(proc.stdout)
                    else:
                        value = measure(plugin, case, enabled)
                    pair["enriched" if enabled else "baseline"] = value
                overhead = pair["enriched"]["total_ms"] - pair["baseline"]["total_ms"]
                manual_delta = (pair["enriched"]["metrics"]["manual_batch_ms"]
                                - pair["baseline"]["metrics"]["manual_batch_ms"])
                row = dict(temperature=temperature, case=case, run=run + 1, **pair,
                           overhead_ms=round(overhead, 2), manual_batch_delta_ms=round(manual_delta, 2),
                           orchestration_delta_ms=round(overhead - manual_delta, 2))
                rows.append(row)
                args.output.write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
                print(json.dumps({key: row[key] for key in ("temperature", "case", "run", "overhead_ms", "orchestration_delta_ms")}), flush=True)
    summary = []
    for temperature in ("cold", "warm"):
        for case in CASES:
            group = [row for row in rows if row["temperature"] == temperature and row["case"] == case]
            summary.append({"temperature": temperature, "case": case, "runs": len(group),
                            "baseline_median_ms": round(statistics.median(row["baseline"]["total_ms"] for row in group), 2),
                            "enriched_median_ms": round(statistics.median(row["enriched"]["total_ms"] for row in group), 2),
                            "overhead_median_ms": round(statistics.median(row["overhead_ms"] for row in group), 2),
                            "overhead_max_ms": max(row["overhead_ms"] for row in group),
                            "part_median_ms": round(statistics.median(row["enriched"]["metrics"].get("part_duration_ms", 0) for row in group), 2),
                            "orchestration_max_ms": max(row["orchestration_delta_ms"] for row in group),
                            "overlap_all": all(row["enriched"]["metrics"].get("overlap_ms", 0) > 0 for row in group),
                            "statuses": [row["enriched"]["part_status"] for row in group]})
    args.output.write_text(json.dumps({"stable_web": args.stable_web, "rows": rows, "summary": summary}, indent=2), encoding="utf-8")
    print(json.dumps({"summary": summary}))

if __name__ == "__main__":
    main()