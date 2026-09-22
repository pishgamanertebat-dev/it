"""One request per isolated process. Only this file imports parallel-web 1.3.2."""
from __future__ import annotations

import asyncio
from importlib.metadata import version
import json
from pathlib import Path
import sys


def _check_environment(parent_prefix):
    prefix = Path(sys.prefix).resolve()
    if prefix == Path(sys.base_prefix).resolve() or prefix == Path(parent_prefix).resolve():
        raise RuntimeError("A separate virtual environment is required")
    cfg = (prefix / "pyvenv.cfg").read_text(encoding="utf-8").lower()
    if not any(line.replace(" ", "").strip() == "include-system-site-packages=false"
               for line in cfg.splitlines()):
        raise RuntimeError("Worker must not inherit system site packages")
    if version("parallel-web") != "1.3.2":
        raise RuntimeError("Unsupported SDK version")


def search(payload):
    from parallel import Parallel
    with Parallel(api_key=payload["api_key"]) as client:
        response = client.search(search_queries=[payload["query"]], objective=payload["query"],
            mode=payload["mode"], advanced_settings={"max_results": min(payload["limit"], 20)})
    return {"success": True, "data": {"web": [
        {"url": r.url or "", "title": r.title or "", "description": " ".join(r.excerpts or []), "position": i + 1}
        for i, r in enumerate(response.results or [])]}}


async def extract(payload):
    from parallel import AsyncParallel
    async with AsyncParallel(api_key=payload["api_key"]) as client:
        response = await client.extract(urls=payload["urls"], advanced_settings={"full_content": True})
    results = []
    for row in response.results or []:
        content = row.full_content or "\n\n".join(row.excerpts or [])
        url, title = row.url or "", row.title or ""
        results.append({"url": url, "title": title, "content": content, "raw_content": content,
                        "metadata": {"sourceURL": url, "title": title}})
    for row in response.errors or []:
        results.append({"url": row.url or "", "title": "", "content": "",
            "error": row.content or row.error_type or "extraction failed",
            "metadata": {"sourceURL": row.url or ""}})
    return results


def main():
    try:
        payload = json.load(sys.stdin)
        _check_environment(payload["parent_prefix"])
    except Exception:
        print(json.dumps({"ok": False, "error": "dependency"}))
        return
    try:
        if payload["operation"] == "search":
            result = search(payload)
        elif payload["operation"] == "extract":
            result = asyncio.run(extract(payload))
        else:
            raise ValueError("Unknown operation")
        print(json.dumps({"ok": True, "result": result}))
    except Exception:
        print(json.dumps({"ok": False, "error": "request"}))


if __name__ == "__main__":
    main()
