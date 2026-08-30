"""Permission protocol ABC."""

from __future__ import annotations

from abc import abstractmethod

from bollydog.models.protocol import Protocol


class PermissionProtocol(Protocol, abstract=True):
    @abstractmethod
    async def match(self, tool: str, args: dict, ctx: dict) -> dict:
        ...

    async def add_rule(self, rule: dict) -> int:
        return 0

    async def rules(self) -> list:
        return []
