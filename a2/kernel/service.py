"""A2Service — base class for all A2 domain services."""

from __future__ import annotations

from bollydog.globals import registry, services
from bollydog.models.base import BaseCommand
from bollydog.models.service import AppService


class A2Service(AppService, abstract=True):
    """A2 domain service base.

    Provides configured command and event resolution helpers.
    """

    def event(self, name: str, **fields) -> BaseCommand:
        """Resolve an Event owned by this service."""
        dest = f'{self.domain}.{self.alias}.{name}'
        cls = services.exchange.resolve(dest)
        event = cls(**fields)
        event.data.update({'topic': dest, 'name': name})
        return event

    def resolve_ref(self, service_ref: str, command: str, **fields) -> BaseCommand:
        """Resolve a configured service reference to a Command instance."""
        return registry.resolve(f'{service_ref}.{command}')(**fields)
