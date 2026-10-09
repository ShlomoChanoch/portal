"""SSE helpers (FR-3, FR-7) — live Build/Run streams that survive refresh.

Streams are persisted lines tailed on reconnect (UX-DR11). The view never mints
terminal states; this adapter only reports what the projection says.
"""

from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable

from .logpersist import LogStore

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "Content-Type": "text/event-stream",
}


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def stream_log(
    store: LogStore,
    tenant_id: int,
    slug: str,
    stream: str,
    initial_offset: int = 0,
    poll_seconds: float = 0.4,
    terminal: Callable[[], Awaitable[bool]] | None = None,
):
    """Async generator tailing a persisted log stream.

    Emits `line` events with {ts, line, offset}. When the terminal predicate
    resolves true, emits an explicit end-of-stream event (UX-DR11: "Build
    complete." / "Instance stopped." never a frozen silent screen) and closes.
    """
    offset = initial_offset
    announced_done = False
    while True:
        if terminal is not None and await terminal():
            if not announced_done:
                yield sse_event("end", {"done": True})
                announced_done = True
            break
        lines = store.tail(tenant_id, slug, stream, offset)
        if lines:
            for ts, text in lines:
                offset += 1
                yield sse_event("line", {"ts": ts, "line": text, "offset": offset})
        else:
            yield sse_event("ping", {"offset": offset})
        await asyncio.sleep(poll_seconds)


def event_stream_response(gen, *, headers: dict | None = None):
    from fastapi.responses import StreamingResponse

    h = dict(SSE_HEADERS)
    if headers:
        h.update(headers)
    return StreamingResponse(gen, headers=h)