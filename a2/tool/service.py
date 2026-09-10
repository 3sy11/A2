"""ToolkitService — tools are Commands (D-A)."""

from __future__ import annotations

import logging

from bollydog.globals import registry
from bollydog.models.base import BaseCommand

from a2.kernel import A2Service
from a2.tool.models import ToolSpec

logger = logging.getLogger(__name__)

class ToolkitService(A2Service):
    domain = 'tool'
    commands = ['commands']

    groups: dict = {}
    always_on_groups: list = ['basic']
    spill_threshold: int = 20000
    rules: list = []

    _name_index: dict = {}

    def reindex(self) -> int:
        self._name_index.clear()
        for dest, cls in registry.all_commands().items():
            alias = getattr(cls, 'alias', cls.__name__)
            group = getattr(cls, 'group', None) or self._group_of(dest)
            if group:
                cls.group = group  # type: ignore[attr-defined]
            self._name_index[alias] = dest
            self._name_index[dest.split('.')[-1]] = dest
        return len(self._name_index)

    def _group_of(self, dest: str) -> str:
        for group, tools in self.groups.items():
            if dest in tools:
                return group
        return 'basic'

    def schemas(self, groups: list | None = None) -> list:
        active = groups or self.always_on_groups
        dests = set()
        for group in active:
            dests.update(self.groups.get(group, []))
        result = []
        for dest in dests:
            spec = self.spec_of(dest)
            if spec:
                result.append(spec.model_dump())
        return result

    def spec_of(self, destination: str) -> ToolSpec | None:
        cls = registry.all_commands().get(destination)
        if not cls or not issubclass(cls, BaseCommand):
            return None
        description = cls.describe()
        params = {
            'type': 'object',
            'properties': description['parameters'],
            'required': description['required'],
        }
        return ToolSpec(
            name=description['name'],
            destination=destination,
            description=description['description'],
            parameters=params,
            group=getattr(cls, 'group', 'basic'),
            read_only=getattr(cls, 'read_only', False),
            concurrency_safe=getattr(cls, 'concurrency_safe', True),
            external=getattr(cls, 'external', False),
        )

    def resolve_tool(self, name: str) -> str:
        if name in self._name_index:
            return self._name_index[name]
        for dest in registry.all_commands():
            if dest.endswith(f'.{name}'):
                return dest
        raise KeyError(f'tool not found: {name}')

    def is_concurrency_safe(self, name: str) -> bool:
        dest = self.resolve_tool(name)
        spec = self.spec_of(dest)
        return spec.concurrency_safe if spec else True

    def truncate(self, raw: dict) -> tuple:
        import json
        text = json.dumps(raw, ensure_ascii=False)
        if len(text) <= self.spill_threshold:
            return raw, False
        return {'summary': text[: self.spill_threshold], 'truncated': True}, True

    def apply_groups(self, active: list, enable: list, disable: list) -> list:
        result = set(active or self.always_on_groups)
        result.update(enable)
        result -= set(disable)
        return sorted(result)

    def dormant_groups(self, active: list) -> list:
        return [g for g in self.groups if g not in active]

    async def on_first_start(self) -> None:
        await super().on_first_start()
        from bollydog.globals import services
        runner = services.get('bollydog.HubService') or services.get('bollydog.ExecuteService')
        if runner and hasattr(runner, 'before'):
            runner.before(self._guard)
        self.reindex()

    async def _guard(self, message: BaseCommand):
        turn = message.data.get('session_id', '')
        if not turn:
            return None
        from bollydog.globals import session
        state = await session.get(f'turn:{turn}')
        if state.get('interrupted'):
            return {'status': 'interrupted', 'error': 'turn interrupted'}
        return None
