"""Memory domain commands."""

from __future__ import annotations

import time
import uuid

from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand, BaseEvent


class Recall(BaseCommand):
    """Recall long-term memory entries."""

    scope: str = 'user'
    subject: str = ''
    query: str = ''
    top_k: int = 5

    async def __call__(self) -> list:
        prefix = f'memory:{self.scope}:{self.subject}:'
        keys = await app.protocol.keys(prefix) if hasattr(app.protocol, 'keys') else []
        entries = []
        for key in keys:
            entry = await app.protocol.get(key)
            if entry:
                entry['score'] = app.score(entry, self.query)
                entries.append(entry)
        entries.sort(key=lambda x: x.get('score', 0), reverse=True)
        return entries[: self.top_k]


class Remember(BaseCommand):
    """Store a long-term memory entry."""

    scope: str = 'user'
    subject: str = ''
    text: str = ''
    kind: str = 'fact'

    async def __call__(self) -> str:
        entry_id = uuid.uuid4().hex[:12]
        entry = {
            'entry_id': entry_id,
            'scope': self.scope,
            'subject': self.subject,
            'text': self.text,
            'kind': self.kind,
            'created_at': time.time(),
            'hits': 0,
        }
        key = f'memory:{self.scope}:{self.subject}:{entry_id}'
        await app.protocol.set(key, entry)
        return entry_id


class Forget(BaseCommand):
    """Delete a memory entry."""

    scope: str = 'user'
    subject: str = ''
    entry_id: str = ''

    async def __call__(self) -> int:
        key = f'memory:{self.scope}:{self.subject}:{self.entry_id}'
        await app.protocol.remove(key)
        return 1


class OnReplyFinished(BaseEvent):
    """Extract long-term memories from a completed agent turn."""

    async def __call__(self) -> dict:
        source = self.data.get('events', [{}])[-1]
        facts = app.extract_facts(source)
        for fact in facts:
            remember = app.resolve_ref(
                'memory.longterm',
                'Remember',
                scope='user',
                subject=source.get('session_id', ''),
                text=fact['text'],
                kind=fact.get('kind', 'fact'),
            )
            await hub.dispatch(remember)
        return {'ok': True, 'facts': len(facts)}
