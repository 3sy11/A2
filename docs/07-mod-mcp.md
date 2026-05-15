# 模块：MCPService

> domain=`mcp` | 单一实例 | depends: [env.local]

---

## 架构

```
MCPService(AppService), domain="mcp", depends=["env.local"]
├── _servers: dict[str, MCPServerProtocol]
├── on_start → 连接所有服务器 → 收集工具
└── on_stop → 断开

MCPServerProtocol(Protocol) → adapter = mcp.ClientSession
├── on_start → stdio_client/sse_client → ClientSession.initialize()
├── list_tools() → tool 列表
└── call_tool(name, args) → 结果
```

> **设计变更**：MCPService 不再依赖 ToolService（已移除）。MCP 工具通过 `get_tool_schemas()` 直接提供给 ReActStepCommand 聚合。MCPService depends `env.local` 仅作为 MCP server 进程管理的备用环境。

## MCPServerProtocol

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPServerProtocol(Protocol):
    name: str = ''; command: str = ''; args: list[str] = []
    env: dict[str, str] = {}; transport: str = 'stdio'

    async def on_start(self):
        if self.transport == 'stdio':
            params = StdioServerParameters(command=self.command, args=self.args, env={**os.environ, **self.env})
            self._ctx = stdio_client(params)
            read, write = await self._ctx.__aenter__()
        else:
            from mcp.client.sse import sse_client
            self._ctx = sse_client(self.command)
            read, write = await self._ctx.__aenter__()
        self.adapter = ClientSession(read, write)
        await self.adapter.initialize()

    async def list_tools(self) -> list[dict]:
        result = await self.adapter.list_tools()
        return [{'name': t.name, 'description': t.description or '', 'inputSchema': t.inputSchema or {}} for t in result.tools]

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self.adapter.call_tool(name, arguments)
        return '\n'.join(c.text for c in result.content if hasattr(c, 'text'))
```

## MCPService

```python
class MCPService(AppService):
    depends = ['env.local']
    _servers: dict[str, MCPServerProtocol] = {}

    async def on_started(self):
        for name, conf in self.config.get('servers', {}).items():
            proto = MCPServerProtocol(**conf)
            await proto.maybe_start()
            self._servers[name] = proto

    def get_tool_schemas(self) -> list[dict]:
        schemas = []
        for server_name, proto in self._servers.items():
            for t in proto._cached_tools:
                schemas.append({
                    'type': 'function',
                    'function': {'name': f'mcp__{server_name}__{t["name"]}',
                                 'description': t['description'], 'parameters': t['inputSchema']}
                })
        return schemas

    async def call_tool(self, full_name: str, arguments: dict) -> str:
        parts = full_name.split('__')
        server_name, tool_name = parts[1], parts[2]
        proto = self._servers.get(server_name)
        if not proto: return f"Error: MCP server '{server_name}' not found"
        return await proto.call_tool(tool_name, arguments)
```

## 执行路径

```
ReActStepCommand 聚合工具:
  env 工具   → env.get_tool_schemas()  → execute: env.execute_tool()
  agent 工具 → svc.get_agent_tool_schemas() → execute: yield SubCommand
  MCP 工具   → mcp.get_tool_schemas()  → execute: mcp.call_tool()

三类工具统一在 ReActStepCommand 中按 tool_name 前缀路由:
  mcp__xxx  → MCPService.call_tool()
  其他      → env.execute_tool() 或 agent tool dispatch
```

## 配置 → config.py

```python
'mcp.MCPService': {
    'module': 'a2.mcp.service.MCPService',
    'depends': ['env.local'],
    'servers': {},
},
```

MCP servers 在 `agent.toml` 中按需追加：

```toml
[mcp.MCPService.servers.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_TOKEN = "${GITHUB_TOKEN}" }
```

## Files

`mcp/service.py`、`mcp/server.py`（MCPServerProtocol）
