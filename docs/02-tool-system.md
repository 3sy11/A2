# 02 — Tool System

## Context

工具系统是 Agent 的"双手"——与真实世界交互的能力。设计原则：开放封闭——对新工具开放（注册一行），对循环封闭（dispatch 逻辑不变）。

核心设计决策：**环境即服务**。每个执行环境（Local/Docker/SSH）是一个独立的 AppService，持有自己的 Protocol。ToolService 作为门面，统一管理工具注册和执行路由。Tool 是协议无关的薄包装，通过 `self._service` 回调执行。

## Architecture

```
ToolService (AppService, 门面)
├── depends: ["a2.env.local.LocalToolService"]
├── depends: ["a2.env.docker.DockerToolService"]
├── depends: ["a2.env.ssh.SSHToolService"]
│
├── _tools: dict[str, Tool]              # 统一工具注册表
├── _envs: dict[str, AppService]         # 环境名 → 环境服务
│
├── register(tool, env)                  # 注册工具到指定环境
├── get_schemas() → list[dict]           # 收集所有工具 schema（注入 env 参数）
└── execute(name, params, env) → str     # 路由到对应环境服务执行

环境服务 (AppService, 每个执行环境一个)
├── protocol: TerminalProtocol           # 通过标准 bollydog 配置注入
│
├── execute(command, timeout) → str      # 委托给 protocol.execute()
├── read_file(path, offset, limit) → str # 委托给 protocol.read_file()
├── write_file(path, content) → str      # 委托给 protocol.write_file()
├── upload(local, remote) → str          # 委托给 protocol.upload()
└── download(remote, local) → str        # 委托给 protocol.download()

Tool (BaseCommand, 协议无关的薄包装)
├── name: ClassVar[str]
├── description: ClassVar[str]
├── parameters: ClassVar[dict]
├── provider: ClassVar[str]              # 直接指定 provider（精确）
├── capability: ClassVar[str]            # 指定能力级别（模糊）
├── _service: ToolService                # 引用 ToolService（路由入口）
├── __call__(**kwargs) → str
└── to_schema() → dict
```

### 执行流程

```
Agent 调用: tool_service.execute('bash', {'command': 'ls', 'env': 'local'})

1. ToolService.execute('bash', {'command': 'ls', 'env': 'local'})
   ├── 查找 tool: _tools['bash'] → BashTool
   ├── 校验参数: tool.cast_params + tool.validate_params
   └── 路由执行: _envs['local'].execute('ls', timeout=30)

2. LocalToolService.execute('ls', timeout=30)
   └── self.protocol.execute('ls', timeout=30)

3. LocalProtocol.execute('ls', timeout=30)
   └── asyncio.create_subprocess_shell('ls', ...)
```

## Class Signatures

### TerminalProtocol (`tools/protocol.py`)

先在 A2 中实现，验证模式后再考虑提升到 bollydog。继承 bollydog 的 `Protocol` 基类，复用 `on_start`/`on_stop` 生命周期和 `self.adapter` 资源引用。

```python
from bollydog.models.protocol import Protocol


class TerminalProtocol(Protocol, abstract=True):
    """执行环境的完整能力接口。

    Protocol 基类已提供：
        on_start() / on_stop()    — 生命周期（子类实现）
        __aenter__ / __aexit__    — 上下文管理（基类实现）
        self.adapter              — 底层资源引用（子类赋值）

    TerminalProtocol 新增 5 个操作方法：
        execute()      — 执行 shell 命令（必须实现）
        read_file()    — 读取文件（必须实现）
        write_file()   — 写入文件（必须实现）
        upload()       — 本地→该环境 传输（可选，不支持时 raise）
        download()     — 该环境→本地 传输（可选，不支持时 raise）
    """

    async def execute(self, command: str, timeout: int = 30) -> str:
        """执行 shell 命令。所有 backend 必须实现。"""
        raise NotImplementedError

    async def read_file(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        """读取文件内容。所有 backend 必须实现。"""
        raise NotImplementedError

    async def write_file(self, path: str, content: str) -> str:
        """写入文件内容。所有 backend 必须实现。"""
        raise NotImplementedError

    async def upload(self, local_path: str, remote_path: str) -> str:
        """将本地文件传输到该执行环境。可选——本地 backend 可不实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support upload")

    async def download(self, remote_path: str, local_path: str) -> str:
        """将该环境的文件取回本地。可选——本地 backend 可不实现。"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support download")
```

