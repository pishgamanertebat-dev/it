"""Bale adapter — a thin subclass of the Telegram adapter.

Bale's Bot API (docs.bale.ai) is Telegram's Bot API with minor changes, so we
reuse Hermes' full Telegram adapter and override only what differs:

  * API base URL  -> https://tapi.bale.ai/bot   (set via PlatformConfig.extra.base_url)
  * File base URL -> https://tapi.bale.ai/file/bot (PlatformConfig.extra.base_file_url)
  * Bot token     -> BALE_BOT_TOKEN             (instead of TELEGRAM_BOT_TOKEN)
  * Allowed users -> BALE_ALLOWED_USERS         (falls back to TELEGRAM_ALLOWED_USERS)

All slash commands, the command list, session binding, inline keyboards, media,
voice, and delivery behavior are inherited unchanged from TelegramAdapter — the
Bale experience is therefore exactly the Telegram experience.

NOTE on the base URL: python-telegram-bot builds the final endpoint as
``base_url + token`` (NOT ``base_url + "bot" + token``), so the Bale base URL
MUST end in ``/bot`` — i.e. ``https://tapi.bale.ai/bot``. That yields
``https://tapi.bale.ai/bot<token>`` which is exactly what Bale's Bot API expects
(the documented endpoint is ``https://tapi.bale.ai/bot<token>/getMe``).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bale Markdown de-escaping
# ---------------------------------------------------------------------------
# The inherited TelegramAdapter.format_message() converts agent markdown into
# Telegram *MarkdownV2*, which backslash-escapes every special character
# (``.  -  !  (  )  _`` ...). Telegram consumes those escapes because it is
# sent with ``parse_mode=MarkdownV2``.
#
# Bale, however, only implements a simple legacy Markdown (``**bold**``,
# ``_italic_``, ``[text](url)``, code fences) and does NOT understand the
# MarkdownV2 ``parse_mode``/escaping. It therefore renders the escape
# backslashes *literally*, so users see noise like ``سلام \- وضعیت\؟``.
#
# Fix: after the parent produces MarkdownV2 text, strip ONLY the escape
# backslashes that MarkdownV2 added, while leaving legitimate backslashes
# (Windows paths, code, escape sequences) untouched. We rely on the exact,
# well-defined transformations format_message() performs so this is a precise
# reverse, not a blind ``replace('\\', '')``.

# A backslash that MarkdownV2 inserted always precedes one of these specials.
_MDV2_UNESCAPE_RE = re.compile(r'\\([_*\[\]()~`>#+\-=|{}.!\\])')


def _bale_deescape_mdv2(text: str) -> str:
    """Reverse MarkdownV2 escaping so Bale shows text without literal ``\\``.

    Legitimate backslashes are preserved:
      * Outside code, only ``\\<special>`` pairs (which format_message added)
        are collapsed to ``<special>``. A lone backslash in normal prose is
        left as-is.
      * Inside inline code / fenced code, format_message doubled backslashes
        (``\\`` -> ``\\\\``) and escaped backticks (`` ` `` -> ``\\` ``) per
        the MarkdownV2 spec; we undo exactly those two doublings, restoring the
        original code content (paths, regex, escapes).
    """
    if not text:
        return text
    parts = re.split(r'(```[\s\S]*?```|`[^`]+`)', text)
    out = []
    for i, seg in enumerate(parts):
        if i % 2 == 1:
            # Code span/block: undo the exact doublings format_message applied.
            # Order matters: restore escaped backticks before backslashes.
            seg = seg.replace('\\`', '`').replace('\\\\', '\\')
            out.append(seg)
        else:
            out.append(_MDV2_UNESCAPE_RE.sub(r'\1', seg))
    return ''.join(out)

# Re-use the real Telegram adapter implementation.
from plugins.platforms.telegram.adapter import (  # noqa: E402
    TelegramAdapter,
    check_telegram_requirements,
    Platform,
    PlatformConfig,
)
from gateway.platform_registry import (  # noqa: E402
    PlatformEntry,
    platform_registry,
)

# IMPORTANT: PTB concatenates base_url + token, so this MUST end in "/bot".
_BALE_API_BASE = "https://tapi.bale.ai/bot"
# Downloads use a separate endpoint; PTB appends the token here as well.
_BALE_FILE_BASE = "https://tapi.bale.ai/file/bot"
_BALE_TOKEN_ENV = "BALE_BOT_TOKEN"
_BALE_ALLOWED_ENV = "BALE_ALLOWED_USERS"
_BALE_CHAT_ID_ENV = "BALE_CHAT_ID"


def _bale_token() -> str:
    return os.getenv(_BALE_TOKEN_ENV, "").strip()


def _bale_allowed_users() -> str:
    # Prefer a dedicated Bale allowlist; otherwise reuse the Telegram one,
    # plus the known Bale chat owner id.
    allowed = os.getenv(_BALE_ALLOWED_ENV, "").strip()
    if not allowed:
        allowed = os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
    chat_id = os.getenv(_BALE_CHAT_ID_ENV, "").strip()
    parts = [p for p in allowed.split(",") if p.strip()]
    if chat_id and chat_id not in parts:
        parts.append(chat_id)
    return ",".join(parts)


class BaleAdapter(TelegramAdapter):
    """Drop-in Bale adapter: same engine as Telegram, different endpoint/token."""

    # Bale message limit matches Telegram's 4096-char cap.
    MAX_MESSAGE_LENGTH = 4096

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        # Previously inherited from the local Telegram core customization.
        if content in {
            "The model provider is rate-limiting requests. Please wait a moment and try again.",
            "⏱️ The model provider is rate-limiting requests. Please wait a moment and try again.",
        }:
            content = "درحال حاضر سرویس قادر به پاسخگویی نمی باشد، دقایقی دیگر مجدد تلاش کنید"
        # Localize the complete reset banner before any rich/plain-text
        # delivery path, so model details and random tips cannot leak through.
        header = content.partition("\n")[0] if content else ""
        if header in {"✨ Session reset! Starting fresh.", "✨ New session started!"} or (
            header.startswith("◐ Session automatically reset (")
            and header.endswith("). Conversation history cleared.")
        ):
            content = "✨ گفتگو تازه شد!\nهر سوالی دارید بپرسید، آماده‌ام کمکتان کنم."
        return await super().send(chat_id, content, reply_to=reply_to, metadata=metadata)

    def format_message(self, content: str) -> str:  # type: ignore[override]
        """Format for Bale's simple Markdown (not Telegram MarkdownV2).

        The parent produces MarkdownV2 (with backslash-escaped specials), which
        Bale renders literally because it doesn't understand MarkdownV2. We take
        the parent's output and strip only the escape backslashes it added, so
        normal text reads cleanly while paths/code/URLs are preserved. The rest
        of the send pipeline (chunking, delivery) is inherited unchanged.
        """
        formatted = super().format_message(content)
        return _bale_deescape_mdv2(formatted)

    async def _handle_callback_query(self, update, context):
        query = update.callback_query
        if not query or not isinstance(query.data, str) or not query.data.startswith('ik:'):
            return await super()._handle_callback_query(update, context)
        # Acknowledge promptly; dispatch still passes through the existing
        # gateway authorization and Bale registration hooks.
        try:
            await query.answer()
        except Exception:
            logger.warning('Could not acknowledge Bale inline callback')
        message = query.message
        if not message or message.chat.type != 'private' or not query.from_user:
            return
        if not self._is_callback_user_authorized(str(query.from_user.id),
                chat_id=message.chat.id, chat_type='private',
                user_name=query.from_user.first_name):
            return
        from gateway.platforms.base import MessageEvent, MessageType
        from gateway.session import SessionSource
        event = MessageEvent(
            text=query.data,
            message_type=MessageType.TEXT,
            source=SessionSource(platform=self.platform, chat_id=str(message.chat.id),
                                 chat_type='dm', user_id=str(query.from_user.id),
                                 user_name=query.from_user.first_name),
            message_id='callback:' + str(query.id),
            raw_message={'bale_inline_callback': True, 'data': query.data,
                         'origin_message_id': str(message.message_id)},
        )
        await self.handle_message(event)

    def __init__(self, config: PlatformConfig):
        # Inject the Bale token + base_url BEFORE TelegramAdapter.__init__ reads
        # config.token / config.extra.  We mutate the passed config object in
        # place so the parent's connect() (which reads self.config.token and
        # extra["base_url"]) sees Bale values.
        if not getattr(config, "token", None):
            config.token = _bale_token()
        extra = dict(getattr(config, "extra", {}) or {})
        # Force the Bale endpoint. PTB appends the token to base_url, so the
        # value must end in "/bot".
        extra["base_url"] = _BALE_API_BASE
        extra["base_file_url"] = _BALE_FILE_BASE
        try:
            config.extra = extra
        except AttributeError:
            # PlatformConfig may use a different setter; fall back to __dict__.
            config.__dict__["extra"] = extra
        super().__init__(config)
        # Tell the engine this is Bale (some logs / delivery keys rely on platform).
        try:
            self.platform = Platform("bale")
        except Exception:
            pass

    # Override user authorization so it reads BALE_ALLOWED_USERS.
    def _authorized_user_ids(self):  # type: ignore[override]
        raw = _bale_allowed_users()
        return {u.strip() for u in raw.split(",") if u.strip()}

    async def _build_ptb_requests(self) -> tuple:
        """v0.21.4 request contract, with Bale's unconditional direct routing.

        Keep the upstream budgets and separate polling limits here; calling the
        Telegram builder would also perform Telegram proxy/IP discovery.
        """
        import httpx
        from telegram.request import HTTPXRequest
        from gateway.platforms._http_client_limits import platform_httpx_limits
        from utils import env_float, env_int

        request_kwargs = {
            "connection_pool_size": env_int("HERMES_TELEGRAM_HTTP_POOL_SIZE", 512),
            "pool_timeout": env_float("HERMES_TELEGRAM_HTTP_POOL_TIMEOUT", 8.0),
            "connect_timeout": env_float("HERMES_TELEGRAM_HTTP_CONNECT_TIMEOUT", 10.0),
            "read_timeout": env_float("HERMES_TELEGRAM_HTTP_READ_TIMEOUT", 20.0),
            "write_timeout": env_float("HERMES_TELEGRAM_HTTP_WRITE_TIMEOUT", 20.0),
            "media_write_timeout": 60.0,
        }
        base_limits = platform_httpx_limits()

        # proxy=None alone still lets HTTPX discover environment/system proxies.
        # Keep this policy in the clients' saved kwargs so pool rebuilds and
        # reconnects also stay direct. Never consult Telegram fallback discovery.
        general_httpx = {"trust_env": False}
        updates_httpx = {"trust_env": False}
        if base_limits is not None:
            general_httpx["limits"] = httpx.Limits(
                max_connections=request_kwargs["connection_pool_size"],
                max_keepalive_connections=base_limits.max_keepalive_connections,
                keepalive_expiry=base_limits.keepalive_expiry,
            )
            updates_httpx["limits"] = httpx.Limits(
                max_connections=request_kwargs["connection_pool_size"],
                max_keepalive_connections=0,
                keepalive_expiry=base_limits.keepalive_expiry,
            )
        logger.info("[Bale] Using direct transport (proxy and fallback discovery disabled)")
        request = HTTPXRequest(**request_kwargs, proxy=None, httpx_kwargs=general_httpx)
        updates = HTTPXRequest(**request_kwargs, proxy=None, httpx_kwargs=updates_httpx)
        return request, self._instrument_polling_request(updates)



def _build_adapter(config):
    adapter = BaleAdapter(config)
    try:
        adapter._notifications_mode = _resolve_notifications_mode()
    except Exception:
        adapter._notifications_mode = "important"
    return adapter


def _resolve_notifications_mode() -> str:
    try:
        import hermes_cli.gateway as gateway_mod
        return getattr(gateway_mod, "get_notifications_mode", lambda: "important")()
    except Exception:
        return "important"


def _is_connected(config) -> bool:
    token = getattr(config, "token", None)
    if not token:
        token = _bale_token()
    return bool(str(token).strip())


def _apply_yaml_config(yaml_cfg: dict, bale_cfg: dict) -> dict | None:
    """Inject Bale-specific extras (base_url) into PlatformConfig.extra.

    Mirrors the telegram apply_yaml_config_fn contract: called from
    load_gateway_config() BEFORE the adapter is constructed, so the adapter
    sees base_url in config.extra.
    """
    extras: dict = {}
    extras["base_url"] = _BALE_API_BASE
    extras["base_file_url"] = _BALE_FILE_BASE
    tok = _bale_token()
    if tok:
        extras["_bale_token"] = tok
    allowed = _bale_allowed_users()
    if allowed:
        extras["_bale_allowed"] = allowed
    return extras or None


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system."""
    ctx.register_platform(
        name="bale",
        label="Bale",
        adapter_factory=_build_adapter,
        check_fn=check_telegram_requirements,
        is_connected=_is_connected,
        required_env=[_BALE_TOKEN_ENV],
        install_hint="Set BALE_BOT_TOKEN in ~/.hermes/.env (and BALE_ALLOWED_USERS).",
        allowed_users_env=_BALE_ALLOWED_ENV,
        allow_all_env="BALE_ALLOW_ALL_USERS",
        cron_deliver_env_var="BALE_HOME_CHANNEL",
        max_message_length=4096,
        emoji="📦",
        allow_update_command=True,
        plugin_name="bale-platform",
        apply_yaml_config_fn=_apply_yaml_config,
    )
