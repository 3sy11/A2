# 09 — MCP 集成

## Context

MCP（Model Context Protocol）支持外部工具服务器——Agent 可以连接 MCP 服务器访问内置工具之外的工具。这提供了插件生态系统：数据库工具、API 集成、自定义工作流等。

**核心设计决策：MCPServer 是 Protocol 子类（资源适配器），MCPService(AppService) 只做生命周期管理。** MCP 服务器是外部资源（进程 + 连接），Protocol 模式天然适配：`adapter` 持有 MCP 客户端连接，`on_start()`/`on_stop()` 管理生命周期，`call_tool()` 是资源访问接口。

## bollydog Protocol 模式对照

| bollydog 概念 | MCP 对应 | 说明 |
|---|---|---|
| Protocol（资源适配器） | MCPServerProtocol | 包装 MCP 连接 |
| Protocol.adapter（原始连接） | MCP Client (stdio/SSE) | JSON-RPC 连接 |
| Protocol.on_start() | 启动 MCP 服务器进程 | `asyncio.create_subprocess_exec` |
| Protocol.on_stop() | 终止进程 | `process.terminate()` |
| AppService（业务逻辑） | MCPService | 只做生命周期管理 + 工具注册 |
| smart_import() + _build_protocol() | MCPService 中的工厂 | 从 TOML config 动态实例化 Protocol |
| Tool（执行单元） | MCPToolWrapper | 委托到 Protocol 执行 |

## 架构

```
MCPService(AppService)                    ← 只做管理，不做执行
├── depends: ["a2.ToolService"]           ← 注册工具到 ToolService
│
├── _servers: dict[str, MCPServerProtocol]← 从 TOML config 创建的 Protocol 实例
│
├── on_start()                            ← 生命周期：连接所有服务器
│   └── for name, conf in config['servers']:
│       └── smart_import(conf.module)(**conf) → MCPServerProtocol
│
├── on_stop()                             ← 生命周期：断开所有连接
│   └── server.disconnect() for all
│
└── _discover_and_register()              ← 发现工具并注册
    └── for server in _servers:
        └── MCPToolWrapper(server_protocol=server, tool_def=def)
            → ToolService.register_tool()

MCPServerProtocol(Protocol)               ← 资源适配器，一个 MCP 服务器一个实例
├── adapter: MCPClient                    ← JSON-RPC over stdio/SSE
├── name: str                             ← 服务器名称
├── command / args / env                  ← 启动参数
│
├── on_start()                            ← 启动进程、初始化连接
├── on_stop()                             ← 终止进程
├── list_tools() → list[MCPToolDef]       ← 获取可用工具
└── call_tool(name, arguments) → str      ← 调用工具

MCPToolWrapper(Tool)                      ← 委托到 Protocol 的薄包装
├── server_protocol: MCPServerProtocol    ← 持有 Protocol 引用（非原始连接）
├── _tool_def: MCPToolDef                 ← 工具定义（schema）
│
├── name → "mcp__{server}__{tool}"        ← 命名空间
├── description → str                     ← 来自 tool_def
├── parameters → dict                     ← 来自 tool_def.inputSchema
└── execute(**kwargs) → str               ← self.server_protocol.call_tool()
```

### 执行路径对比

**内置工具：** Tool → ToolService.execute() → EnvService → Protocol.execute()
**MCP 工具：** Tool → ToolService.execute() → MCPToolWrapper.execute() → MCPServerProtocol.call_tool()

两者都经过 ToolService.execute()，权限检查、参数验证、审计日志统一处理。

## Class Signatures

### MCPServerProtocol (Protocol)

