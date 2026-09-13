"""Commands owned by embedding-model services only."""

from __future__ import annotations

from bollydog.globals import app
from bollydog.models.base import BaseCommand


class Embed(BaseCommand):
    """Batch text embedding."""

    texts: list = []
    model: str = ''

    async def __call__(self) -> list:
        return await app.protocol.embed(self.texts, self.model or app.model)
