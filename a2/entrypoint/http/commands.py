"""HTTP entry commands."""

from __future__ import annotations

import uuid

from bollydog.globals import registry
from bollydog.models.base import BaseCommand

from a2.kernel import relay_gen


class Chat(BaseCommand):
    """SSE chat endpoint — relays to agent Reply."""

    message: str = ''
    session_id: str = ''
    user_id: str = ''
    agent: str = 'assistant'

    async def __call__(self):
        sid = self.session_id or uuid.uuid4().hex
        open_cmd = registry.resolve('session.store.OpenSession')(
            session_id=sid,
            user_id=self.user_id,
            agent=self.agent,
        )
        yield open_cmd
        reply = registry.resolve(f'agent.{self.agent}.Reply')(
            session_id=sid,
            inputs=[{'role': 'user', 'content': self.message, 'name': 'user'}],
            structured_schema=None,
            resume_state=None,
            max_iters=0,
        )
        async for evt in relay_gen(reply):
            yield evt
