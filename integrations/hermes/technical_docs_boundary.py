"""Explicit curated tool surface boundary; identity approval remains the Gateway's job.

No authorization database, Function resolver, host tool or user write capability.
Both fixed workers validate the same trusted configuration as the plugin handlers.
"""
from pathlib import Path
import yaml

TOOLSET = 'komatso_technical_docs'
PLUGIN = 'komatso-technical-docs'

def technical_docs_enabled(home):
    try:
        config = yaml.safe_load((Path(home).resolve()/'config.yaml').read_text(encoding='utf-8')) or {}
        enabled = (config.get('plugins') or {}).get('enabled') or []
        disabled = (config.get('agent') or {}).get('disabled_toolsets') or []
        surfaces = config.get('platform_toolsets') or {}
        # Explicit rollout to the current messaging surfaces, never implicit CLI/tool access.
        return any(plugin in enabled and toolset not in disabled and any(
            toolset in (surfaces.get(platform) or []) for platform in ('bale','telegram'))
            for plugin,toolset in [(PLUGIN,TOOLSET),('komatso-maintenance-manual','komatso_maintenance')])
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
        return False

def runtime_home(home):
    home = Path(home).resolve()
    return home.parent.parent if home.parent.name.casefold() == 'profiles' else home