**接口分类：**

| 方法 | 必须/可选 | 原因 |
|------|----------|------|
| `execute(command, timeout)` | 必须 | 核心能力——所有环境都能跑命令 |
| `read_file(path, offset, limit)` | 必须 | 工具（ReadTool/EditTool）依赖 |
| `write_file(path, content)` | 必须 | 工具（WriteTool/EditTool）依赖 |
| `upload(local, remote)` | 可选 | 仅远程环境需要（Docker/SSH） |
| `download(remote, local)` | 可选 | 仅远程环境需要（Docker/SSH） |

LocalProtocol 的 upload/download 可以用 `shutil.copy` 实现（本地到本地），也可以不实现（由 Tool 层跳过）。

各 backend 实现（`tools/protocol.py`）：

```python
class LocalProtocol(TerminalProtocol):
    """本机执行。read_file/write_file 直接操作文件系统。"""

    async def execute(self, command: str, timeout: int = 30) -> str:
        proc = await asyncio.create_subprocess_shell(
            command, stdout=PIPE, stderr=PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        return (stdout + stderr).decode()[-50000:]

    async def read_file(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        lines = Path(path).read_text().splitlines()
        selected = lines[offset:offset + limit]
        return '\n'.join(f'{i+offset+1}\t{line}' for i, line in enumerate(selected))

    async def write_file(self, path: str, content: str) -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Written {len(content)} bytes to {path}"

    async def upload(self, local_path: str, remote_path: str) -> str:
        # 本地到本地，直接 copy
        shutil.copy2(local_path, remote_path)
        return f"Copied to {remote_path}"

    async def download(self, remote_path: str, local_path: str) -> str:
        shutil.copy2(remote_path, local_path)
        return f"Downloaded to {local_path}"


class DockerProtocol(TerminalProtocol):
    """Docker 容器执行。文件操作通过 docker exec/cp 实现。"""

    image: str = 'python:3.12'

    async def execute(self, command: str, timeout: int = 30) -> str:
        proc = await asyncio.create_subprocess_exec(
            'docker', 'exec', self.adapter, 'sh', '-c', command,
            stdout=PIPE, stderr=PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        return (stdout + stderr).decode()[-50000:]

    async def read_file(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        return await self.execute(
            f'cat -n {path} | tail -n +{offset+1} | head -n {limit}')

    async def write_file(self, path: str, content: str) -> str:
        escaped = content.replace("'", "'\\''")
        return await self.execute(f"echo '{escaped}' > {path}")

    async def upload(self, local_path: str, remote_path: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            'docker', 'cp', local_path, f'{self.adapter}:{remote_path}',
            stdout=PIPE, stderr=PIPE)
        await proc.communicate()
        return f"Uploaded to container:{remote_path}"

    async def download(self, remote_path: str, local_path: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            'docker', 'cp', f'{self.adapter}:{remote_path}', local_path,
            stdout=PIPE, stderr=PIPE)
        await proc.communicate()
        return f"Downloaded to {local_path}"


class SSHProtocol(TerminalProtocol):
    """远程 SSH 执行。文件操作通过 ssh/scp 实现。"""

    host: str = ''
    key_file: str = None

    async def execute(self, command: str, timeout: int = 30) -> str:
        args = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes']
        if self.key_file:
            args.extend(['-i', self.key_file])
        args.extend([self.host, command])
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=PIPE, stderr=PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        return (stdout + stderr).decode()[-50000:]

    async def read_file(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        return await self.execute(
            f'cat -n {path} | tail -n +{offset+1} | head -n {limit}')

    async def write_file(self, path: str, content: str) -> str:
        escaped = content.replace("'", "'\\''")
        return await self.execute(f"echo '{escaped}' > {path}")

    async def upload(self, local_path: str, remote_path: str) -> str:
        args = ['scp', '-o', 'StrictHostKeyChecking=no']
        if self.key_file:
            args.extend(['-i', self.key_file])
        args.extend([local_path, f'{self.host}:{remote_path}'])
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=PIPE, stderr=PIPE)
        await proc.communicate()
        return f"Uploaded to {self.host}:{remote_path}"

    async def download(self, remote_path: str, local_path: str) -> str:
        args = ['scp', '-o', 'StrictHostKeyChecking=no']
        if self.key_file:
            args.extend(['-i', self.key_file])
        args.extend([f'{self.host}:{remote_path}', local_path])
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=PIPE, stderr=PIPE)
        await proc.communicate()
        return f"Downloaded to {local_path}"
```

