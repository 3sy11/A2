"""Agent tools — HITL and delivery."""

from __future__ import annotations

from typing import ClassVar

from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand


class AskHuman(BaseCommand):
    """Ask the user a question and park the turn."""
    group: ClassVar[str] = 'basic'
    external: ClassVar[bool] = True

    question: str = ''
    options: list = []
    session_id: str = ''

    async def __call__(self) -> dict:
        park_cmd = app.resolve_ref(
            app.session_ref, 'Park',
            session_id=self.session_id,
            turn_id='',
            kind='question',
            pending={'question': self.question, 'options': self.options},
        )
        await hub.dispatch(park_cmd)
        park_id = await park_cmd.state
        event = app.event(
            'ReplyParked',
            session_id=self.session_id,
            turn_id='',
            agent=app.alias,
            park_id=park_id,
            kind='question',
        )
        await hub.emit(topic=type(event).destination, source=event)
        return {'status': 'parked', 'park_id': park_id}


class PresentFiles(BaseCommand):
    """Present file artifacts to the user."""
    group: ClassVar[str] = 'basic'
    read_only: ClassVar[bool] = True

    refs: list = []
    title: str = ''

    async def __call__(self) -> dict:
        return {'status': 'ok', 'title': self.title, 'refs': self.refs}
