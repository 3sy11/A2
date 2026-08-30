"""ChatModelService — LLM provider abstraction."""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from a2.kernel import A2Service

logger = logging.getLogger(__name__)

_BASE_COMMAND_FIELDS = {
    'created_time', 'update_time', 'iid', 'sign', 'created_by',
    'expire_time', 'delivery_count', 'state', 'trace_id', 'span_id',
    'parent_span_id', 'data',
}


class ChatModelService(A2Service):
    domain = 'model'
    commands = ['commands']
    emits: ClassVar[list[str]] = ['ModelCalled', 'ModelFailed']

    provider: str = 'scripted'
    model: str = 'gpt-4o'
    fallback_models: list = []
    max_retries: int = 2
    context_size: int = 128000
    formatter: str = 'chat'
    params: dict = {}

    def format(self, messages: list, mode: str = '') -> list:
        return messages

    def merge_deltas(self, deltas: list) -> dict:
        text_parts: list[str] = []
        tool_calls: dict[str, dict] = {}
        for delta in deltas:
            payload = delta.get('payload', delta)
            block = payload.get('block', 'text')
            if block == 'text':
                text_parts.append(payload.get('text', ''))
            elif block == 'tool_call':
                idx = payload.get('index', 0)
                tc = tool_calls.setdefault(
                    str(idx),
                    {'type': 'tool_call', 'id': '', 'name': '', 'input': {}},
                )
                if 'id' in payload:
                    tc['id'] = payload['id']
                if 'name' in payload:
                    tc['name'] = payload['name']
                if 'input' in payload:
                    tc['input'].update(payload['input'])
        content = []
        if text_parts:
            content.append({'type': 'text', 'text': ''.join(text_parts)})
        content.extend(tool_calls.values())
        finish_reason = 'tool_calls' if tool_calls else 'stop'
        return {'content': content, 'finish_reason': finish_reason, 'usage': {}}

    def fallbacks(self) -> list:
        return [self.model, *self.fallback_models]

    def backoff(self, attempt: int) -> float:
        return min(2 ** attempt, 8.0)

    def estimate_tokens(self, messages: list) -> int:
        return sum(max(1, len(str(m.get('content', ''))) // 4) for m in messages)


class EmbeddingService(A2Service):
    domain = 'model'
    commands = ['commands']
    alias = 'embed'

    model: str = 'text-embedding-3-small'
    batch_size: int = 64
    dimension: int = 1536

    def batches(self, texts: list) -> list:
        return [
            texts[i : i + self.batch_size]
            for i in range(0, len(texts), self.batch_size)
        ]

    def cache_key(self, text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode()).hexdigest()[:16]
