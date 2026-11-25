"""Fleet Performance & Downtime Analytics module for KomatsoAI."""
from __future__ import annotations

__all__ = [
    "MODULE_VERSION",
    "DEFAULT_RUNTIME_DIR",
    "DEFAULT_DB_PATH",
]

from pathlib import Path

MODULE_VERSION = "1.0.0"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNTIME_DIR = PROJECT_ROOT / "runtime" / "fleet-downtime"
DEFAULT_DB_PATH = DEFAULT_RUNTIME_DIR / "downtime.sqlite3"
