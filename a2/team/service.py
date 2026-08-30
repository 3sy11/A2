"""TeamService — multi-agent collaboration."""

from __future__ import annotations

from typing import ClassVar

from a2.kernel import A2Service


class TeamService(A2Service):
    domain = 'team'
    commands = ['commands']
    emits: ClassVar[list[str]] = ['MessageBroadcast', 'RoundCompleted']

    async def members(self, topic: str) -> list:
        return await self.protocol.get(f'topic:{topic}') or []

    def to_inputs(self, result: list) -> list:
        return [{'role': 'user', 'content': str(r)} for r in result]

    def zip(self, agents: list, results: list) -> list:
        return [{'agent': a, 'result': r} for a, r in zip(agents, results)]