### Environment Service（环境服务）

```python
class LocalToolService(EnvironmentService):
    """本地执行环境。Protocol 由 TOML 注入。"""
    domain = 'a2.env.local'
    alias = 'LocalToolService'

    async def upload(self, local_path: str, remote_path: str) -> str:
        return await self.protocol.upload(local_path, remote_path)

    async def download(self, remote_path: str, local_path: str) -> str:
        return await self.protocol.download(remote_path, local_path)


class EnvironmentService(AppService, abstract=True):
    """通用环境服务基类——消除 Local/Docker/SSH 的重复代码。
    所有操作委托给 TOML 注入的 TerminalProtocol。"""
    depends = []

    async def execute(self, command: str, timeout: int = 30) -> str:
        return await self.protocol.execute(command, timeout)

    async def read_file(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        return await self.protocol.read_file(path, offset, limit)

    async def write_file(self, path: str, content: str) -> str:
        return await self.protocol.write_file(path, content)

    async def upload(self, local_path: str, remote_path: str) -> str:
        return await self.protocol.upload(local_path, remote_path)

    async def download(self, remote_path: str, local_path: str) -> str:
        return await self.protocol.download(remote_path, local_path)


class DockerToolService(EnvironmentService):
    """Docker 执行环境。Protocol 由 TOML 注入。"""
    domain = 'a2.env.docker'
    alias = 'DockerToolService'


class SSHToolService(EnvironmentService):
    """SSH 远程执行环境。Protocol 由 TOML 注入。"""
    domain = 'a2.env.ssh'
    alias = 'SSHToolService'
```

### Tool (Base Class)

```python
class Tool(BaseCommand, abstract=True):
    """工具基类。协议无关——只调用 self._service，不直接调用 protocol。
    abstract=True 防止 __init_subclass__ 注册到 BaseService.registry。"""
    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict]
    is_destructive: ClassVar[bool] = False
    provider: ClassVar[str] = None
    capability: ClassVar[str] = None

    _service: 'ToolService' = None

    async def __call__(self, **kwargs) -> str:
        return await self.execute(**kwargs)

    async def execute(self, **kwargs) -> str:
        raise NotImplementedError

    def cast_params(self, params: dict) -> dict:
        """根据 parameters schema 做类型转换（str→int 等）。"""
        props = self.parameters.get('properties', {})
        for k, v in list(params.items()):
            spec = props.get(k, {})
            if spec.get('type') == 'integer' and isinstance(v, str):
                params[k] = int(v)
            elif spec.get('type') == 'boolean' and isinstance(v, str):
                params[k] = v.lower() in ('true', '1')
        return params

    def validate_params(self, params: dict) -> list[str]:
        """校验必填参数。返回错误列表，空列表表示通过。"""
        errors = []
        for field in self.parameters.get('required', []):
            if field not in params:
                errors.append(f"Missing required parameter: {field}")
        return errors

    def to_schema(self) -> dict:
        return {
            'type': 'function',
            'function': {
                'name': self.name, 'description': self.description,
                'parameters': self.parameters,
            }
        }
```

### ToolService (Facade)

