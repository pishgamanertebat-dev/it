"""Public web research surface; browser policy lives at the actual network dial."""
from .browser import browser_action, runtime_paths

ACTIONS={
 "navigate": ("Open a public HTTP/HTTPS page, run its JavaScript, and read text/links.", {"url":{"type":"string"}}, ["url"]),
 "snapshot": ("Read the current public page and interactive element references.", {"char_limit":{"type":"integer"}}, []),
 "click": ("Click a page element by @eN reference or CSS selector.", {"target":{"type":"string"}}, ["target"]),
 "type": ("Fill a public page input.", {"target":{"type":"string"},"text":{"type":"string"}}, ["target","text"]),
 "hover": ("Hover a page element.", {"target":{"type":"string"}}, ["target"]),
 "select": ("Select a public page option.", {"target":{"type":"string"},"value":{"type":"string"}}, ["target","value"]),
 "press": ("Press a browser key such as Enter, Tab, or Escape.", {"key":{"type":"string"}}, ["key"]),
 "scroll": ("Scroll the public page.", {"x":{"type":"integer"},"y":{"type":"integer"}}, []),
 "back": ("Go back in public browsing history.", {}, []),
 "tabs": ("List this research session's public tabs.", {}, []),
 "switch_tab": ("Switch to a public tab by index.", {"index":{"type":"integer"}}, ["index"]),
 "frame": ("Select an iframe by index; zero returns to the main frame.", {"index":{"type":"integer"}}, ["index"]),
 "drag": ("Drag one public page element to another.", {"source":{"type":"string"},"target":{"type":"string"}}, ["source","target"]),
 "screenshot": ("View a screenshot of the current public page.", {"question":{"type":"string"}}, []),
 "images": ("List images displayed by the current public page.", {}, []),
 "console": ("Read browser logs or evaluate JavaScript in the public page for DOM research.", {"expression":{"type":"string"}}, []),
 "close": ("Close this public research browser session.", {}, []),
}

def available():
    try:
        runtime_paths(); return True
    except RuntimeError:
        return False

def register(ctx):
    for action,(description,properties,required) in ACTIONS.items():
        name="public_browser_"+action
        def handler(args, _action=action, **kw):
            return browser_action(_action,args,kw.get("task_id") or kw.get("session_id"))
        ctx.register_tool(name=name,toolset="komatso_public_browser",
            schema={"name":name,"description":description,"parameters":{
                "type":"object","properties":properties,"required":required,"additionalProperties":False}},
            handler=handler,check_fn=available,description=description)
