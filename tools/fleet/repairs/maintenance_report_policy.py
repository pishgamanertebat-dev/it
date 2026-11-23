"""Owner-authorized report receipt policy; no model chooses a path or authority."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / 'settings/maintenance_reports.json'


def permission(actor, chat, *, config=CONFIG, entry_config=None):
    from .maintenance_permission import approved_identity, permission as manual_permission
    settings = json.loads(Path(config).read_text('utf8'))
    if str(actor) != str(chat) or str(actor) not in settings['reporters']:
        raise PermissionError('اجازهٔ ثبت این گزارش تعمیرات را ندارید.')
    policy = settings['reporters'][str(actor)]
    if policy.get('scope') not in ('all', 'miscellaneous'):
        raise PermissionError('دامنهٔ گزارش مشخص نیست.')
    entry_config = Path(entry_config or ROOT / 'settings/maintenance_entry.json')
    target = json.loads(entry_config.read_text('utf8'))
    name = approved_identity(actor)
    if policy['scope'] == 'all':
        manual_permission(actor, entry_config)
    if not isinstance(name, str) or not name.strip():
        raise PermissionError('نام تأییدشدهٔ ثبت‌کننده موجود نیست.')
    return dict(policy, source=target['source'], recorder=name)


def enabled(config=CONFIG):
    try:
        return json.loads(Path(config).read_text('utf8')).get('enabled') is True
    except (OSError, ValueError):
        return False


def partial_enabled(settings):
    return settings['scope'] == 'all' and settings.get('partial_confirmation') is True
