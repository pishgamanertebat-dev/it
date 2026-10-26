"""Publish this session's generated technical pages into Admin's existing cache."""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import re
import tempfile

from integrations.hermes.technical_docs_boundary import technical_docs_enabled

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)
_MEDIA_LINE = re.compile(r"(?m)^([ \t]*MEDIA:)[ \t]*([^\r\n]+)")
_GENERATED_PAGE = re.compile(r"(?:partbook|manual_evidence)-p[1-9][0-9]*\.png")
_FAILURE = "تصویر برای ارسال آماده نشد؛ لطفاً دوباره درخواست تصویر را بفرستید."


def _publish_page(source: Path, home: Path, session_id: str) -> Path:
    from gateway.platforms.base import validate_media_delivery_path

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", session_id):
        raise ValueError("Invalid technical session")
    artifacts = (ROOT / "artifacts").resolve(strict=True)
    session_dir = (artifacts / session_id).resolve(strict=True)
    resolved = source.resolve(strict=True)
    if (session_dir != artifacts / session_id or resolved.parent != session_dir
            or not _GENERATED_PAGE.fullmatch(resolved.name) or not resolved.is_file()):
        raise ValueError("Not a generated page owned by this session")
    data = resolved.read_bytes()
    if (len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR"
            or not int.from_bytes(data[16:20], "big") or not int.from_bytes(data[20:24], "big")):
        raise ValueError("Invalid generated PNG")
    cache = (home / "image_cache" / "komatso_technical" / session_id).resolve()
    if not cache.is_relative_to(home):
        raise ValueError("Technical cache escapes this profile")
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (hashlib.sha256(data).hexdigest()[:16] + "-" + resolved.name)
    if target.is_symlink():
        raise ValueError("Technical cache target is a link")
    if not target.exists() or target.read_bytes() != data:
        fd, temporary = tempfile.mkstemp(prefix=".technical-", dir=cache)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    if not validate_media_delivery_path(str(target)):
        raise ValueError("Published page rejected by profile delivery policy")
    return target


def transform_technical_media(response_text, session_id="", platform="", **kwargs):
    """Native final-output hook; covers fresh tool results and same-session history retries.

    Earlier messages/prompts and other profiles are untouched. No allowlist is widened:
    the normal gateway validator still checks each published path before upload.
    """
    if platform not in {"bale", "telegram"} or "MEDIA:" not in response_text:
        return None
    from hermes_constants import get_hermes_home
    from hermes_cli.config import load_config_readonly

    home = get_hermes_home().resolve()
    if home.name != "admin" or home.parent.name != "profiles" or not technical_docs_enabled(home):
        return None
    artifacts = ROOT / "artifacts"

    def replace(match):
        source = Path(match[2].strip().strip("`\"'"))
        if not source.is_absolute() or not source.is_relative_to(artifacts):
            return match[0]
        return match[1] + _publish_page(source, home, session_id).as_posix()

    try:
        policy = load_config_readonly().get("gateway") or {}
        if policy.get("media_delivery_exclusive_roots") is not True:
            return None
        transformed = _MEDIA_LINE.sub(replace, response_text)
        return transformed if transformed != response_text else None
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Admin technical image preparation failed: %s", type(exc).__name__)
        return _FAILURE
