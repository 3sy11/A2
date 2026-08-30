"""Streaming chunk protocol — A2 frontend event contract."""

from __future__ import annotations

import time
from typing import Any

from bollydog.globals import session


async def chunk(
    type_: str,
    *,
    session_id: str = '',
    turn_id: str = '',
    agent: str = '',
    payload: dict | None = None,
    seq: int | None = None,
) -> dict[str, Any]:
    """Build a streaming event chunk dict."""
    if seq is None and session_id:
        seq = await next_seq(session_id)
    return {
        'type': type_,
        'seq': seq or 0,
        'session_id': session_id,
        'turn_id': turn_id,
        'agent': agent,
        'payload': payload or {},
        'ts': time.time(),
    }


async def next_seq(session_id: str) -> int:
    """Monotonic sequence counter per session (in-turn)."""
    key = f'chunk_seq:{session_id}'
    data = await session.get(key)
    current = data.get('seq', 0) if isinstance(data, dict) else 0
    current += 1
    await session.set(key, {'seq': current})
    return current
