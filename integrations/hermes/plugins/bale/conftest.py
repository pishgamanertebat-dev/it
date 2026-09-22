"""Use an explicit Hermes checkout and block network access in Bale tests."""
import asyncio
import os
import socket
import sys
from pathlib import Path

import pytest

root = Path(os.environ["BALE_TEST_HERMES_ROOT"]).resolve()
if not (root / "plugins/platforms/telegram/adapter.py").is_file():
    raise RuntimeError("BALE_TEST_HERMES_ROOT must point to a Hermes checkout")
sys.path.insert(0, str(root))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # Windows creates a local socketpair when constructing an event loop.
    # Create it before blocking sockets; test coroutines still cannot connect.
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(asyncio, "run", loop.run_until_complete)
    def denied(*args, **kwargs):
        raise AssertionError("Network access is forbidden in Bale targeted tests")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    yield
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()
