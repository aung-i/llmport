"""TUI components for llmport."""

import httpx


async def async_get_json(url: str) -> dict | list | None:
    """Helper to fetch JSON from the control API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return None
