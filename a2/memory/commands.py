"""Memory domain commands."""

from __future__ import annotations

import time
import uuid

from bollydog.globals import app
from bollydog.models.base import BaseCommand


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
