"""
Shared, pooled HTTP client.

Every collector used to open its own `httpx.AsyncClient` per request, which
meant a fresh SSL context + TCP/TLS handshake for every single upstream call
(12+ per parcel, thousands per batch). `pooled_client()` is a drop-in
replacement for that pattern:

    async with pooled_client(timeout=8.0, headers=H) as client:
        r = await client.get(url, params=...)

It hands back a thin view over ONE process-wide client, so connections to
FL DOR / TIGER / Supabase / FEMA stay warm and are reused (HTTP keep-alive).
The per-call `timeout` and `headers` are applied to each request, and leaving
the `async with` block does NOT close the shared pool.
"""
from __future__ import annotations

import asyncio
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx

_LIMITS = httpx.Limits(max_connections=200, max_keepalive_connections=60, keepalive_expiry=30.0)

_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def _no_cookie_jar() -> CookieJar:
    # The old per-call clients never carried cookies between requests; keep it
    # that way so one upstream's session cookie can't leak into another call.
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


def get_shared_client() -> httpx.AsyncClient:
    """Return the process-wide client, (re)creating it for the running loop."""
    global _client, _client_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(limits=_LIMITS, cookies=_no_cookie_jar())
        _client_loop = loop
    return _client


async def close_shared_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


_UNSET = object()


class _ClientView:
    """Per-call defaults (timeout, headers, follow_redirects) over the shared pool."""

    __slots__ = ("_client", "_timeout", "_headers", "_follow")

    def __init__(self, client: httpx.AsyncClient, timeout, headers, follow_redirects):
        self._client = client
        self._timeout = timeout
        self._headers = dict(headers) if headers else None
        self._follow = follow_redirects

    def _kw(self, kw: dict) -> dict:
        if self._timeout is not _UNSET and "timeout" not in kw:
            kw["timeout"] = self._timeout
        if self._headers:
            merged = dict(self._headers)
            if kw.get("headers"):
                merged.update(kw["headers"])
            kw["headers"] = merged
        if self._follow is not _UNSET and "follow_redirects" not in kw:
            kw["follow_redirects"] = self._follow
        return kw

    async def request(self, method: str, url, **kw) -> httpx.Response:
        return await self._client.request(method, url, **self._kw(kw))

    async def get(self, url, **kw) -> httpx.Response:
        return await self.request("GET", url, **kw)

    async def post(self, url, **kw) -> httpx.Response:
        return await self.request("POST", url, **kw)

    async def put(self, url, **kw) -> httpx.Response:
        return await self.request("PUT", url, **kw)

    async def patch(self, url, **kw) -> httpx.Response:
        return await self.request("PATCH", url, **kw)

    async def delete(self, url, **kw) -> httpx.Response:
        return await self.request("DELETE", url, **kw)

    async def head(self, url, **kw) -> httpx.Response:
        return await self.request("HEAD", url, **kw)

    def stream(self, method: str, url, **kw):
        return self._client.stream(method, url, **self._kw(kw))


class pooled_client:
    """`async with pooled_client(timeout=..., headers=...) as client:`"""

    def __init__(self, timeout=_UNSET, headers=None, follow_redirects=_UNSET):
        self._view = _ClientView(get_shared_client(), timeout, headers, follow_redirects)

    async def __aenter__(self) -> _ClientView:
        return self._view

    async def __aexit__(self, *exc) -> None:
        return None
