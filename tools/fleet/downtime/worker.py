"""CLI and structured JSON-stdin worker for Fleet Downtime Analytics.

Allows Hermes/scheduler to invoke heavy .venv tasks (Excel import, PDF rendering,
recipient verification) in a clean subprocess without polluting Hermes's uv Python.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from . import DEFAULT_DB_PATH, DEFAULT_RUNTIME_DIR
from .query import DowntimeQueryService


def dispatch_query(operation: str, args: dict[str, Any], *, db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, Any]:
    svc = DowntimeQueryService(db_path=db_path)
    if operation == "list_downtime_periods":
        return svc.list_downtime_periods()
    if operation == "get_daily_downtime":
        return svc.get_daily_downtime(
            date=args["date"],
            device_code=args.get("device_code"),
            limit=int(args.get("limit", 20)),
        )
    if operation == "get_monthly_downtime_summary":
        return svc.get_monthly_downtime_summary(period=args.get("period"))
    if operation == "get_device_downtime":
        return svc.get_device_downtime(
            device_code=args["device_code"],
            period=args.get("period"),
            date=args.get("date"),
        )
    if operation == "rank_devices_by_downtime":
        return svc.rank_devices_by_downtime(
            period=args.get("period"),
            metric=args.get("metric", "downtime_hours"),
            order=args.get("order", "desc"),
            limit=int(args.get("limit", 10)),
        )
    if operation == "get_downtime_causes":
        return svc.get_downtime_causes(
            period=args.get("period"),
            device_code=args.get("device_code"),
            cause=args.get("cause"),
        )
    if operation == "get_downtime_notes":
        return svc.get_downtime_notes(
            period=args.get("period"),
            device_code=args.get("device_code"),
            date=args.get("date"),
            keyword=args.get("keyword"),
            limit=int(args.get("limit", 15)),
        )
    if operation == "compare_device_performance":
        return svc.compare_device_performance(
            device_a=args["device_a"],
            device_b=args["device_b"],
            period=args.get("period"),
        )
    if operation == "get_monthly_downtime_report":
        return svc.get_monthly_downtime_report(period=args.get("period"))
    return {"ok": False, "error": f"Unknown query operation: {operation}"}


def main() -> int:
    parser = argparse.ArgumentParser(description="KomatsoAI Fleet Downtime Analytics worker")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_imp = sub.add_parser("import", help="Import monthly Excel workbook into SQLite")
    p_imp.add_argument("source", help="Path to monthly downtime Excel workbook")
    p_imp.add_argument("--period", default=None, help="Jalali period YYYY-MM (e.g., 1405-08)")
    p_imp.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR))
    p_imp.add_argument("--notes", default="")

    p_rep = sub.add_parser("render-pdf", help="Render executive monthly PDF report")
    p_rep.add_argument("--period", required=True, help="Jalali period YYYY-MM")
    p_rep.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR))
    p_rep.add_argument("--output", default=None)

    p_disc = sub.add_parser("discover", help="Discover candidate monthly downtime workbooks")
    p_disc.add_argument("--watch-dir", default=r"E:\Function")

    p_q = sub.add_parser("query", help="Execute a read-only query operation (JSON on stdin or --args)")
    p_q.add_argument("operation")
    p_q.add_argument("--args", default="{}")
    p_q.add_argument("--db-path", default=str(DEFAULT_DB_PATH))

    p_stdin = sub.add_parser("stdin", help="Read JSON {operation, args} from stdin")
    p_stdin.add_argument("--db-path", default=str(DEFAULT_DB_PATH))

    ns = parser.parse_args()
    if ns.cmd == "import":
        from .importer import import_monthly_workbook

        out = import_monthly_workbook(ns.source, period=ns.period, runtime_dir=ns.runtime_dir, notes=ns.notes)
    elif ns.cmd == "render-pdf":
        from .report import generate_monthly_pdf_report

        out = generate_monthly_pdf_report(ns.period, runtime_dir=ns.runtime_dir, output_path=ns.output)
    elif ns.cmd == "discover":
        from .importer import discover_monthly_workbooks

        out = {"workbooks": discover_monthly_workbooks(ns.watch_dir)}
    elif ns.cmd == "query":
        out = dispatch_query(ns.operation, json.loads(ns.args), db_path=ns.db_path)
    elif ns.cmd == "stdin":
        payload = json.loads(sys.stdin.read() or "{}")
        out = dispatch_query(payload.get("operation", ""), payload.get("args") or {}, db_path=ns.db_path)
    else:
        out = {"ok": False, "error": "Unsupported command"}

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
