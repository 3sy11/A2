"""TraceService — observability."""

from __future__ import annotations

import time
from typing import ClassVar

from bollydog.models.base import BaseCommand

from a2.kernel import A2Service, safe_subscriber


class TraceService(A2Service):
    domain = 'observe'
    commands = ['commands']
    subscribers: ClassVar[dict] = {'#': 'on_any'}
    sample_rate: float = 1.0

    def to_span(self, evt: BaseCommand) -> dict:
        src = self.source_of(evt) if hasattr(self, 'source_of') else {}
        return {
            'trace_id': getattr(evt, 'trace_id', src.get('trace_id', '')),
            'span_id': getattr(evt, 'iid', ''),
            'name': type(evt).__name__,
            'destination': type(evt).destination or '',
            'ts': time.time(),
            'status': 'ok',
            'attrs': src or evt.model_dump(),
        }

    def tree(self, rows: list) -> list:
        by_parent: dict = {}
        for row in rows:
            parent = row.get('parent_span_id', '--')
            by_parent.setdefault(parent, []).append(row)
        return by_parent.get('--', rows)

    @safe_subscriber
    async def on_any(self, message: BaseCommand) -> dict:
        src = self.source_of(message)
        span = {
            'trace_id': src.get('trace_id', getattr(message, 'trace_id', '')),
            'span_id': message.iid,
            'name': type(message).__name__,
            'destination': getattr(type(message), 'destination', '') or '',
            'ts': time.time(),
            'attrs': src,
        }
        key = f'span:{span["trace_id"]}:{span["span_id"]}'
        if self.protocol:
            await self.protocol.set(key, span)
        return {'ok': True}
