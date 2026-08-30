"""McpService — external tool server integration."""

from __future__ import annotations

import inspect
from typing import ClassVar

from bollydog.globals import registry
from bollydog.models.base import BaseCommand

from a2.kernel import A2Service


class McpService(A2Service):
    domain = 'mcp'
    commands = ['commands']
    emits: ClassVar[list[str]] = ['ServerConnected', 'ServerLost']

    servers: dict = {}
    _connected: dict = {}

    def tool_name(self, server: str, name: str) -> str:
        return f'mcp__{server}__{name}'

    def register_tools(self, server: str, tools: list) -> int:
        count = 0
        for tool in tools:
            name = tool.get('name', '')
            dest = f'mcp.gateway.{self.tool_name(server, name)}'
            annotations = {'args': dict}
            defaults = {'args': {}}

            async def _caller(self_cmd, srv=server, tname=name):
                async for chunk in app.protocol.call_tool(tname, self_cmd.args):
                    yield chunk

            cls = type(
                self.tool_name(server, name),
                (BaseCommand,),
                {
                    'destination': dest,
                    'alias': self.tool_name(server, name),
                    '__doc__': tool.get('description', ''),
                    '__annotations__': annotations,
                    '__call__': _caller,
                    'group': 'mcp',
                    **defaults,
                },
            )
            registry.commands[dest] = cls
            count += 1
        return count

    def unregister_tools(self, server: str) -> int:
        prefix = f'mcp.gateway.mcp__{server}__'
        to_remove = [d for d in registry.commands if d.startswith(prefix.replace('mcp.gateway.', ''))]
        for dest in list(registry.commands):
            if f'mcp__{server}__' in dest:
                del registry.commands[dest]
        return len(to_remove)
