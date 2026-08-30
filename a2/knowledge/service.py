"""KnowledgeService — RAG."""

from __future__ import annotations

from typing import ClassVar

from a2.kernel import A2Service


class KnowledgeService(A2Service):
    domain = 'knowledge'
    commands = ['commands']
    emits: ClassVar[list[str]] = ['DocumentIngested']

    embed_ref: str = 'model.embed'
    chunk_size: int = 800
    overlap: int = 100

    def parse(self, path: str) -> list:
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read()
        return [{'text': text, 'title': path}]

    def chunk(self, sections: list, size: int | None = None, overlap: int | None = None) -> list:
        size = size or self.chunk_size
        overlap = overlap or self.overlap
        chunks = []
        for section in sections:
            text = section.get('text', '')
            start = 0
            i = 0
            while start < len(text):
                end = start + size
                chunks.append({
                    'text': text[start:end],
                    'metadata': {'title': section.get('title', ''), 'offset': start},
                    'chunk_id': f'chunk_{i}',
                })
                start += size - overlap
                i += 1
        return chunks

    def batches(self, items: list) -> list:
        batch_size = 32
        return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]

    def render_hits(self, hits: list) -> str:
        lines = []
        for hit in hits:
            lines.append(f'[{hit.get("source", hit.get("doc_id", ""))}] {hit.get("text", "")[:300]}')
        return '\n'.join(lines)
