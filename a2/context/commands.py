"""Context domain commands."""

from __future__ import annotations

import time

from bollydog.globals import app, hub, session
from bollydog.models.base import BaseCommand, BaseEvent


async def _ctx_history(session_id: str) -> list:
    data = await session.get(f'context:{session_id}')
    return data.get('items', []) if isinstance(data, dict) else []


async def _ctx_set(session_id: str, items: list) -> None:
    await session.set(f'context:{session_id}', {'items': items})


class Assemble(BaseCommand):
    """Assemble messages for model input within token budget."""

    session_id: str = ''
    agent: str = ''
    inputs: list = []
    system_prompt: str = ''
    tool_names: list = []
    budget: int = 0
    rag_query: str = ''

    async def __call__(self) -> dict:
        svc = app
        history = await _ctx_history(self.session_id)
        summary_data = await session.get(f'summary:{self.session_id}')
        summary = summary_data.get('text', '') if isinstance(summary_data, dict) else ''

        hints = [svc.render_hint({'time': time.strftime('%Y-%m-%d %H:%M'), 'tokens': 0})]

        parts = {
            'system_prompt': self.system_prompt,
            'summary': summary,
            'hints': [h for h in hints if h],
            'rag_hits': [],
            'history': history,
            'inputs': self.inputs,
        }
        messages = svc.assemble(parts)
        tokens = svc.estimate(messages)
        return {
            'messages': messages,
            'tokens': tokens,
            'need_compress': svc.need_compress(tokens),
            'injected': parts.get('hints', []),
        }


class AppendContext(BaseCommand):
    """Append messages to in-turn context."""

    session_id: str = ''
    messages: list = []

    async def __call__(self) -> int:
        history = await _ctx_history(self.session_id)
        history.extend(self.messages)
        await _ctx_set(self.session_id, history)
        return len(history)


class Compress(BaseCommand):
    """Compress conversation context via summarization."""

    session_id: str = ''
    agent: str = ''
    keep_ratio: float = 0.0
    model_ref: str = 'model.chat'

    async def __call__(self) -> dict:
        svc = app
        history = await _ctx_history(self.session_id)
        ratio = self.keep_ratio or svc.keep_ratio
        old, kept = svc.split_window(history, ratio)
        before_tokens = svc.estimate(history)

        summary_text = ' '.join(str(m.get('content', ''))[:200] for m in old)[:2000]

        summary_data = await session.get(f'summary:{self.session_id}')
        prev = summary_data.get('text', '') if isinstance(summary_data, dict) else ''
        await session.set(
            f'summary:{self.session_id}',
            {'text': (prev + '\n' + summary_text).strip()},
        )
        await _ctx_set(self.session_id, kept)

        after_tokens = svc.estimate(kept)
        folded = len(old)
        event = svc.event(
            'ContextCompacted',
            session_id=self.session_id,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            folded=folded,
        )
        await hub.emit(topic=type(event).destination, source=event)
        return {
            'before_tokens': before_tokens,
            'after_tokens': after_tokens,
            'folded': folded,
        }


class ContextCompacted(BaseEvent):
    session_id: str = ''
    before_tokens: int = 0
    after_tokens: int = 0
    folded: int = 0
