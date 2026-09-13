"""Agent tools — HITL and delivery."""

from __future__ import annotations

from typing import ClassVar

from bollydog.globals import app
from bollydog.models.base import BaseCommand

from a2.kernel import relay


class AskHuman(BaseCommand):
    """Ask the user a question and park the turn."""
    group: ClassVar[str] = 'basic'
    external: ClassVar[bool] = True
    concurrency_safe: ClassVar[bool] = False

    question: str = ''
    options: list = []
    session_id: str = ''

    async def __call__(self) -> dict:
        park_cmd = app.resolve_ref(
            app.session_ref, 'Park',
            session_id=self.session_id,
            turn_id=self.data.get('turn_id', ''),
            kind='question',
            pending={
                'iteration': self.data.get('iteration', 0),
                'messages': self.data.get('messages', []),
                'question': self.question,
                'options': self.options,
            },
        )
        park_id = await relay(park_cmd)
        return {
            'status': 'parked',
            'kind': 'question',
            'park_id': park_id,
            'question': self.question,
            'options': self.options,
        }


class PresentFiles(BaseCommand):
    """Present file artifacts to the user."""
    group: ClassVar[str] = 'basic'
    read_only: ClassVar[bool] = True

    refs: list = []
    title: str = ''

    async def __call__(self) -> dict:
        return {'status': 'ok', 'title': self.title, 'refs': self.refs}
