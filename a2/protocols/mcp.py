"""MCP protocol ABC."""

from __future__ import annotations

from abc import abstractmethod
from typing import AsyncIterator

from bollydog.models.protocol import Protocol


class McpProtocol(Protocol, abstract=True):
    @abstractmethod
    async def list_tools(self) -> list:
        ...

    @abstractmethod
    async def call_tool(self, name: str, args: dict) -> AsyncIterator[dict]:
        ...

    async def list_resources(self) -> list:
        return []
