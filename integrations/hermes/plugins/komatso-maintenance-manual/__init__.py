"""Compatibility loader; all Technical Docs behavior has one canonical source."""
from pathlib import Path
_source = Path("E:/KomatsoAI/integrations/hermes/plugins/komatso-technical-docs/__init__.py")
__file__ = str(_source)
exec(compile(_source.read_text(encoding="utf-8"), str(_source), "exec"), globals())
# Legacy discovery integrations may still select this historic toolset.
TOOLSET = "komatso_maintenance"
