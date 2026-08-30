"""ToolkitService — tools are Commands (D-A)."""

from __future__ import annotations

import logging
from typing import ClassVar

from bollydog.globals import hub, registry
from bollydog.models.base import BaseCommand

from a2.kernel import A2Service, safe_subscriber
from a2.tool.models import ToolSpec

logger = logging.getLogger(__name__)

_BASE_FIELDS = {
    'created_time', 'update_time', 'iid', 'sign', 'created_by',
    'expire_time', 'delivery_count', 'state', 'trace_id', 'span_id',
    'parent_span_id', 'data', 'host', 'version', 'module', 'alias', 'destination',
}


class ToolkitService(A2Service):
    domain = 'tool'
    commands = ['commands']
    emits: ClassVar[list[str]] = ['ToolInvoked', 'ToolFailed', 'GroupActivated']
    subscribers: ClassVar[dict] = {
        'mcp.*.ServerConnected': 'on_server_connected',
        'mcp.*.ServerLost': 'on_server_lost',
    }

    groups: dict = {}
    always_on_groups: list = ['basic']
    spill_threshold: int = 20000
    rules: list = []

    _name_index: dict = {}

    def reindex(self) -> int:
        self._name_index.clear()
        for dest, cls in registry.commands.items():
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
        cls = registry.commands.get(destination)
        if not cls or not issubclass(cls, BaseCommand):
            return None
        props = {}
        required = []
        for name, field in cls.model_fields.items():
            if name in _BASE_FIELDS:
                continue
            props[name] = {'type': self._json_type(field.annotation)}
            if field.is_required():
                required.append(name)
        params = {'type': 'object', 'properties': props, 'required': required}
        return ToolSpec(
            name=getattr(cls, 'alias', cls.__name__),
            destination=destination,
            description=(cls.__doc__ or '').strip(),
            parameters=params,
            group=getattr(cls, 'group', 'basic'),
            read_only=getattr(cls, 'read_only', False),
            concurrency_safe=getattr(cls, 'concurrency_safe', True),
            external=getattr(cls, 'external', False),
        )

    @staticmethod
    def _json_type(annotation) -> str:
        if annotation in (int, 'int'):
            return 'integer'
        if annotation in (float, 'float'):
            return 'number'
        if annotation in (bool, 'bool'):
            return 'boolean'
        if getattr(annotation, '__origin__', None) is list:
            return 'array'
        if getattr(annotation, '__origin__', None) is dict:
            return 'object'
        return 'string'

    def resolve_tool(self, name: str) -> str:
        if name in self._name_index:
            return self._name_index[name]
        for dest in registry.commands:
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

    @safe_subscriber
    async def on_server_connected(self, message: BaseCommand) -> dict:
        self.reindex()
        return {'ok': True}

    @safe_subscriber
    async def on_server_lost(self, message: BaseCommand) -> dict:
        self.reindex()
        return {'ok': True}

    async def _guard(self, message: BaseCommand):
        turn = message.data.get('session_id', '')
        if not turn:
            return None
        from bollydog.globals import session
        state = await session.get(f'turn:{turn}')
        if state.get('interrupted'):
            return {'status': 'interrupted', 'error': 'turn interrupted'}
        return None
