"""Tool domain commands."""

from __future__ import annotations

import json
import time
from typing import ClassVar

from bollydog.globals import app, hub, registry, session
from bollydog.models.base import BaseCommand, BaseEvent

from a2.kernel import chunk, relay, relay_gen


class ListTools(BaseCommand):
    """List tool JSON schemas for active groups."""

    session_id: str = ''
    agent: str = ''
    groups: list = []

    async def __call__(self) -> list:
        active = self.groups
        if not active:
            key = f'tool_groups:{self.session_id}'
            data = await session.get(key)
            active = data.get('groups', app.always_on_groups) if isinstance(data, dict) else app.always_on_groups
        return app.schemas(active)


class Invoke(BaseCommand):
    """Execute a tool call, streaming output."""

    call_id: str = ''
    tool: str = ''
    args: dict = {}
    session_id: str = ''
    agent: str = ''

    async def __call__(self):
        svc = app
        start = time.time()
        try:
            if self.data.get('permission_action') == 'allow':
                perm = {'action': 'allow', 'reason': ''}
            else:
                perm = yield CheckPermission(
                    session_id=self.session_id,
                    agent=self.agent,
                    tool=self.tool,
                    args=self.args,
                )
            action = perm.get('action', 'allow')
            if action == 'deny':
                yield await chunk(
                    'tool.result',
                    session_id=self.session_id,
                    payload={
                        'id': self.call_id,
                        'status': 'denied',
                        'output': {'error': perm.get('reason', 'denied')},
                        'artifact': None,
                        'truncated': False,
                    },
                )
                return
            if action == 'ask':
                park_id = await _park_confirm(self)
                yield await chunk(
                    'tool.result',
                    session_id=self.session_id,
                    payload={
                        'id': self.call_id,
                        'status': 'parked',
                        'output': {'park_id': park_id},
                        'artifact': None,
                        'truncated': False,
                    },
                )
                return

            dest = svc.resolve_tool(self.tool)
            cmd = registry.resolve(dest)(**self.args)
            cmd.data.update({
                'session_id': self.session_id,
                'turn_id': self.data.get('turn_id', ''),
                'iteration': self.data.get('iteration', 0),
                'messages': self.data.get('messages', []),
            })
            if 'session_id' in type(cmd).model_fields:
                cmd.session_id = self.session_id

            if cmd.is_async_gen:
                async for item in relay_gen(cmd):
                    yield await chunk(
                        'tool.chunk',
                        session_id=self.session_id,
                        payload={'id': self.call_id, 'data': item},
                    )
                result = cmd.state.result()
            else:
                result = await relay(cmd)

            if isinstance(result, dict) and result.get('status') == 'parked':
                yield await chunk(
                    'tool.result',
                    session_id=self.session_id,
                    payload={
                        'id': self.call_id,
                        'status': 'parked',
                        'output': result,
                        'artifact': None,
                        'truncated': False,
                    },
                )
                return

            truncated_result, truncated = svc.truncate(
                result if isinstance(result, dict) else {'result': result}
            )
            ms = int((time.time() - start) * 1000)
            event = svc.event(
                'ToolInvoked',
                tool=self.tool,
                call_id=self.call_id,
                session_id=self.session_id,
                ok=True,
                ms=ms,
            )
            await hub.emit(topic=type(event).destination, source=event)
            yield await chunk(
                'tool.result',
                session_id=self.session_id,
                payload={
                    'id': self.call_id,
                    'status': 'ok',
                    'output': truncated_result,
                    'artifact': truncated_result.get('artifact'),
                    'truncated': truncated,
                },
            )
        except Exception as exc:
            ms = int((time.time() - start) * 1000)
            event = svc.event(
                'ToolFailed',
                tool=self.tool,
                call_id=self.call_id,
                session_id=self.session_id,
                error=str(exc),
            )
            await hub.emit(topic=type(event).destination, source=event)
            yield await chunk(
                'tool.result',
                session_id=self.session_id,
                payload={
                    'id': self.call_id,
                    'status': 'error',
                    'output': {'error': str(exc)},
                    'artifact': None,
                    'truncated': False,
                },
            )


class CheckPermission(BaseCommand):
    """Check permission for a tool invocation."""

    session_id: str = ''
    agent: str = ''
    tool: str = ''
    args: dict = {}

    async def __call__(self) -> dict:
        if app.protocol:
            return await app.protocol.match(
                self.tool, self.args, {'session_id': self.session_id, 'agent': self.agent}
            )
        return {'action': 'allow', 'reason': ''}


class ActivateGroup(BaseCommand):
    """Enable or disable tool groups."""

    session_id: str = ''
    enable: list = []
    disable: list = []

    async def __call__(self) -> dict:
        key = f'tool_groups:{self.session_id}'
        data = await session.get(key)
        active = data.get('groups', app.always_on_groups) if isinstance(data, dict) else app.always_on_groups
        new_active = app.apply_groups(active, self.enable, self.disable)
        await session.set(key, {'groups': new_active})
        event = app.event(
            'GroupActivated',
            session_id=self.session_id,
            groups=new_active,
        )
        await hub.emit(topic=type(event).destination, source=event)
        return {'groups': new_active}


class ToolInvoked(BaseEvent):
    tool: str = ''
    call_id: str = ''
    session_id: str = ''
    ok: bool = True
    ms: int = 0


class ToolFailed(BaseEvent):
    tool: str = ''
    call_id: str = ''
    session_id: str = ''
    error: str = ''


class GroupActivated(BaseEvent):
    session_id: str = ''
    groups: list = []


class OnServerConnected(BaseEvent):
    """Refresh the tool index after an MCP server connects."""

    async def __call__(self) -> dict:
        return {'ok': True, 'tools': app.reindex()}


class OnServerLost(BaseEvent):
    """Refresh the tool index after an MCP server disconnects."""

    async def __call__(self) -> dict:
        return {'ok': True, 'tools': app.reindex()}


async def _park_confirm(invoke: Invoke) -> str:
    from bollydog.globals import registry, hub
    park_cmd = registry.resolve('session.store.Park')(
        session_id=invoke.session_id,
        turn_id=invoke.data.get('turn_id', ''),
        kind='confirm',
        pending={
            'iteration': invoke.data.get('iteration', 0),
            'messages': invoke.data.get('messages', []),
            'tool_call': {
                'id': invoke.call_id,
                'name': invoke.tool,
                'input': invoke.args,
            },
            'reason': perm_reason(invoke),
        },
    )
    return await relay(park_cmd)


def perm_reason(invoke: Invoke) -> str:
    """Direct Invoke callers have no earlier permission result to retain."""
    return invoke.data.get('permission_reason', '')
