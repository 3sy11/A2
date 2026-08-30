"""Vector store protocol ABC."""

from __future__ import annotations

from abc import abstractmethod

from bollydog.models.protocol import Protocol


class VectorStoreProtocol(Protocol, abstract=True):
    @abstractmethod
    async def upsert(self, collection: str, items: list) -> int:
        ...

    @abstractmethod
    async def search(
        self, collection: str, vector: list, top_k: int, flt: dict | None = None
    ) -> list:
        ...

    @abstractmethod
    async def delete(self, collection: str, doc_id: str) -> int:
        ...

    async def collections(self) -> list:
        return []
