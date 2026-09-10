"""SessionService — cross-turn persistence."""

from __future__ import annotations

import time

from a2.kernel import A2Service


class SessionService(A2Service):
    domain = 'session'
    commands = ['commands']
    event_retention: int = 2000

    def new_record(self, session_id: str, user_id: str, agent: str) -> dict:
        now = time.time()
        return {
            'session_id': session_id,
            'user_id': user_id,
            'agent': agent,
            'title': '',
            'turn_count': 0,
            'created_at': now,
            'updated_at': now,
        }

    def session_key(self, session_id: str) -> str:
        return f'session:{session_id}'

    def turn_key(self, session_id: str, turn_id: str) -> str:
        return f'turn:{session_id}:{turn_id}'

    def park_key(self, session_id: str) -> str:
        return f'park:{session_id}'

    def event_key(self, session_id: str, seq: int) -> str:
        return f'evt:{session_id}:{seq}'

    def context_from_turns(self, turns: list) -> list:
        messages = []
        for turn in turns:
            for msg in turn.get('inputs', []):
                messages.append(msg)
            output = turn.get('output', {})
            if output:
                messages.append(output)
        return messages

    def title_of(self, inputs: list) -> str:
        for msg in inputs:
            content = msg.get('content', '') if isinstance(msg, dict) else str(msg)
            if isinstance(content, list):
                for block in content:
                    if block.get('type') == 'text':
                        return block.get('text', '')[:80]
            elif content:
                return str(content)[:80]
        return 'New conversation'
