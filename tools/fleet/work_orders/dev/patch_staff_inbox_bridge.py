"""Apply the staff inbox routing change to the locally installed Bale bridge."""
import argparse
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path('C:/Users/win-10/AppData/Local/hermes/plugins/komatso-bale-registry/__init__.py')
OLD = '    if text.isdecimal() or text in {"تایید", "تأیید"} or text.startswith(("تایید AF-", "تأیید AF-", "تایید GR-", "تأیید GR-")):'
NEW = '''    receipt_text = " ".join(text.translate(str.maketrans("كي", "کی")).replace("\\u200c", " ").split())
    if receipt_text.isdecimal() or receipt_text in {"حکم کار", "تایید", "تأیید"} or receipt_text.startswith(("تایید AF-", "تأیید AF-", "تایید GR-", "تأیید GR-", "تایید OC-", "تأیید OC-")):'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    source = TARGET.read_text(encoding='utf-8')
    if NEW in source:
        print('Staff inbox bridge is already installed.')
        return
    if source.count(OLD) != 1:
        raise RuntimeError('Bridge changed; inspect routing before applying.')
    updated = source.replace(OLD, NEW)
    compile(updated, str(TARGET), 'exec')
    if args.apply:
        backup = Path('E:/KomatsoAI/backup/staff_inbox') / datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') / '__init__.py'
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(TARGET.read_bytes())
        TARGET.write_text(updated, encoding='utf-8')
        print(f'Installed; backup: {backup}. Restart the idle gateway to load the change.')
    else:
        print('Bridge patch validated. Use --apply to install.')


if __name__ == '__main__':
    main()
