"""Observe domain commands."""

from __future__ import annotations

from bollydog.globals import app
from bollydog.models.base import BaseCommand


class QueryTrace(BaseCommand):
    """Query trace spans by trace_id."""

    trace_id: str = ''

    async def __call__(self) -> dict:
        prefix = f'span:{self.trace_id}:'
        keys = await app.protocol.keys(prefix) if hasattr(app.protocol, 'keys') else []
        rows = []
        for key in keys:
            span = await app.protocol.get(key)
            if span:
                rows.append(span)
        return {'trace_id': self.trace_id, 'spans': app.tree(rows)}


class ExportTrace(BaseCommand):
    """Export trace as replay case."""

    trace_id: str = ''
    fmt: str = 'json'

    async def __call__(self) -> dict:
        from bollydog.globals import hub
        cmd = QueryTrace(trace_id=self.trace_id)
        await hub.dispatch(cmd)
        trace = await cmd.state
        return {'format': self.fmt, 'case': trace}


class ListTraces(BaseCommand):
    """List traces for a session."""

    session_id: str = ''
    limit: int = 20
    offset: int = 0

    async def __call__(self) -> list:
        keys = await app.protocol.keys('span:') if hasattr(app.protocol, 'keys') else []
        traces = []
        seen = set()
        for key in keys:
            span = await app.protocol.get(key)
            if span:
                tid = span.get('trace_id', '')
                if tid not in seen:
                    seen.add(tid)
                    traces.append({'trace_id': tid, 'spans': 1})
        return traces[self.offset : self.offset + self.limit]
