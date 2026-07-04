from __future__ import annotations

import asyncio
import socket
from collections.abc import Generator
from typing import Any

import httpx
import pytest
from respx.mocks import HTTPCoreMocker

from localjobscout.scrapers import fetcher as scraper_fetcher
from localjobscout.scrapers import politeness


def _respx_is_active() -> bool:
    """True iff a `respx.mock(...)` scope is currently open. respx tracks
    its active routers in a class-level list on `HTTPCoreMocker`; it is
    populated on `__enter__` / `.start()` and cleared on `__exit__` /
    `.stop()`. When non-empty, the respx router is intercepting requests
    and will assert / mock them on its own."""
    return bool(HTTPCoreMocker.routers)


@pytest.fixture(scope="session", autouse=True)
def block_real_network() -> Generator[None, None, None]:
    """Block real network access in tests.

    Layered defense:
    1. `socket.socket.connect` — catches plain sockets and asyncio's
       selector event loop (works on macOS / Linux / Windows-Selector).
    2. `httpx` transports — catches `httpx.AsyncClient` on Windows
       ProactorEventLoop, where `socket.connect` is bypassed by the
       underlying overlapped-IO transport, and any other httpcore path
       that doesn't surface through plain sockets.

    `respx.mock(...)` replaces the httpcore connection pool with its own
    router callable. When a respx-mocked request flows through, the call
    is dispatched by respx code itself — we detect that frame via stack
    introspection and pass the request through so the router can match
    routes / produce a mocked response without our block firing.
    """
    mp = pytest.MonkeyPatch()
    _orig_connect = socket.socket.connect

    def _blocked_connect(*args: Any, **kwargs: Any) -> Any:
        addr = args[1] if len(args) > 1 else kwargs.get("address")
        if isinstance(addr, tuple) and addr[0] in ("127.0.0.1", "::1"):
            return _orig_connect(*args, **kwargs)
        raise RuntimeError("network access in test")

    mp.setattr(socket.socket, "connect", _blocked_connect)

    _orig_sync = httpx.HTTPTransport.handle_request
    _orig_async = httpx.AsyncHTTPTransport.handle_async_request

    def _gated_sync(self: Any, request: Any) -> Any:
        if _respx_is_active():
            return _orig_sync(self, request)
        raise RuntimeError(
            f"httpx network access in test (URL: {request.url}). "
            "Mock the request with respx or rewrite the test."
        )

    async def _gated_async(self: Any, request: Any) -> Any:
        if _respx_is_active():
            return await _orig_async(self, request)
        raise RuntimeError(
            f"httpx async network access in test (URL: {request.url}). "
            "Mock the request with respx or rewrite the test."
        )

    mp.setattr(httpx.HTTPTransport, "handle_request", _gated_sync)
    mp.setattr(
        httpx.AsyncHTTPTransport,
        "handle_async_request",
        _gated_async,
    )

    yield
    mp.undo()


@pytest.fixture(autouse=True)
def reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    politeness.reset()
    # Ensure the Scrapling fetch adapter is inactive in unit tests so
    # polite_get uses the httpx path that respx mocks. run_scan() configures
    # it in production; without this reset that global state would leak across
    # tests and route mocked fetches to the real network.
    scraper_fetcher.reset()

    async def _instant(_: float) -> None:
        pass

    monkeypatch.setattr(asyncio, "sleep", _instant)
