"""Memory domain models."""

from bollydog.models.base import BaseDomain


class MemoryEntry(BaseDomain):
    entry_id: str = ''
    scope: str = 'user'
    subject: str = ''
    text: str = ''
    kind: str = 'fact'
    vector: list = []
    created_at: float = 0.0
    hits: int = 0