```python
class ToolService(AppService):
    """门面：统一工具注册 + 环境路由。不直接持有 Protocol。"""
    domain = 'a2'
    alias = 'ToolService'
    depends = [
        'a2.env.local.LocalToolService',
        'a2.env.docker.DockerToolService',
        'a2.env.ssh.SSHToolService',
    ]
    # ↑ depends 键精确匹配各服务的 {domain}.{alias}
    # 见 10-bollydog-integration-conventions.md §2

    _tools: dict[str, Tool] = {}
    _tool_env: dict[str, str] = {}
    _envs: dict[str, AppService] = {}

    _hint: str = "\n\n[Analyze the error above and try a different approach.]"

    async def on_started(self):
        # 1. 从 depends 注入的 _children 中解析环境服务引用
        for dep in self._children:
            if isinstance(dep, Protocol): continue
            name = type(dep).__name__.replace('ToolService', '').lower()
            if name: self._envs[name] = dep
        if not self._envs.get('local'):
            self._envs['local'] = self._children[0]

        # 2. 注册内置工具
        enabled = self.config.get('enabled_tools', {})
        for env_name, tool_names in enabled.items():
            for tool_name in tool_names:
                tool = _create_builtin(tool_name)
                tool._service = self
                self._tools[tool_name] = tool
                self._tool_env[tool_name] = env_name

    def get_schemas(self) -> list[dict]:
        """收集所有工具 schema，注入 env 参数让 LLM 选择执行环境。"""
        envs = list(self._envs.keys())
        schemas = []
        for tool in self._tools.values():
            schema = tool.to_schema()
            # 注入 env 参数
            schema['function']['parameters']['properties']['env'] = {
                'type': 'string',
                'enum': envs,
                'default': self._tool_env.get(tool.name, 'local'),
                'description': 'Execution environment'
            }
            schemas.append(schema)
        return schemas

    async def execute(self, name: str, params: dict, env: str = None) -> str:
        """路由执行：查找工具 → 确定环境 → 委托环境服务。"""
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found"

        # 确定执行环境
        env_name = env or params.pop('env', None) or self._tool_env.get(name, 'local')
        env_service = self._envs.get(env_name)
        if not env_service:
            return f"Error: Unknown environment '{env_name}'"

        try:
            params = tool.cast_params(params)
            errors = tool.validate_params(params)
            if errors:
                return f"Error: {'; '.join(errors)}" + self._hint
            result = await tool.execute(**params)
            return result + self._hint if isinstance(result, str) and result.startswith("Error") else result
        except Exception as e:
            return f"Error executing {name}: {e}" + self._hint
```

### Toolset

```python
class Toolset:
    """工具分组。用于批量启用/禁用，不绑定执行环境。"""
    def __init__(self, name: str, tools: list[type[Tool]]):
        self.name = name
        self.tool_classes = tools
```

## Built-in Tools (9 Atomic Tools)

所有工具都是协议无关的薄包装。文件操作通过 `self._service` 的环境服务委托给 Protocol。

### Filesystem Tools

```python
class ReadTool(Tool):
    name = 'read'
    description = 'Read file contents. Use offset/limit for large files.'
    parameters = {
        'type': 'object',
        'properties': {
            'path': {'type': 'string', 'description': 'Absolute file path'},
            'offset': {'type': 'integer', 'description': 'Line offset (0-based)'},
            'limit': {'type': 'integer', 'description': 'Max lines to read'},
        },
        'required': ['path'],
    }

    async def execute(self, path: str, offset: int = 0, limit: int = 2000, **kw) -> str:
        env_svc = self._service._envs[self._service._tool_env.get(self.name, 'local')]
        return await env_svc.read_file(path, offset, limit)


class WriteTool(Tool):
    name = 'write'
    description = 'Write content to a file. Creates parent directories.'
    is_destructive = True
    parameters = {
        'type': 'object',
        'properties': {
            'path': {'type': 'string'},
            'content': {'type': 'string'},
        },
        'required': ['path', 'content'],
    }

    async def execute(self, path: str, content: str, **kw) -> str:
        env_svc = self._service._envs[self._service._tool_env.get(self.name, 'local')]
        return await env_svc.write_file(path, content)


class EditTool(Tool):
    name = 'edit'
    description = 'Exact string replacement in a file.'
    is_destructive = True
    parameters = {
        'type': 'object',
        'properties': {
            'path': {'type': 'string'},
            'old_string': {'type': 'string'},
            'new_string': {'type': 'string'},
        },
        'required': ['path', 'old_string', 'new_string'],
    }

    async def execute(self, path: str, old_string: str, new_string: str, **kw) -> str:
        env_svc = self._service._envs[self._service._tool_env.get(self.name, 'local')]
        # 先读取
        content = await env_svc.read_file(path, offset=0, limit=999999)
        # 解析 cat -n 格式，提取纯内容
        lines = []
        for line in content.split('\n'):
            parts = line.split('\t', 1)
            lines.append(parts[1] if len(parts) > 1 else line)
        text = '\n'.join(lines)

        count = text.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {path}"
        if count > 1:
            return f"Error: old_string found {count} times — must be unique"
        text = text.replace(old_string, new_string, 1)
        return await env_svc.write_file(path, text)


class GlobTool(Tool):
    name = 'glob'
    description = 'Find files by pattern (e.g. **/*.py).'
    parameters = {
        'type': 'object',
        'properties': {
            'pattern': {'type': 'string'},
            'path': {'type': 'string', 'description': 'Search root (default: cwd)'},
        },
        'required': ['pattern'],
    }

    async def execute(self, pattern: str, path: str = '.', **kw) -> str:
        # glob 通过 shell find 命令实现，委托给环境服务
        env_svc = self._service._envs[self._service._tool_env.get(self.name, 'local')]
        return await env_svc.execute(f'find {path} -name "{pattern}" | head -500')


class GrepTool(Tool):
    name = 'grep'
    description = 'Search file contents by regex.'
    parameters = {
        'type': 'object',
        'properties': {
            'pattern': {'type': 'string'},
            'path': {'type': 'string'},
            'include': {'type': 'string', 'description': 'File glob filter (e.g. *.py)'},
        },
        'required': ['pattern'],
    }

    async def execute(self, pattern: str, path: str = '.', include: str = None, **kw) -> str:
        env_svc = self._service._envs[self._service._tool_env.get(self.name, 'local')]
        cmd = f'grep -rn "{pattern}" {path}'
        if include:
            cmd += f' --include="{include}"'
        cmd += ' | head -200'
        return await env_svc.execute(cmd)
```

