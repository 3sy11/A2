"""MemoryService — long-term memory."""

from __future__ import annotations

from a2.kernel import A2Service


class MemoryService(A2Service):
    domain = 'memory'
    commands = ['commands']
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
