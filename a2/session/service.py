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
        return f'evt:{session_id}:{seq:08d}'

    def context_from_turns(self, turns: list) -> list:
        """Rebuild model context from completed conversation turns."""
        messages = []
        for turn in sorted(turns, key=lambda item: item.get('created_at', 0)):
            for msg in turn.get('inputs', []):
                messages.append(msg)
            output = turn.get('output', [])
            if output:
                messages.append({
                    'role': 'assistant',
                    'name': turn.get('agent', 'assistant'),
                    'content': output,
                })
        return messages

    def pending_action_for_client(self, parked: dict) -> dict:
        """Return only the information a client needs to continue a parked turn."""
        pending = parked.get('pending', {})
        result = {
            'park_id': parked.get('park_id', ''),
            'turn_id': parked.get('turn_id', ''),
            'kind': parked.get('kind', ''),
            'created_at': parked.get('created_at', 0),
        }
        if result['kind'] == 'confirm':
            call = pending.get('tool_call', {})
            result.update({
                'tool': call.get('name', ''),
                'args': call.get('input', {}),
                'reason': pending.get('reason', ''),
            })
        elif result['kind'] == 'question':
            result.update({
                'question': pending.get('question', ''),
                'options': pending.get('options', []),
            })
        return result

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
