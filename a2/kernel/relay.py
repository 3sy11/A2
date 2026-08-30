"""Command relay — stream bubbling pattern (D-01)."""

from __future__ import annotations

from typing import AsyncIterator

from bollydog.globals import services
from bollydog.models.base import BaseCommand


async def _dispatch(cmd: BaseCommand):
    """Dispatch via Hub or inline ExecuteService."""
    hub = services.get('bollydog.HubService')
    if hub and hub._started.is_set():
        await hub.dispatch(cmd)
    else:
        executor = services.get('bollydog.ExecuteService')
        if executor is None:
            raise RuntimeError('no hub or executor available')
        await executor._submit(cmd)


async def relay_gen(cmd: BaseCommand) -> AsyncIterator[dict]:
    """Dispatch a streaming command and relay all chunks to the caller."""
    await _dispatch(cmd)
    async for item in cmd.state:
        yield item
    if cmd.state.done() and not cmd.state.cancelled():
        cmd.state.result()


async def relay(cmd: BaseCommand) -> dict:
    """Dispatch a non-streaming command and await its result."""
    await _dispatch(cmd)
    return await cmd.state
