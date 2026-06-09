"""Verify the conftest network guard blocks real httpx requests outside
respx scope (catches the Windows ProactorEventLoop leak documented in
`currentstateandplan.md`)."""
from __future__ import annotations

import httpx
import pytest
import respx


def test_sync_httpx_blocked_outside_respx() -> None:
    """A sync httpx.Client request to a non-loopback host must raise the
    conftest block error, not actually hit the network."""
    with pytest.raises(RuntimeError, match="httpx network access in test"):
        with httpx.Client() as client:
            client.get("https://example.com/")


@pytest.mark.asyncio
async def test_async_httpx_blocked_outside_respx() -> None:
    """The async transport block must also fire for httpx.AsyncClient."""
    with pytest.raises(RuntimeError, match="httpx async network access in test"):
        async with httpx.AsyncClient() as client:
            await client.get("https://example.com/")


def test_sync_httpx_inside_respx_passes_through() -> None:
    """Inside `respx.mock(...)`, the gate must NOT fire — respx handles
    the request."""
    with respx.mock() as mock:
        mock.get("https://example.com/").respond(200, text="ok")
        with httpx.Client() as client:
            resp = client.get("https://example.com/")
            assert resp.status_code == 200
            assert resp.text == "ok"


@pytest.mark.asyncio
async def test_async_httpx_inside_respx_passes_through() -> None:
    with respx.mock() as mock:
        mock.get("https://example.com/").respond(200, text="ok")
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://example.com/")
            assert resp.status_code == 200
            assert resp.text == "ok"
