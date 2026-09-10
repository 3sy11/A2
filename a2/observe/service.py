"""TraceService — observability."""

from __future__ import annotations

import time

from a2.kernel import A2Service


class TraceService(A2Service):
    domain = 'observe'
    commands = ['commands']
    sample_rate: float = 1.0

    def to_span(self, source: dict) -> dict:
        data = source.get('data', {})
        return {
            'trace_id': source.get('trace_id', ''),
            'span_id': source.get('iid', ''),
            'parent_span_id': source.get('parent_span_id', '--'),
            'name': data.get('name', ''),
            'destination': data.get('topic', ''),
            'ts': time.time(),
            'status': 'ok',
            'attrs': source,
        }

    def tree(self, rows: list) -> list:
        by_parent: dict = {}
        for row in rows:
            parent = row.get('parent_span_id', '--')
            by_parent.setdefault(parent, []).append(row)
        return by_parent.get('--', rows)
