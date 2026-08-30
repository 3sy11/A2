"""Command reference helpers — always construct via registry.resolve (D-05)."""

from __future__ import annotations

from bollydog.globals import registry
from bollydog.models.base import BaseCommand


def ref(destination: str, **fields) -> BaseCommand:
    """Construct a command/event by full destination."""
    return registry.resolve(destination)(**fields)


def R(destination: str):
    """Partial application helper: R('model.chat.Generate')(**kw)."""
    cls = registry.resolve(destination)

    def _factory(**fields):
        return cls(**fields)

    _factory.__doc__ = f'Factory for {destination}'
    _factory.destination = destination
    return _factory