### Shell Tools

```python
class BashTool(Tool):
    name = 'bash'
    description = 'Execute shell command. Returns stdout+stderr.'
    is_destructive = True
    parameters = {
        'type': 'object',
        'properties': {
            'command': {'type': 'string'},
            'timeout': {'type': 'integer', 'description': 'Timeout in seconds (default: 30)'},
        },
        'required': ['command'],
    }

    async def execute(self, command: str, timeout: int = 30, **kw) -> str:
        env_svc = self._service._envs[self._service._tool_env.get(self.name, 'local')]
        return await env_svc.execute(command, timeout)


class PythonExecTool(Tool):
    name = 'python_exec'
    description = 'Execute Python code in subprocess. Returns stdout+stderr.'
    is_destructive = True
    parameters = {
        'type': 'object',
        'properties': {
            'code': {'type': 'string'},
            'timeout': {'type': 'integer', 'default': 30},
        },
        'required': ['code'],
    }

    async def execute(self, code: str, timeout: int = 30, **kw) -> str:
        env_svc = self._service._envs[self._service._tool_env.get(self.name, 'local')]
        # 写入临时文件，通过 execute 执行
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            tmp_path = f.name
        try:
            return await env_svc.execute(f'python3 {tmp_path}', timeout)
        finally:
            os.unlink(tmp_path)
```

### Transfer Tools（跨环境传输）

```python
class UploadTool(Tool):
    name = 'upload'
    description = 'Upload a local file to a remote environment (SSH/Docker).'
    parameters = {
        'type': 'object',
        'properties': {
            'local_path': {'type': 'string', 'description': 'Local file path'},
            'remote_path': {'type': 'string', 'description': 'Destination path in target env'},
            'env': {'type': 'string', 'enum': ['docker', 'ssh'], 'description': 'Target environment'},
        },
        'required': ['local_path', 'remote_path', 'env'],
    }

    async def execute(self, local_path: str, remote_path: str, env: str, **kw) -> str:
        env_svc = self._service._envs.get(env)
        if not env_svc:
            return f"Error: Unknown environment '{env}'"
        return await env_svc.upload(local_path, remote_path)


class DownloadTool(Tool):
    name = 'download'
    description = 'Download a file from a remote environment to local.'
    parameters = {
        'type': 'object',
        'properties': {
            'remote_path': {'type': 'string', 'description': 'File path in remote env'},
            'local_path': {'type': 'string', 'description': 'Local destination path'},
            'env': {'type': 'string', 'enum': ['docker', 'ssh'], 'description': 'Source environment'},
        },
        'required': ['remote_path', 'local_path', 'env'],
    }

    async def execute(self, remote_path: str, local_path: str, env: str, **kw) -> str:
        env_svc = self._service._envs.get(env)
        if not env_svc:
            return f"Error: Unknown environment '{env}'"
        return await env_svc.download(remote_path, local_path)
```

### Agent Tools

