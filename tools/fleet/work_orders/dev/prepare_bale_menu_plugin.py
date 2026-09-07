"""Stage the two registration-approved menu entry points; never installs them."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


HERMES_ROOT = Path(r"C:\Users\win-10\AppData\Local\hermes")
STAGE_ROOT = Path(r"E:\KomatsoAI\runtime\work_order_bale_pilot")

BRIDGE = '''def _handle_work_order_menu(event, gateway):
    # Extend the existing tools namespace: Hermes also owns a tools package.
    # Never replace Hermes' tools module or its existing search paths.
    try:
        import tools as tools_package
        project_tools = r"E:\\KomatsoAI\\tools"
        if project_tools not in tools_package.__path__:
            tools_package.__path__.append(project_tools)
        from tools.fleet.work_orders.channels.bale.message_handler import handle_work_order_message
        return handle_work_order_message(event, gateway, send=_send)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Work-order bridge unavailable")
        text = " ".join((event.text or "").translate(str.maketrans("كي", "کی")).replace("\\u200c", " ").split())
        if text == "حکم کار" or text.isdecimal() or text in {"انصراف", "لغو", "/cancel"}:
            chat_id = getattr(event.source, "chat_id", None)
            if chat_id:
                try:
                    _send(gateway, str(chat_id), "منوی حکم کار موقتاً در دسترس نیست. لطفاً دوباره تلاش کنید.")
                except Exception:
                    logging.getLogger(__name__).exception("Could not schedule menu error reply")
            return {"action": "skip", "reason": "work-order-bridge-error"}
        return None


'''


def stage_plugin() -> Path:
    source = HERMES_ROOT / "plugins" / "komatso-bale-registry" / "__init__.py"
    original = source.read_bytes()
    text = original.decode("utf-8").replace("\r\n", "\n")
    replacements = (
        ("def _handle_bale(event, gateway, **kwargs):", BRIDGE + "def _handle_bale(event, gateway, **kwargs):"),
        (
            "        # پیام‌های عادی مدیر همچنان به Agent می‌روند.\n        return None",
            "        # Work-order requests use their own permission check.\n        return _handle_work_order_menu(event, gateway)",
        ),
        (
            '        if user_status == "approved":\n            conn.commit()\n            return None',
            '        if user_status == "approved":\n            conn.commit()\n            return _handle_work_order_menu(event, gateway)',
        ),
    )
    if "def _handle_work_order_menu(" in text:
        raise RuntimeError("Bridge already exists; inspect before changing it.")
    for old, new in replacements:
        if text.count(old) != 1:
            raise RuntimeError("Plugin changed; expected insertion point is not unique.")
        text = text.replace(old, new, 1)
    compile(text, str(source), "exec")
    newline = "\r\n" if b"\r\n" in original else "\n"
    staged_bytes = text.replace("\n", newline).encode("utf-8")
    stage = STAGE_ROOT / "komatso-bale-registry"
    stage.mkdir(parents=True, exist_ok=True)
    (STAGE_ROOT / "original_init.py").write_bytes(original)
    target = stage / "__init__.py"
    target.write_bytes(staged_bytes)
    manifest = {
        "target": str(source),
        "staged": str(target),
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "staged_sha256": hashlib.sha256(staged_bytes).hexdigest(),
        "protected_sha256": {
            name: hashlib.sha256((HERMES_ROOT / name).read_bytes()).hexdigest()
            for name in (".env", "config.yaml", "auth.json")
            if (HERMES_ROOT / name).is_file()
        },
    }
    (STAGE_ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return target


if __name__ == "__main__":
    print(stage_plugin())
