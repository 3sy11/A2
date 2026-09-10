"""Shared utilities."""

from __future__ import annotations

import uuid

from bollydog.globals import session


def uid(prefix: str = '') -> str:
    value = uuid.uuid4().hex[:16]
    return f'{prefix}{value}' if prefix else value


async def should_stop(session_id: str) -> bool:
    """Cooperative interrupt check (D-02)."""
    turn = await session.get(f'turn:{session_id}')
    return bool(turn.get('interrupted'))
