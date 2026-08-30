# 模块：EnvService（执行环境 + 工具）

> domain=`env` | 运行时有且只有一个实例（默认 local，可替换 docker/ssh） | Protocol: TerminalProtocol (复合)

---

## 设计理念

**工具是环境的能力，而非独立实体。** EnvService 同时拥有执行环境（protocol）和配套工具（ToolCommand），不再需要独立的 ToolService 门面。

- 运行时有且只有**一个** EnvService 实例（默认 `env.local`，TOML 可替换为 `env.docker` 或 `env.ssh`）
- ToolCommand 注册在 EnvService 上，通过 `globals.protocol` 自动路由到 TerminalProtocol
- 环境无关工具（web_search, ask_user, spawn_agent）注册在 AgentService 上

## 架构

```
EnvService(AppService), domain="env"
├── protocol: TerminalProtocol (复合协议)
│   ├── adapter: LocalTerminal / DockerTerminal / SSHTerminal  ← 命令执行
│   └── protocol: PermissionProtocol                           ← 内层：权限校验
│       └── PermissionConfig 数据模型（从 config 加载）
├── commands: ['a2.env.tools']   ← ToolCommand 批量注册
├── get_tool_schemas(filter) → list[dict]     ← 供 ReActStep 构建 LLM tools
└── execute_tool(name, params) → str          ← protocol 内部先校验权限再执行
```

## TerminalProtocol — 复合协议

外层负责命令执行，内层 `PermissionProtocol` 负责权限校验。执行前先通过内层 `self.protocol.check(tool_name)` 校验。

| adapter 类型 | 执行方式 | 依赖 |
|-------------|---------|------|
| LocalTerminal | `asyncio.create_subprocess_exec` | — |
| DockerTerminal | 容器内执行 | `aiodocker` |
| SSHTerminal | 远程执行 | `asyncssh` |

```python
class TerminalProtocol(Protocol):
    """复合协议：外层命令执行 + 内层权限校验。"""
    async def execute(self, command: str, timeout: int = 30) -> str:
        return await self.adapter.run(command, timeout)
    async def read_file(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        return await self.adapter.read_file(path, offset, limit)
    async def write_file(self, path: str, content: str) -> str:
        return await self.adapter.write_file(path, content)

    async def check_permission(self, tool_name: str) -> str:
        """委托内层 PermissionProtocol 校验。"""
        return self.protocol.check(tool_name)

class PermissionProtocol(Protocol):
    """内层权限协议，管理 PermissionConfig 数据模型。"""
    _config: PermissionConfig = None
    async def on_start(self):
        rules = self.config.get('rules', [])
        self._config = PermissionConfig(rules=[PermissionRule(**r) for r in rules]) if rules else PermissionConfig()
    def check(self, tool_name: str) -> str:
        return self._config.check(tool_name)
```

## EnvService

```python
class EnvService(AppService):
    depends = []

    async def on_started(self):
        self._tools: dict[str, type] = {}
        for cmd_cls in (self.__class__.commands_registry or []):
            if hasattr(cmd_cls, 'tool_name'):
                self._tools[cmd_cls.tool_name] = cmd_cls

    def get_tool_schemas(self, role_filter: list[str] = None, current_model: str = None) -> list[dict]:
        schemas = []
        for name, cmd_cls in self._tools.items():
            if role_filter and '*' not in role_filter and name not in role_filter: continue
            if current_model and cmd_cls.required_model and current_model not in cmd_cls.required_model: continue
            schemas.append(cmd_cls.to_schema())
        return schemas

    async def execute_tool(self, name: str, params: dict) -> str:
        cmd_cls = self._tools.get(name)
        if not cmd_cls: return f"Error: Unknown tool '{name}'"
        perm = await self.protocol.check_permission(name)  # 复合协议内层校验
        if perm == 'block': return f"Error: Tool '{name}' is blocked"
        cmd = cmd_cls(**cmd_cls.cast_params(params))
        errors = cmd.validate_params(params)
        if errors: return f"Validation error: {'; '.join(errors)}"
        try: result = await cmd.execute()
        except Exception as e: result = f"Error: {e}\n[Analyze the error and try a different approach.]"
        return result[:30000]
```

## ToolCommand 基类

