"""Shared HTTP forwarding logic for protocol handlers.

Provides ``forward()`` (non-streaming) and ``open_stream()`` (streaming) used
by both ``openai_handler`` and ``anthropic_handler``. Each handler passes its
own headers; the call sites in ``server.py`` stay unchanged.

Transparency contract: the upstream's real status code and body are returned
verbatim so the server can pass them through to the client. No error is
synthesized here -- a timeout / connection failure is reported as a
``None`` status (``reason`` set) so the server can decide (504). In-request
fallback lives elsewhere (it doesn't: the server marks the provider down and
the *next* request routes around it).
"""

from dataclasses import dataclass

import httpx

from llmport.models.provider import ProviderConfig


@dataclass
class UpstreamResult:
    """Outcome of one non-streaming forward attempt.

    ``status is None`` means the upstream never responded (timeout /
    unreachable); ``reason`` is then ``"timeout"`` or ``"unreachable"`` and
    ``body`` is empty. Otherwise ``status``/``body``/``content_type`` carry
    the real upstream response, whatever its code.
    """
    status: int | None
    body: bytes
    content_type: str | None
    reason: str | None  # "timeout" | "unreachable" | None


class OpenedStream:
    """An open streaming connection to the upstream.

    Returned by :func:`open_stream` on a successful connect. The caller peeks
    :attr:`status` *before* committing an HTTP 200 to the client: an error
    status is read in full and passed through; a 2xx is piped via
    :meth:`aiter_bytes`. The caller must :meth:`aclose` when done.
    """

    def __init__(self, resp: httpx.Response, client: httpx.AsyncClient):
        self._resp = resp
        self._client = client

    @property
    def status(self) -> int:
        return self._resp.status_code

    @property
    def content_type(self) -> str | None:
        return self._resp.headers.get("content-type")

    async def aread(self) -> bytes:
        return await self._resp.aread()

    async def aiter_bytes(self):
        async for chunk in self._resp.aiter_bytes():
            yield chunk

    async def aclose(self) -> None:
        await self._resp.aclose()
        await self._client.aclose()


async def forward(
    request_body: dict,
    provider: ProviderConfig,
    model_name: str,
    path: str,
    headers: dict,
    timeout: float = 120.0,
) -> UpstreamResult:
    """Forward a non-streaming request. Returns the raw upstream response."""
    body = {**request_body, "model": model_name}
    url = f"{provider.base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(
                url, json=body, headers=headers, allow_redirects=False
            )
        except httpx.TimeoutException:
            return UpstreamResult(None, b"", None, "timeout")
        except httpx.ConnectError:
            return UpstreamResult(None, b"", None, "unreachable")
        return UpstreamResult(
            resp.status_code, resp.content, resp.headers.get("content-type"), None
        )


async def open_stream(
    request_body: dict,
    provider: ProviderConfig,
    model_name: str,
    path: str,
    headers: dict,
    timeout: float = 300.0,
) -> OpenedStream | str:
    """Open a streaming request to the upstream.

    Returns an :class:`OpenedStream` on connect (caller peeks ``.status``),
    or ``"timeout"`` / ``"unreachable"`` if the upstream never responded.
    """
    body = {**request_body, "model": model_name}
    body.setdefault("stream", True)
    url = f"{provider.base_url.rstrip('/')}{path}"
    forward_headers = {**headers, "Accept": "text/event-stream"}
    client = httpx.AsyncClient(timeout=timeout)
    try:
        req = client.build_request(
            "POST", url, json=body, headers=forward_headers
        )
        resp = await client.send(req, stream=True, follow_redirects=False)
    except httpx.TimeoutException:
        await client.aclose()
        return "timeout"
    except httpx.ConnectError:
        await client.aclose()
        return "unreachable"
    return OpenedStream(resp, client)
