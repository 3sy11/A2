"""A2Service — base class for all A2 domain services."""

from __future__ import annotations

from typing import ClassVar

from bollydog.globals import registry
from bollydog.models.base import BaseCommand
from bollydog.models.service import AppService


class A2Service(AppService, abstract=True):
    """A2 domain service base.

    - Injects TOML kwargs into declared class attributes (D-06).
    - Provides bound event construction via registry.resolve (D-05).
    - Provides source_of helper for subscriber callbacks (D-11).
    """

    emits: ClassVar[list[str]] = []

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(type(self), key):
                setattr(self, key, value)
        super().__init__(**kwargs)

    def event(self, name: str, **fields) -> BaseCommand:
        dest = f'{self.domain}.{self.alias}.{name}'
        cls = registry.resolve(dest)
        return cls(**fields)

    @staticmethod
    def source_of(message: BaseCommand) -> dict:
        src = getattr(message, '_source', None)
        return src.model_dump() if src is not None else {}

    async def on_started(self) -> None:
        await super().on_started()
        for name in self.emits:
            dest = f'{self.domain}.{self.alias}.{name}'
            if dest not in registry.commands:
                raise RuntimeError(
                    f'{self.domain}.{self.alias} declares emit {name!r} '
                    f'but {dest!r} is not registered'
                )

    def resolve_ref(self, service_ref: str, command: str, **fields) -> BaseCommand:
        """Resolve a configured service reference to a Command instance."""
        return registry.resolve(f'{service_ref}.{command}')(**fields)
