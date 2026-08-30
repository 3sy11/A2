"""MemoryService — long-term memory."""

from __future__ import annotations

import time
import uuid
from typing import ClassVar

from bollydog.models.base import BaseCommand

from a2.kernel import A2Service, safe_subscriber


class MemoryService(A2Service):
    domain = 'memory'
    commands = ['commands']
    subscribers: ClassVar[dict] = {
        'agent.*.ReplyFinished': 'on_reply_finished',
    }
    embed_ref: str = 'model.embed'

    def extract_facts(self, turn: dict) -> list:
        facts = []
        content = turn.get('content', [])
        for block in content:
            if block.get('type') == 'text':
                text = block.get('text', '')
                if 'prefer' in text.lower() or 'remember' in text.lower():
                    facts.append({'text': text[:500], 'kind': 'preference'})
        return facts

    def score(self, entry: dict, query: str) -> float:
        query_words = set(query.lower().split())
        text_words = set(entry.get('text', '').lower().split())
        if not query_words:
            return 0.0
        return len(query_words & text_words) / len(query_words)

    @safe_subscriber
    async def on_reply_finished(self, message: BaseCommand) -> dict:
        src = self.source_of(message)
        facts = self.extract_facts(src)
        for fact in facts:
            remember = self.resolve_ref('memory.longterm', 'Remember', **{
                'scope': 'user',
                'subject': src.get('session_id', ''),
                'text': fact['text'],
                'kind': fact.get('kind', 'fact'),
            })
            from bollydog.globals import hub
            await hub.dispatch(remember)
        return {'ok': True, 'facts': len(facts)}
