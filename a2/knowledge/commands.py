"""Knowledge domain commands."""

from __future__ import annotations

import uuid

from bollydog.globals import app, hub, registry
from bollydog.models.base import BaseCommand, BaseEvent

from a2.kernel import chunk


class IngestDocument(BaseCommand):
    """Parse, chunk, embed and store a document."""

    doc_id: str = ''
    path: str = ''
    collection: str = 'default'
    chunk_size: int = 0
    overlap: int = 0

    async def __call__(self):
        doc_id = self.doc_id or uuid.uuid4().hex[:12]
        sections = app.parse(self.path)
        yield await chunk('ingest.parsed', payload={'sections': len(sections)})
        chunks = app.chunk(sections, self.chunk_size or None, self.overlap or None)
        total = len(chunks)
        for i, batch in enumerate(app.batches(chunks)):
            texts = [c['text'] for c in batch]
            embed_cmd = registry.resolve('model.embed.Embed')(texts=texts, model='')
            vectors = yield embed_cmd
            items = []
            for c, vec in zip(batch, vectors):
                items.append({
                    'chunk_id': c['chunk_id'],
                    'doc_id': doc_id,
                    'text': c['text'],
                    'vector': vec,
                    'metadata': c.get('metadata', {}),
                })
            yield UpsertChunks(collection=self.collection, items=items)
            yield await chunk('ingest.progress', payload={'done': min((i + 1) * 32, total), 'total': total})
        await hub.emit(app.event('DocumentIngested', doc_id=doc_id, chunks=total, collection=self.collection))
        yield await chunk('ingest.completed', payload={'doc_id': doc_id, 'chunks': total})


class UpsertChunks(BaseCommand):
    """Upsert document chunks into vector store."""

    collection: str = 'default'
    items: list = []

    async def __call__(self) -> int:
        return await app.protocol.upsert(self.collection, self.items)


class Search(BaseCommand):
    """Similarity search in knowledge base."""

    collection: str = 'default'
    query: str = ''
    top_k: int = 5
    score_threshold: float = 0.0

    async def __call__(self) -> list:
        from bollydog.globals import hub
        embed_cmd = registry.resolve('model.embed.Embed')(texts=[self.query], model='')
        await hub.dispatch(embed_cmd)
        vectors = await embed_cmd.state
        hits = await app.protocol.search(self.collection, vectors[0], self.top_k)
        return [h for h in hits if h.get('score', 0) >= self.score_threshold]


class DeleteDocument(BaseCommand):
    """Delete document from knowledge base."""

    collection: str = 'default'
    doc_id: str = ''

    async def __call__(self) -> int:
        return await app.protocol.delete(self.collection, self.doc_id)


class DocumentIngested(BaseEvent):
    doc_id: str = ''
    chunks: int = 0
    collection: str = ''
