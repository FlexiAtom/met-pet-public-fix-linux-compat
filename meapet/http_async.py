"""
共享 httpx.AsyncClient（跑在 meapet.async_runtime 的 loop 上）。

真正的非阻塞 HTTP：不再靠 asyncio.to_thread(requests)。
"""
from __future__ import annotations

import atexit
import ssl
from typing import Any, Dict, Optional

import httpx

from meapet import USER_AGENT
from meapet.async_runtime import get_loop, submit

_client: Optional[httpx.AsyncClient] = None


def _new_client() -> httpx.AsyncClient:
    """Create a shared async HTTP client.

    Uses ``ssl.create_default_context()`` so that the OS-native certificate
    store (Schannel on Windows) is used for HTTPS verification.  This avoids
    relying on ``certifi.where()`` which can point to a non-existent
    ``cacert.pem`` inside a PyInstaller bundle.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0),
        follow_redirects=True,
        verify=ssl.create_default_context(),
        headers={"User-Agent": USER_AGENT},
    )


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = _new_client()
    return _client


async def aclose_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _shutdown() -> None:
    try:
        loop = get_loop()
        if loop.is_running():
            submit(aclose_client()).result(timeout=2)
    except Exception:
        pass


atexit.register(_shutdown)


async def post_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json: Any = None,
    timeout: Optional[float] = None,
) -> httpx.Response:
    client = await get_client()
    kw: Dict[str, Any] = {"headers": headers or {}, "json": json}
    if timeout is not None:
        kw["timeout"] = timeout
    return await client.post(url, **kw)


async def get_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> httpx.Response:
    client = await get_client()
    kw: Dict[str, Any] = {"headers": headers or {}}
    if timeout is not None:
        kw["timeout"] = timeout
    return await client.get(url, **kw)