```python
from bollydog.models.protocol import Protocol

class MCPServerProtocol(Protocol):
    """MCP 服务器的 Protocol 适配器。
    adapter = MCP 客户端连接（stdio 进程或 SSE 客户端）。"""
    name: str = ''
    command: str = ''
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    transport: str = 'stdio'  # 'stdio' 或 'sse'

    _process: asyncio.subprocess.Process = None
    _reader: 'MCPReader' = None

    async def on_start(self):
        """启动 MCP 服务器进程并初始化连接。"""
        if self.transport == 'stdio':
            self._process = await asyncio.create_subprocess_exec(
                self.command, *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **self.env},
            )
            self.adapter = MCPReader(self._process.stdout, self._process.stdin)
            await self.adapter.initialize()
        elif self.transport == 'sse':
            self.adapter = await MCPHttpClient.connect(self.command)
        else:
            raise ValueError(f"Unknown transport: {self.transport}")

    async def on_stop(self):
        """终止 MCP 服务器进程。"""
        if self._process:
            self._process.terminate()
            await self._process.wait()

    async def list_tools(self) -> list['MCPToolDef']:
        """获取服务器的可用工具。"""
        response = await self.adapter.call('tools/list')
        return [MCPToolDef(**t) for t in response.get('tools', [])]

    async def call_tool(self, name: str, arguments: dict) -> str:
        """调用此服务器上的工具。"""
        response = await self.adapter.call('tools/call', {
            'name': name,
            'arguments': arguments,
        })
        content = response.get('content', [])
        return '\n'.join(c.get('text', '') for c in content if c.get('type') == 'text')
```

**设计要点：**
- `adapter` 持有 MCP 客户端连接（MCPReader 或 MCPHttpClient），与 bollydog 的 `Protocol.adapter` 语义一致
- `on_start()` / `on_stop()` 管理进程生命周期，bollydog 自动调用
- `list_tools()` 和 `call_tool()` 是 MCP 协议的资源访问方法
- Protocol 实例在 MCPService 的生命周期内存活（长连接），与数据库连接池同模式

### MCPService (AppService)

```python
class MCPService(AppService):
    """MCP 服务：管理 MCP 服务器的 Protocol 生命周期。
    只做管理，不做执行——执行通过 MCPServerProtocol 完成。"""
    domain = 'a2'
    alias = 'MCPService'
    depends = ['a2.ToolService']

    _servers: dict[str, MCPServerProtocol] = {}

    async def on_start(self):
        """启动时：创建 Protocol 实例、连接服务器、发现并注册工具。"""
        from mode.utils.imports import smart_import

        servers_config = self.config.get('servers', {})
        for name, conf in servers_config.items():
            # 动态加载 Protocol 子类（支持自定义 MCPServerProtocol）
            cls_path = conf.pop('module', 'a2.mcp.server.MCPServerProtocol')
            cls = smart_import(cls_path)
            server = cls(name=name, **conf)

            # 启动连接（bollydog 生命周期）
            await server.on_start()
            self._servers[name] = server

        # 发现工具并注册到 ToolService
        await self._discover_and_register()

    async def on_stop(self):
        """关闭时：断开所有 MCP 服务器。"""
        for server in self._servers.values():
            await server.on_stop()
        self._servers.clear()

    async def _discover_and_register(self):
        """从所有服务器发现工具，创建 MCPToolWrapper 并注册。"""
        tool_service = await self.resolve('a2.ToolService')
        for server in self._servers.values():
            try:
                tool_defs = await server.list_tools()
                for tool_def in tool_defs:
                    wrapper = MCPToolWrapper(
                        server_protocol=server,
                        tool_def=tool_def,
                    )
                    wrapper._service = tool_service
                    tool_service.register_tool(wrapper)
            except Exception as e:
                # 服务器连接失败不影响其他服务器
                print(f"MCP server '{server.name}' tool discovery failed: {e}")
```

**设计要点：**
- MCPService 不持有任何执行逻辑，只做生命周期管理和工具注册
- 使用 `smart_import()` 动态加载 Protocol 子类，与 bollydog 的 `_build_protocol()` 模式一致
- `server.on_start()` / `server.on_stop()` 委托到 Protocol 生命周期
- `_discover_and_register()` 创建 MCPToolWrapper 并注册到 ToolService
- 服务器连接失败时隔离错误，不影响其他服务器

### MCPToolWrapper (Tool)

```python
class MCPToolWrapper(Tool):
    """将 MCP 工具包装为 ToolService 的普通 Tool。
    持有 MCPServerProtocol 引用，委托执行到 Protocol。"""
    server_protocol: MCPServerProtocol = None
    _tool_def: 'MCPToolDef' = None

    @property
    def name(self) -> str:
        return f'mcp__{self.server_protocol.name}__{self._tool_def.name}'

    @property
    def description(self) -> str:
        return self._tool_def.description or f'MCP 工具，来自 {self.server_protocol.name}'

    @property
    def parameters(self) -> dict:
        return self._tool_def.inputSchema or {'type': 'object', 'properties': {}}

    async def execute(self, **kwargs) -> str:
        """委托到 MCPServerProtocol 执行。"""
        return await self.server_protocol.call_tool(self._tool_def.name, kwargs)
```

