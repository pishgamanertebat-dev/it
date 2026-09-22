"""Offline compatibility checks against BALE_TEST_HERMES_ROOT."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram import Bot
from telegram.request import HTTPXRequest

from bale.adapter import BaleAdapter, PlatformConfig
from plugins.platforms.telegram import adapter as upstream


@pytest.mark.parametrize("configured", [False, True])
def test_direct_transport_matches_upstream_budgets(monkeypatch, configured):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "TELEGRAM_PROXY"):
        monkeypatch.setenv(key, "http://127.0.0.1:9")
    if configured:
        for key, value in {
            "POOL_SIZE": "37", "POOL_TIMEOUT": "3.5", "CONNECT_TIMEOUT": "4.5",
            "READ_TIMEOUT": "5.5", "WRITE_TIMEOUT": "6.5",
        }.items():
            monkeypatch.setenv("HERMES_TELEGRAM_HTTP_" + key, value)
        monkeypatch.setenv("HERMES_GATEWAY_HTTPX_MAX_KEEPALIVE", "7")
        monkeypatch.setenv("HERMES_GATEWAY_HTTPX_KEEPALIVE_EXPIRY", "1.5")
    else:
        for key in list(os.environ):
            if key.startswith(("HERMES_TELEGRAM_HTTP_", "HERMES_GATEWAY_HTTPX_")):
                monkeypatch.delenv(key)
    bale = object.__new__(BaleAdapter)
    forbidden = Mock(side_effect=AssertionError("Telegram routing consulted"))
    monkeypatch.setattr(upstream, "resolve_proxy_url", forbidden)
    monkeypatch.setattr(upstream, "discover_fallback_ips", forbidden)
    monkeypatch.setattr(bale, "_fallback_ips", forbidden)

    async def check():
        general, updates = await bale._build_ptb_requests()
        try:
            forbidden.assert_not_called()
            for request in (general, updates):
                assert request._client_kwargs["trust_env"] is False
                assert request._client_kwargs["proxy"] is None
                assert request._client._mounts == {}
                rebuilt = request._build_client()
                assert rebuilt._trust_env is False
                assert rebuilt._mounts == {}
                await rebuilt.aclose()
            assert general._client is not updates._client
            assert updates._client_kwargs["limits"].max_keepalive_connections == 0
            monkeypatch.setenv("HERMES_TELEGRAM_DISABLE_FALLBACK_IPS", "1")
            monkeypatch.setattr(upstream, "resolve_proxy_url", lambda *a, **k: None)
            monkeypatch.setattr(upstream, "HTTPXRequest", HTTPXRequest)
            parent = object.__new__(upstream.TelegramAdapter)
            parent.platform = upstream.Platform.TELEGRAM
            parent.config = SimpleNamespace(extra={})
            ref_general, ref_updates = await parent._build_ptb_requests()
            try:
                for actual, reference in zip((general, updates), (ref_general, ref_updates)):
                    assert actual._client_kwargs["timeout"] == reference._client_kwargs["timeout"]
                    assert actual._client_kwargs["limits"] == reference._client_kwargs["limits"]
                    assert actual._media_write_timeout == reference._media_write_timeout == 60.0
            finally:
                await ref_general.shutdown()
                await ref_updates.shutdown()
            observer = Mock()
            monkeypatch.setattr(bale, "_observe_polling_request_result", observer)
            monkeypatch.setattr(HTTPXRequest, "do_request", AsyncMock(return_value=(200, b'{}')))
            await updates.do_request(url="https://offline.invalid/getUpdates", method="POST")
            observer.assert_called_once_with(updates, None, (200, b'{}'))
        finally:
            await general.shutdown()
            await updates.shutdown()
    asyncio.run(check())


@pytest.mark.parametrize("content", [
    "✨ Session reset! Starting fresh.\nSecret model and tip",
    "✨ New session started!\nSecret model and tip",
    "◐ Session automatically reset (idle). Conversation history cleared.\nSecret",
    "The model provider is rate-limiting requests. Please wait a moment and try again.",
    "⏱️ The model provider is rate-limiting requests. Please wait a moment and try again.",
    "ordinary text",
])
def test_localized_notices(monkeypatch, content):
    send = AsyncMock()
    monkeypatch.setattr(upstream.TelegramAdapter, "send", send)
    adapter = object.__new__(BaleAdapter)
    asyncio.run(adapter.send("42", content, reply_to="7", metadata={"test": True}))
    expected = content
    if "Session" in content or "New session" in content:
        expected = "✨ گفتگو تازه شد!\nهر سوالی دارید بپرسید، آماده‌ام کمکتان کنم."
    elif "rate-limiting" in content:
        expected = "درحال حاضر سرویس قادر به پاسخگویی نمی باشد، دقایقی دیگر مجدد تلاش کنید"
    send.assert_awaited_once_with("42", expected, reply_to="7", metadata={"test": True})


@pytest.mark.parametrize("data,private,authorized", [
    ("ik:choose", True, True), ("ik:choose", False, True),
    ("ik:choose", True, False), ("other:choose", True, True),
])
def test_callback_dispatch(monkeypatch, data, private, authorized):
    adapter = object.__new__(BaleAdapter)
    adapter.platform = "bale"
    adapter.handle_message = AsyncMock()
    adapter._is_callback_user_authorized = Mock(return_value=authorized)
    parent = AsyncMock()
    monkeypatch.setattr(upstream.TelegramAdapter, "_handle_callback_query", parent)
    query = SimpleNamespace(data=data, id="q1", answer=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(id=42, type="private" if private else "group"), message_id=7),
        from_user=SimpleNamespace(id=42, first_name="User"))
    update = SimpleNamespace(callback_query=query)
    asyncio.run(adapter._handle_callback_query(update, None))
    if not data.startswith("ik:"):
        parent.assert_awaited_once_with(update, None)
    else:
        query.answer.assert_awaited_once()
        if private and authorized:
            event = adapter.handle_message.call_args.args[0]
            assert event.text == data and event.message_id == "callback:q1"
            assert event.raw_message["bale_inline_callback"] is True
            assert event.source.chat_id == "42"
        else:
            adapter.handle_message.assert_not_called()


def test_file_download_and_upload_use_bale_transport(monkeypatch):
    config = PlatformConfig(token="123:offline", extra={"proxy_url": "http://127.0.0.1:9"})
    adapter = BaleAdapter(config)
    calls = []

    async def fake_request(self, url, method, **kwargs):
        calls.append((self, url, method, kwargs))
        if url.endswith("/getFile"):
            result = {"file_id": "f1", "file_unique_id": "u1", "file_path": "documents/test.txt"}
        elif url.endswith("/sendDocument"):
            result = {"message_id": 1, "date": 0, "chat": {"id": 42, "type": "private"}}
        else:
            return 200, b"offline file contents"
        return 200, json.dumps({"ok": True, "result": result}).encode()

    monkeypatch.setattr(HTTPXRequest, "do_request", fake_request)
    async def check():
        general, updates = await adapter._build_ptb_requests()
        try:
            bot = Bot(config.token, base_url=config.extra["base_url"],
                      base_file_url=config.extra["base_file_url"], request=general, get_updates_request=updates)
            file = await bot.get_file("f1")
            assert await file.download_as_bytearray() == b"offline file contents"
            await bot.send_document(42, b"upload", filename="test.txt")
            assert [c[1] for c in calls] == [
                "https://tapi.bale.ai/bot123:offline/getFile",
                "https://tapi.bale.ai/file/bot123%3Aoffline/documents/test.txt",
                "https://tapi.bale.ai/bot123:offline/sendDocument",
            ]
            assert all(c[0] is general for c in calls)
        finally:
            await general.shutdown()
            await updates.shutdown()
    asyncio.run(check())


def test_manifest_and_new_directory_loader():
    from hermes_cli.plugins import PluginManager, parse_manifest_file
    from gateway.platform_registry import PlatformEntry
    directory = Path(__file__).parent
    manifest = parse_manifest_file(directory / "plugin.yaml", directory, "user", "")
    assert manifest is not None and manifest.name == "bale-platform" and manifest.kind == "platform"
    manager = PluginManager()
    module = manager._load_directory_module(manifest, module_name="hermes_plugins.bale_port_test")
    # Exercise the loader's relative import and validate registration kwargs
    # without discovering/loading any other plugins or touching global registry.
    class Context:
        def register_platform(self, **kwargs):
            self.entry = PlatformEntry(**kwargs)
    ctx = Context()
    module.register(ctx)
    assert ctx.entry.name == "bale"
    assert ctx.entry.required_env == ["BALE_BOT_TOKEN"]
    assert ctx.entry.allowed_users_env == "BALE_ALLOWED_USERS"
    assert callable(ctx.entry.adapter_factory)
