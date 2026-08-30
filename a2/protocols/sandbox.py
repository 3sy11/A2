"""Sandbox execution protocol ABC."""

from __future__ import annotations

from abc import abstractmethod
from typing import AsyncIterator

from bollydog.models.protocol import Protocol


class SandboxProtocol(Protocol, abstract=True):
    @abstractmethod
    async def exec_stream(
        self, command: str, cwd: str, env: dict, timeout: int
    ) -> AsyncIterator[dict]:
        ...

    @abstractmethod
    async def put(self, path: str, content: bytes) -> None:
        ...

    @abstractmethod
    async def fetch(self, path: str) -> bytes:
        ...

    async def list_changed(self, since: float) -> list:
        return []
