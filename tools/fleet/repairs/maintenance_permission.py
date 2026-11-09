"""Existing repair allowlist plus fresh approved private Bale identity."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from tools.authorization import AuthorizationStore

CONFIG = Path(__file__).resolve().parents[3] / 'settings/maintenance_entry.json'


def permission(actor, config=CONFIG):
    settings = json.loads(Path(config).read_text(encoding='utf-8'))
    if isinstance(actor, bool) or not re.fullmatch(r'[1-9][0-9]*', str(actor)) or str(actor) not in settings['allowed_users']:
        raise PermissionError('اجازهٔ ثبت تعمیرات را ندارید.')
    try:
        store = AuthorizationStore()
        with closing(store._connect()) as db:
            store._version(db)
            identity = db.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?",
                                  (str(actor),)).fetchone()
        if identity != (str(actor), 'approved'):
            raise PermissionError('هویت تأییدشدهٔ بله برای ثبت تعمیرات لازم است.')
    except (sqlite3.Error, OSError) as exc:
        raise PermissionError('بررسی مجوز تعمیرات ممکن نیست؛ ثبت متوقف شد.') from exc
    return settings