```python
class SubagentTool(Tool):
    name = 'subagent'
    description = 'Delegate task to a specialized sub-agent.'
    parameters = {
        'type': 'object',
        'properties': {
            'agent_type': {'type': 'string', 'enum': ['explore', 'plan', 'general']},
            'prompt': {'type': 'string'},
        },
        'required': ['agent_type', 'prompt'],
    }

    async def execute(self, agent_type: str, prompt: str, **kw) -> str:
        # 通过 Hub dispatch SpawnSubagentCommand（见 15-command-separation.md）
        from bollydog.globals import hub
        from a2.subagent.commands import SpawnSubagentCommand
        cmd = SpawnSubagentCommand(task=prompt, agent_type=agent_type)
        result = await hub.execute(cmd)
        return str(await result.state)


class AskUserTool(Tool):
    name = 'ask_user'
    description = 'Ask the user a question when clarification is needed.'
    parameters = {
        'type': 'object',
        'properties': {
            'question': {'type': 'string'},
            'options': {'type': 'array', 'items': {'type': 'string'}},
        },
        'required': ['question'],
    }

    async def execute(self, question: str, options: list[str] = None, **kw) -> str:
        return json.dumps({'type': 'ask_user', 'question': question, 'options': options or []})
```

## Environment Tool Mapping

不同环境适合不同的工具组合：

| Tool | local | docker | ssh | 原因 |
|------|:-----:|:------:|:---:|------|
| read | ✅ | ✅ | ✅ | 通过 Protocol.read_file() 统一 |
| write | ✅ | ✅ | ✅ | 通过 Protocol.write_file() 统一 |
| edit | ✅ | ✅ | ✅ | read + write 组合 |
| glob | ✅ | ✅ | ✅ | 通过 shell find 实现 |
| grep | ✅ | ✅ | ✅ | 通过 shell grep 实现 |
| bash | ✅ | ✅ | ✅ | 通过 Protocol.execute() 统一 |
| python_exec | ✅ | ✅ | ✅ | 通过 Protocol.execute() 统一 |
| upload | — | ✅ | ✅ | 本地→远程传输 |
| download | — | ✅ | ✅ | 远程→本地传输 |
| subagent | — | — | — | 元工具，不依赖环境 |
| ask_user | — | — | — | 元工具，不依赖环境 |

## Tool LLM 需求声明

工具通过 `provider` 和 `capability` 两个属性声明 LLM 需求。解析优先级：`provider` > `capability` > 当前默认。

- **provider**（精确）：直接指定 provider 实例名称，如 `'claude-opus'`
- **capability**（模糊）：指定能力级别，路由到对应的能力服务，如 `'fast'`/`'capable'`/`'code'`
- **都未指定**：使用 Agent Loop 当前默认 capability

| Tool | provider | capability | 解析结果 |
|------|----------|-----------|---------|
| code_review | `'claude-opus'` | — | 直接用 claude-opus provider（最精确） |
| search | — | `'fast'` | 路由到 FastLLMService |
| bash | — | — | 用当前默认 capability |
| read/write/edit | — | — | 文件操作不需要 LLM |
| glob/grep | — | — | 搜索操作不需要 LLM |
| subagent | — | — | 子智能体自行指定 capability |
| ask_user | — | — | 用户交互不需要 LLM |

Agent Loop 中的解析逻辑参见 [06-llm-provider.md](06-llm-provider.md) 的"两层解析逻辑"章节。

## TOML Configuration

```toml
# 环境服务配置（标准 bollydog 格式，protocol 模块在 A2 中）
["a2.env.local.LocalToolService".protocol]
module = "a2.tools.protocol.LocalProtocol"

["a2.env.docker.DockerToolService".protocol]
module = "a2.tools.protocol.DockerProtocol"
image = "python:3.12"

["a2.env.ssh.SSHToolService".protocol]
module = "a2.tools.protocol.SSHProtocol"
host = "deploy@server"
key_file = "~/.ssh/deploy_key"

# ToolService 配置：哪些工具注册到哪个环境
["a2.ToolService"]
enabled_tools.local = ["read", "write", "edit", "glob", "grep", "bash", "python_exec"]
enabled_tools.docker = ["bash", "python_exec"]
enabled_tools.ssh = ["bash", "upload", "download"]

# 权限配置
["a2.ToolService".permissions]
default = "allow"

[["a2.ToolService".permissions.rules]]
tools = ["read", "glob", "grep"]
action = "allow"

[["a2.ToolService".permissions.rules]]
tools = ["bash"]
action = "ask"
patterns = ["rm -rf *", "rm -r /", "git push --force", "git reset --hard", "DROP TABLE *"]

[["a2.ToolService".permissions.rules]]
tools = ["read", "write", "edit"]
action = "allow"
sandbox = "."
```

## Permission（权限管理）

