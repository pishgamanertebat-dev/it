"""Existing repair allowlist plus fresh approved private Bale identity."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from tools.authorization import AuthorizationStore

CONFIG = Path(__file__).resolve().parents[3] / 'settings/maintenance_entry.json'


def approved_identity(actor):
    if isinstance(actor, bool) or not re.fullmatch(r'[1-9][0-9]*', str(actor)):
        raise PermissionError('هویت حساب بله معتبر نیست.')
    try:
        store = AuthorizationStore()
        with closing(store._connect()) as db:
            store._version(db)
            identity = db.execute("SELECT chat_id,registration_status,verified_name FROM channel_users WHERE platform='bale' AND user_id=?",
                                  (str(actor),)).fetchone()
        if identity is None or identity[:2] != (str(actor), 'approved'):
            raise PermissionError('هویت تأییدشدهٔ بله برای ثبت تعمیرات لازم است.')
    except (sqlite3.Error, OSError) as exc:
        raise PermissionError('بررسی مجوز تعمیرات ممکن نیست؛ ثبت متوقف شد.') from exc
    return identity[2]


def permission(actor, config=CONFIG):
    settings = json.loads(Path(config).read_text(encoding='utf-8'))
    if str(actor) not in settings['allowed_users']:
        raise PermissionError('اجازهٔ ثبت تعمیرات را ندارید.')
    return dict(settings, recorder=approved_identity(actor))
