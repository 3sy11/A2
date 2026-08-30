"""Model domain commands."""

from __future__ import annotations

import asyncio
import time
from typing import ClassVar

from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand, BaseEvent

from a2.kernel import chunk


class Generate(BaseCommand):
    """Stream a chat model completion."""

    messages: list = []
    tools: list = []
    tool_choice: str = 'auto'
    stream: bool = True
    model: str = ''
    params: dict = {}

    async def __call__(self):
        svc = app
        models = svc.fallbacks() if hasattr(svc, 'fallbacks') else [svc.model]
        if self.model:
            models = [self.model, *[m for m in models if m != self.model]]

        last_error = ''
        for attempt, model_name in enumerate(models[: svc.max_retries + 1]):
            try:
                payload = {
                    'messages': svc.format(self.messages),
                    'tools': self.tools,
                    'tool_choice': self.tool_choice,
                    'params': {**svc.params, **self.params},
                }
                start = time.time()
                deltas = []
                async for item in svc.protocol.stream(payload, model_name):
                    if item.get('type') == 'model.completed':
                        result = item.get('payload', item)
                        usage = result.get('usage', {})
                        latency = int((time.time() - start) * 1000)
                        await hub.emit(
                            svc.event(
                                'ModelCalled',
                                model=model_name,
                                usage=usage,
                                latency_ms=latency,
                                finish_reason=result.get('finish_reason', 'stop'),
                            )
                        )
                        yield await chunk(
                            'model.completed',
                            payload={
                                'content': result.get('content', []),
                                'usage': usage,
                                'finish_reason': result.get('finish_reason', 'stop'),
                            },
                        )
                        return
                    deltas.append(item)
                    yield await chunk('model.delta', payload=item.get('payload', item))

                if deltas:
                    merged = svc.merge_deltas(deltas)
                    usage = merged.get('usage', {})
                    latency = int((time.time() - start) * 1000)
                    await hub.emit(
                        svc.event(
                            'ModelCalled',
                            model=model_name,
                            usage=usage,
                            latency_ms=latency,
                            finish_reason=merged.get('finish_reason', 'stop'),
                        )
                    )
                    yield await chunk('model.completed', payload=merged)
                    return
            except Exception as exc:
                last_error = str(exc)
                yield await chunk(
                    'model.retry',
                    payload={'model': model_name, 'attempt': attempt + 1, 'error': last_error},
                )
                if attempt < len(models) - 1:
                    await asyncio.sleep(svc.backoff(attempt))

        await hub.emit(
            svc.event('ModelFailed', model=models[0], error=last_error, attempts=len(models))
        )
        yield await chunk(
            'model.failed',
            payload={'error': last_error, 'attempts': len(models)},
        )


class CountTokens(BaseCommand):
    """Estimate token count for messages."""

    messages: list = []
    model: str = ''

    async def __call__(self) -> int:
        return await app.protocol.count_tokens(self.messages, self.model or app.model)


class Embed(BaseCommand):
    """Batch text embedding."""

    texts: list = []
    model: str = ''

    async def __call__(self) -> list:
        return await app.protocol.embed(self.texts, self.model or app.model)


class ModelCalled(BaseEvent):
    model: str = ''
    usage: dict = {}
    latency_ms: int = 0
    finish_reason: str = ''


class ModelFailed(BaseEvent):
    model: str = ''
    error: str = ''
    attempts: int = 0