工具执行前的权限检查。配置驱动，字符串匹配 + 通配符。

### PermissionRule（Pydantic 模型）

```python
class PermissionRule(BaseModel):
    """单条权限规则。"""
    tools: list[str]           # 匹配的工具名，支持 "*" 通配符
    action: str                # "allow" / "deny" / "ask"
    patterns: list[str] = []   # 拒绝模式（可选，bash 命令匹配）
    sandbox: str = None        # 路径沙箱（可选，文件操作限定目录）
```

### PermissionConfig（配置模型）

```python
class PermissionConfig(BaseModel):
    """权限配置。从 TOML 加载。"""
    rules: list[PermissionRule] = []
    default: str = "allow"     # 无规则匹配时的默认行为

    def check(self, tool_name: str, params: dict) -> str:
        """检查工具权限。返回 'allow' / 'deny' / 'ask'。"""
        for rule in self.rules:
            if not self._match_tool(rule.tools, tool_name):
                continue

            # 检查拒绝模式
            if rule.patterns and tool_name == 'bash':
                cmd = params.get('command', '')
                for pattern in rule.patterns:
                    if self._match_pattern(pattern, cmd):
                        return 'deny'

            # 检查路径沙箱
            if rule.sandbox and tool_name in ('read', 'write', 'edit'):
                path = params.get('path', '')
                if not self._in_sandbox(path, rule.sandbox):
                    return 'deny'

            return rule.action

        return self.default

    def _match_tool(self, tools: list[str], name: str) -> bool:
        """工具名匹配，支持 '*' 通配符。"""
        if '*' in tools:
            return True
        return name in tools

    def _match_pattern(self, pattern: str, text: str) -> bool:
        """简单通配符匹配。'rm -rf *' 匹配 'rm -rf /tmp'。"""
        if '*' in pattern:
            prefix = pattern.split('*')[0]
            return text.startswith(prefix)
        return pattern in text

    def _in_sandbox(self, path: str, sandbox: str) -> bool:
        """路径是否在沙箱内。"""
        try:
            resolved = (Path(sandbox) / path).resolve()
            return resolved.is_relative_to(Path(sandbox).resolve())
        except (ValueError, OSError):
            return False
```

### ToolService 中的权限检查

```python
class ToolService(AppService):
    ...

    _permission: PermissionConfig = None

    async def on_start(self):
        ...
        # 加载权限配置
        perm_config = self.config.get('permissions', {})
        self._permission = PermissionConfig(**perm_config)

    async def execute(self, name: str, params: dict, env: str = None) -> str:
        """路由执行：权限检查 → 查找工具 → 确定环境 → 委托环境服务。"""
        # 权限检查
        decision = self._permission.check(name, params)
        if decision == 'deny':
            logger.warning(f"tool={name} denied: {params}")
            return f"[denied] 操作被权限规则拒绝"
        if decision == 'ask':
            confirmed = await self._ask_user(name, params)
            if not confirmed:
                return "[denied] 用户拒绝"

        # 原有逻辑
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found"
        ...

        # 审计日志
        logger.info(f"tool={name} env={env_name}")
```

## Files to Create

| File | Purpose |
|------|---------|
| `tools/__init__.py` | Exports |
| `tools/service.py` | ToolService(AppService) 门面 + 权限检查 |
| `tools/tool.py` | Tool(BaseCommand) 基类 |
| `tools/protocol.py` | TerminalProtocol + LocalProtocol + DockerProtocol + SSHProtocol |
| `tools/builtin.py` | 11 个内置工具（9 原有 + upload + download） |
| `tools/permission.py` | PermissionRule + PermissionConfig（Pydantic） |
| `tools/commands.py` | RegisterTool、ListTools 命令 |
| `env/__init__.py` | Exports |
| `env/local/__init__.py` | Exports |
| `env/local/service.py` | LocalToolService(AppService) |
| `env/docker/__init__.py` | Exports |
| `env/docker/service.py` | DockerToolService(AppService) |
| `env/ssh/__init__.py` | Exports |
| `env/ssh/service.py` | SSHToolService(AppService) |

## Dependencies

- `bollydog.models.base.BaseCommand` — Tool 基类
- `bollydog.models.service.AppService` — ToolService / 环境服务基类
- `bollydog.models.protocol.Protocol` — TerminalProtocol 基类
- `pathlib` — 文件操作（LocalProtocol 的 read_file/write_file）
- OpenAI function-calling schema format
