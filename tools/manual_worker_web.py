"""Private stdin bridge to existing Hermes web tools; no alternate backend."""
import asyncio
import json
import os
import sys
from pathlib import Path


def main():
    home = Path(os.environ.get("HERMES_HOME", "")).resolve()
    if home.name != "maintenance" or home.parent.name != "profiles":
        raise ValueError("Maintenance profile required")
    repo = home.parent.parent / "hermes-agent"
    sys.path.insert(0, str(repo))
    from hermes_cli.env_loader import load_hermes_dotenv
    load_hermes_dotenv(hermes_home=home)
    from tools.web_tools import web_search_tool, web_extract_tool
    request = json.load(sys.stdin)
    if request["operation"] == "search":
        result = json.loads(web_search_tool(request["query"], limit=3))
        # Bound metadata while preserving URLs and title/excerpt provenance.
        for item in result.get("data", {}).get("web", []):
            description = str(item.get("description", ""))
            item["description"] = description[:1000]
            item["description_truncated"] = len(description) > 1000
    elif request["operation"] == "extract":
        result = json.loads(asyncio.run(web_extract_tool([request["url"]],char_limit=4000)))
    else:
        raise ValueError("Unknown operation")
    print(json.dumps(result,ensure_ascii=True))

if __name__ == "__main__":
    try: main()
    except Exception:
        # Never return arbitrary provider errors, environment or credentials.
        print(json.dumps({"success":False,"error":"Configured Hermes web operation unavailable; no retry."}))
