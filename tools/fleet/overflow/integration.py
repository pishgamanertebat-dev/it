"""Stage/install the small bridge in the existing Hermes Bale registry."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TARGET = Path(r'C:\Users\win-10\AppData\Local\hermes\plugins\komatso-bale-registry\__init__.py')
STAGE = ROOT / 'runtime/overflow-integration'
BRIDGE = '''def _handle_overflow_report(event, gateway):
    try:
        import tools as tools_package
        project_tools = r"E:\\KomatsoAI\\tools"
        if project_tools not in tools_package.__path__:
            tools_package.__path__.append(project_tools)
        from tools.fleet.overflow.bale import handle_overflow_message
        return handle_overflow_message(event, gateway, send=_send)
    except Exception:
        import logging
        import re
        logging.getLogger(__name__).exception("Overflow report bridge unavailable")
        text = " ".join((event.text or "").replace("ي", "ی").replace("\\u200c", " ").split())
        if re.match(r"^سر\\s*ریز(?:\\s|$)", text):
            _send(gateway, str(event.source.chat_id), "گزارش سرریز موقتاً در دسترس نیست؛ لطفاً دوباره تلاش کنید.")
            return {"action": "skip", "reason": "overflow-bridge-error"}
        return None


'''
INSERT = '''    # Public daily overflow lookup, before registration and work-order menus.
    result = _handle_overflow_report(event, gateway)
    if result is not None:
        return result

'''


def patched(original):
    if 'def _handle_overflow_report(' in original:
        raise RuntimeError('Overflow bridge is already installed')
    marker = 'def _handle_bale(event, gateway, **kwargs):'
    location = '    # Only a persisted assigned recipient can acknowledge here;'
    if original.count(marker) != 1 or original.count(location) != 1:
        raise RuntimeError('Bale registry changed; inspect insertion points')
    text = original.replace(marker, BRIDGE + marker).replace(location, INSERT + location)
    compile(text, str(TARGET), 'exec')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    if args.install:
        original = (STAGE / 'original.py').read_bytes()
        if TARGET.read_bytes() != original:
            raise RuntimeError('Live registry changed after staging')
        candidate = (STAGE / 'staged.py').read_bytes()
        if candidate.decode('utf-8') != patched(original.decode('utf-8').replace('\r\n', '\n')):
            raise RuntimeError('Staged bridge does not match the reviewed patch')
        backup = ROOT / 'backup/overflow' / hashlib.sha256(original).hexdigest() / '__init__.py'
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(original)
        temporary = TARGET.with_suffix('.overflow.tmp')
        temporary.write_bytes(candidate)
        temporary.replace(TARGET)
        print(f'Installed. Backup: {backup}. Restart the idle gateway to load the bridge.')
    else:
        original = TARGET.read_bytes()
        text = patched(original.decode('utf-8').replace('\r\n', '\n'))
        STAGE.mkdir(parents=True, exist_ok=True)
        (STAGE / 'original.py').write_bytes(original)
        (STAGE / 'staged.py').write_bytes(text.encode('utf-8'))
        print(STAGE / 'staged.py')


if __name__ == '__main__':
    main()