**设计要点：**
- 持有 `server_protocol`（Protocol 引用）而非 `server`（原始连接引用）
- `execute()` 委托到 Protocol 的 `call_tool()`，与内置工具通过 EnvService 委托到 Protocol 的模式对称
- `name` 属性使用命名空间 `mcp__{server}__{tool}` 避免冲突

## TOML 配置

```toml
["a2.MCPService"]
module = "a2.mcp.service.MCPService"

# 每个服务器是一个 Protocol 实例
# module 字段支持自定义 MCPServerProtocol 子类（smart_import 动态加载）
["a2.MCPService".servers.github]
module = "a2.mcp.server.MCPServerProtocol"    # 可替换为自定义子类
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_TOKEN = "${GITHUB_TOKEN}" }

["a2.MCPService".servers.postgres]
module = "a2.mcp.server.MCPServerProtocol"
command = "mcp-server-postgres"
args = ["postgresql://localhost/mydb"]

["a2.MCPService".servers.custom]
module = "a2.mcp.server.MCPServerProtocol"
command = "python"
args = ["-m", "my_mcp_server"]
transport = "stdio"
```

**与 bollydog `_build_protocol()` 的关系：** bollydog 的 `_build_protocol()` 适用于单 Protocol 链（一个 AppService 一条链）。MCP 的场景是 N 个独立服务器（每个有自己的进程和连接），需要 MCPService 作为工厂从 config 动态创建 N 个 Protocol 实例。这与 MemoryService 的 L0-L4 各自独立创建 Protocol 的模式类似，只是 MCP 的数量是动态的。

## 工具命名约定

MCP 工具使用命名空间：`mcp__{server}__{tool}`

示例：`mcp__github__search_repos`、`mcp__postgres__query`

这避免了 MCP 服务器之间以及与内置工具之间的命名冲突。

## 生命周期管理

```
AgentService 启动
    → MCPService.on_start()
        → smart_import() 动态加载每个 MCPServerProtocol
        → server.on_start() 启动进程、建立连接
        → _discover_and_register() 发现工具、创建 MCPToolWrapper、注册到 ToolService

AgentService 关闭
    → MCPService.on_stop()
        → server.on_stop() 终止进程、清理连接
```

**设计决策：** AgentService 启动时启动所有 MCP 服务器，关闭时全部终止。原因：MCP 服务器启动可能较慢（如 `npx` 需要下载），提前启动避免首次调用延迟。空闲关闭可作为后续优化。

## 自定义 Protocol 子类

`module` 字段支持替换为自定义 MCPServerProtocol 子类，用于：
- 自定义传输协议（WebSocket、gRPC）
- 自定义认证流程（OAuth、API Key 注入）
- 自定义响应解析（非标准 MCP 服务器）

```python
class CustomMCPServer(MCPServerProtocol):
    """自定义 MCP 服务器：带 OAuth 认证。"""
    oauth_token: str = ''

    async def on_start(self):
        self.oauth_token = await self._authenticate()
        await super().on_start()

    async def call_tool(self, name: str, arguments: dict) -> str:
        arguments['_auth'] = self.oauth_token
        return await super().call_tool(name, arguments)
```

TOML 配置：
```toml
["a2.MCPService".servers.custom]
module = "my_project.mcp.CustomMCPServer"
command = "my-server"
oauth_token = "${OAUTH_TOKEN}"
```

## Files to Create

| File | Purpose |
|------|---------|
| `mcp/__init__.py` | Exports |
| `mcp/service.py` | MCPService(AppService)：生命周期管理 + 工具注册 |
| `mcp/server.py` | MCPServerProtocol(Protocol)：MCP 连接适配器 |
| `mcp/wrapper.py` | MCPToolWrapper(Tool)：委托到 Protocol 的薄包装 |

## Dependencies

- `bollydog.models.protocol.Protocol` — MCPServerProtocol 的父类
- `mode.utils.imports.smart_import` — 动态加载 Protocol 子类
- `a2.ToolService` — 注册 MCP 工具
- MCP 协议（JSON-RPC over stdio/SSE）
- `asyncio.create_subprocess_exec` 用于服务器进程管理
