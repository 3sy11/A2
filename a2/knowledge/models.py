"""Knowledge domain models."""

from bollydog.models.base import BaseDomain


class DocChunk(BaseDomain):
    chunk_id: str = ''
    doc_id: str = ''
    text: str = ''
    vector: list = []
    metadata: dict = {}


class Hit(BaseDomain):
    chunk_id: str = ''
    doc_id: str = ''
    text: str = ''
    score: float = 0.0
    source: str = ''
