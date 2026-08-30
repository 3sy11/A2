"""Chat model and embedding protocol ABCs."""

from __future__ import annotations

from abc import abstractmethod
from typing import AsyncIterator

from bollydog.models.protocol import Protocol


class ChatModelProtocol(Protocol, abstract=True):
    @abstractmethod
    async def stream(self, payload: dict, model: str) -> AsyncIterator[dict]:
        ...

    @abstractmethod
    async def complete(self, payload: dict, model: str) -> dict:
        ...

    async def count_tokens(self, messages: list, model: str) -> int:
        total = 0
        for msg in messages:
            content = msg.get('content') or ''
            total += max(1, len(str(content)) // 4)
        return total


class EmbeddingProtocol(Protocol, abstract=True):
    @abstractmethod
    async def embed(self, texts: list, model: str) -> list:
        ...


class TTSProtocol(Protocol, abstract=True):
    @abstractmethod
    async def synthesize(self, text: str, voice: str) -> dict:
        ...
