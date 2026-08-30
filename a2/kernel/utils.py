"""Shared utilities."""

from __future__ import annotations

import functools
import logging
import uuid
from typing import Callable

from bollydog.globals import session

logger = logging.getLogger(__name__)


def uid(prefix: str = '') -> str:
    value = uuid.uuid4().hex[:16]
    return f'{prefix}{value}' if prefix else value


async def should_stop(session_id: str) -> bool:
    """Cooperative interrupt check (D-02)."""
    turn = await session.get(f'turn:{session_id}')
    return bool(turn.get('interrupted'))


def safe_subscriber(fn: Callable):
    """Wrap subscriber callbacks — catch exceptions, never propagate (D-12)."""

    @functools.wraps(fn)
    async def wrapper(self, message, *args, **kwargs):
        try:
            return await fn(self, message, *args, **kwargs)
        except Exception as exc:
            logger.exception('subscriber %s failed: %s', fn.__name__, exc)
            return {'ok': False, 'error': str(exc)}

    return wrapper
