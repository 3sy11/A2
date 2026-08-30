"""MCP domain commands."""

from __future__ import annotations

from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand, BaseEvent


class ConnectServer(BaseCommand):
    """Connect to an MCP server and register its tools."""

    server: str = ''
    transport: str = 'stdio'
    command: str = ''
    args: list = []
    url: str = ''
    env: dict = {}
    headers: dict = {}

    async def __call__(self) -> dict:
        tools = await app.protocol.list_tools()
        count = app.register_tools(self.server, tools)
        app._connected[self.server] = {'transport': self.transport, 'tools': count}
        await hub.emit(app.event('ServerConnected', server=self.server, tool_count=count, transport=self.transport))
        return {'server': self.server, 'tools': count}


class DisconnectServer(BaseCommand):
    """Disconnect MCP server."""

    server: str = ''

    async def __call__(self) -> dict:
        count = app.unregister_tools(self.server)
        app._connected.pop(self.server, None)
        return {'server': self.server, 'removed': count}


class ListServers(BaseCommand):
    """List connected MCP servers."""

    async def __call__(self) -> list:
        return [
            {'server': name, **info}
            for name, info in app._connected.items()
        ]


class ServerConnected(BaseEvent):
    server: str = ''
    tool_count: int = 0
    transport: str = ''


class ServerLost(BaseEvent):
    server: str = ''
    error: str = ''