```python
class ToolCommand(BaseCommand, abstract=True):
    tool_name: ClassVar[str]; description: ClassVar[str]; parameters: ClassVar[dict]
    is_destructive: ClassVar[bool] = False
    required_model: ClassVar[list[str]] = []

    @classmethod
    def cast_params(cls, params: dict) -> dict:
        props = cls.parameters.get('properties', {})
        for k, v in list(params.items()):
            spec = props.get(k, {})
            if spec.get('type') == 'integer' and isinstance(v, str): params[k] = int(v)
            elif spec.get('type') == 'boolean' and isinstance(v, str): params[k] = v.lower() in ('true', '1')
        return params

    def validate_params(self, params: dict) -> list[str]:
        return [f"Missing: {f}" for f in self.parameters.get('required', []) if f not in params]

    @classmethod
    def to_schema(cls) -> dict:
        return {'type': 'function', 'function': {'name': cls.tool_name, 'description': cls.description, 'parameters': cls.parameters}}

    async def execute(self, **kw) -> str: ...
```

## 内置工具清单

| 工具 | is_destructive | Protocol 方法 |
|------|---------------|--------------|
| `read_file` | ✗ | `protocol.read_file` |
| `write_file` | ✓ | `protocol.write_file` |
| `str_replace` | ✓ | `protocol.read_file` + `write_file` |
| `bash` | ✓ | `protocol.execute` |
| `python` | ✓ | `protocol.execute('python -c')` |
| `list_dir` | ✗ | `protocol.execute('ls')` |
| `grep_search` | ✗ | `protocol.execute('rg')` |
| `upload` / `download` | ✓/✗ | `protocol.upload` / `download` |

> `ask_user`、`spawn_agent`、`create_role`、`present_files`、`web_search`、`web_fetch` 是环境无关工具，注册在 AgentService 上。

## 权限模型 — PermissionProtocol 内层管理

权限校验在 TerminalProtocol 复合协议内层完成，数据模型详见 [01-data-models.md](01-data-models.md) §五。

ToolCommand 执行时，EnvService 调用 `self.protocol.check_permission(name)`，委托到内层 `PermissionProtocol.check()` → 遍历 `PermissionRule` 列表按通配符匹配。

## Tool.required_model — 工具降级策略

大部分 ToolCommand 不关心模型（`required_model = []`），特定工具需要特定模型能力。

| Tool 示例 | required_model | 原因 |
|-----------|---------------|------|
| `vision_analyze` | `["anthropic/claude-sonnet-4-20250514", "openai/gpt-4o"]` | 多模态 |
| `code_edit` | `["anthropic/claude-sonnet-4-20250514", "deepseek/deepseek-coder"]` | 强代码 |
| `bash` / `read_file` | `[]`（任意） | 无要求 |

**v1 策略：工具降级。** ReActStepCommand 构建 tools 时，移除与当前 `role_def.model` 不兼容的 ToolCommand。LLM 看不到不兼容的工具，自然不会调用。

## 多环境编排（v2）

v1 运行时有且只有一个 EnvService 实例。多环境场景需要跨进程或多实例部署：

- **v1**：单 Env 实例，所有角色共用同一执行环境
- **v2**：多进程部署，每个进程绑定不同 Env，通过 HTTP router 跨进程 SpawnAgent

```python
# v2 跨进程编排（每个进程各自的 env 配置不同）
[staging_r, prod_r] = yield [
    SpawnAgentCommand(role="staging_admin", input={"task": "获取最近100行日志"}),
    SpawnAgentCommand(role="prod_admin", input={"task": "获取最近100行日志"}),
]
```

## 配置 → env/config.py（默认 local，可替换）

模块默认配置在 `a2/env/config.py` 中管理。切换环境在 `agent.toml` 中**替换**配置节即可。

```python
# a2/env/config.py
ENV_DEFAULT = {
    'env.local': {
        'module': 'a2.env.service.EnvService', 'alias': 'local',
        'commands': ['a2.env.tools'],
        'protocol': {
            'module': 'a2.env.protocol.TerminalProtocol',
            'adapter': {'module': 'a2.env.protocol.LocalTerminal', 'working_dir': '.'},
            'protocol': {
                'module': 'a2.env.protocol.PermissionProtocol',
                'rules': [
                    {'pattern': 'read_file', 'action': 'allow'},
                    {'pattern': 'list_dir', 'action': 'allow'},
                    {'pattern': 'grep_search', 'action': 'allow'},
                    {'pattern': '*', 'action': 'allow'},
                ],
            },
        },
    },
}
```

替换为 Docker 环境（agent.toml 中覆盖）：
```toml
[env.local.protocol.adapter]
module = "a2.env.protocol.DockerTerminal"
image = "python:3.12-slim"
```

替换为 SSH 环境：
```toml
[env.local.protocol.adapter]
module = "a2.env.protocol.SSHTerminal"
host = "prod.example.com"
```

## Files

```
env/
├── config.py        # ENV_DEFAULT
├── service.py       # EnvService（执行环境 + 工具管理）
├── tools.py         # ToolCommand 基类 + 内置工具实现
└── protocol.py      # TerminalProtocol（复合）+ PermissionProtocol + Local/Docker/SSH adapter
```
