# 02 — 基础服务（Phase 1 叶子层）

> Phase 1 所有零依赖服务。按模块分节。

---

## 一、LLMService

### 架构

```
LLMService(AppService) ← 单一实例
└── protocol: LiteLLMProvider(Protocol) → adapter = litellm.Router
```

### LiteLLMProvider(Protocol)

```python
class LiteLLMProvider(Protocol):
    model: str = 'anthropic/claude-sonnet-4-20250514'
    fallback_models: list[str] = []
    api_keys: dict[str, str] = {}

    async def on_start(self):
        model_list = [{'model_name': self.model, 'litellm_params': {'model': self.model}}]
        for fb in self.fallback_models:
            model_list.append({'model_name': fb, 'litellm_params': {'model': fb}})
        self.adapter = litellm.Router(model_list=model_list, fallbacks=[{self.model: self.fallback_models}])

    async def chat(self, messages, model=None, tools=None, **kw) -> 'LLMResponse':
        params = {'model': model or self.model, 'messages': messages}
        if tools: params['tools'] = tools
        return await self.adapter.acompletion(**params, **kw)

    async def count_tokens(self, model: str, messages: list[dict]) -> int:
        return litellm.token_counter(model=model, messages=messages)
```

### LLMService(AppService)

```python
class LLMService(AppService):
    domain = 'a2'; alias = 'LLMService'; depends = []
    _capabilities: dict[str, str] = {}  # capability → model

    async def on_start(self):
        self._capabilities = self.config.get('capabilities', {})

    def resolve_model(self, model=None, capability=None) -> str:
        if model: return model
        if capability and capability in self._capabilities: return self._capabilities[capability]
        return self.protocol.model

    async def chat(self, messages, model=None, capability=None, tools=None, **kw):
        return await self.protocol.chat(messages, model=self.resolve_model(model, capability), tools=tools, **kw)

    async def count_tokens(self, messages, model=None) -> int:
        return await self.protocol.count_tokens(model or self.protocol.model, messages)
```

### TOML

```toml
[a2.LLMService]
module = "a2.llm.service.LLMService"
[a2.LLMService.capabilities]
fast = "deepseek/deepseek-chat"
capable = "anthropic/claude-sonnet-4-20250514"
code = "deepseek/deepseek-coder"
[a2.LLMService.protocol]
module = "a2.llm.provider.LiteLLMProvider"
model = "anthropic/claude-sonnet-4-20250514"
fallback_models = ["openai/gpt-4o"]
```

### Files

`llm/service.py`（LLMService）、`llm/provider.py`（LiteLLMProvider）

---

## 二、ToolService + EnvService

### 架构

```
ToolService(AppService) 门面
├── depends: ["a2.env.local", "a2.env.docker", "a2.env.ssh"]
├── _tools: dict[str, Tool]
├── register_tool / get_schemas / execute / check_permission

EnvService(AppService) 基类 ← TOML 配三个实例
└── protocol: TerminalProtocol
    └── execute / read_file / write_file / upload / download

Tool(BaseCommand, abstract=True) 薄包装
├── name / description / parameters (ClassVar)
├── provider / capability (LLM 需求声明)
├── cast_params / validate_params / execute / to_schema
```

### EnvService

```python
class EnvService(AppService):
    depends = []
    async def execute(self, command: str, timeout: int = 30) -> str:
        return await self.protocol.execute(command, timeout)
    async def read_file(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        return await self.protocol.read_file(path, offset, limit)
    async def write_file(self, path: str, content: str) -> str:
        return await self.protocol.write_file(path, content)
```

### TerminalProtocol 子类

| 子类 | adapter | 依赖 |
|------|---------|------|
| LocalProtocol | — | `asyncio.create_subprocess_exec` |
| DockerProtocol | container_id | `aiodocker` |
| SSHProtocol | ssh_conn | `asyncssh` |

### Tool 基类

```python
class Tool(BaseCommand, abstract=True):
    name: ClassVar[str]; description: ClassVar[str]; parameters: ClassVar[dict]
    is_destructive: ClassVar[bool] = False
    provider: ClassVar[str] = None; capability: ClassVar[str] = None

    def cast_params(self, params: dict) -> dict:
        props = self.parameters.get('properties', {})
        for k, v in list(params.items()):
            spec = props.get(k, {})
            if spec.get('type') == 'integer' and isinstance(v, str): params[k] = int(v)
            elif spec.get('type') == 'boolean' and isinstance(v, str): params[k] = v.lower() in ('true', '1')
        return params

    def validate_params(self, params: dict) -> list[str]:
        return [f"Missing: {f}" for f in self.parameters.get('required', []) if f not in params]
```

### ToolService 核心

```python
class ToolService(AppService):
    domain = 'a2'; alias = 'ToolService'
    depends = ['a2.env.local', 'a2.env.docker', 'a2.env.ssh']
    _tools: dict[str, Tool] = {}; _envs: dict[str, EnvService] = {}

    async def execute(self, name: str, params: dict, env: str = 'local') -> str:
        tool = self._tools.get(name)
        if not tool: return f"Error: Unknown tool '{name}'"
        params = tool.cast_params(params)
        errors = tool.validate_params(params)
        if errors: return f"Validation error: {'; '.join(errors)}"
        try: result = await tool.execute(**params)
        except Exception as e: result = f"Error: {e}\n[Analyze the error and try a different approach.]"
        return result[:30000]
```

### 内置工具

| 工具 | is_destructive | 环境映射 |
|------|---------------|---------|
| `read` | ✗ | env.read_file |
| `write` | ✓ | env.write_file |
| `edit` | ✓ | env.read_file + write_file |
| `bash` | ✓ | env.execute |
| `python` | ✓ | env.execute('python -c') |
| `glob` | ✗ | env.execute('find/ls') |
| `grep` | ✗ | env.execute('rg') |
| `upload`/`download` | ✓/✗ | env.upload/download |
| `ask_user` | ✗ | stdin/callback |
| `spawn_agent` | ✗ | hub.execute(SpawnAgentCommand) |

### 权限模型

```python
class PermissionConfig(BaseModel):
    allow_list: list[str] = ['*']
    block_list: list[str] = []
    require_approval: list[str] = []

    def check(self, tool_name: str) -> str:  # → 'allow'/'block'/'approve'
        if any(fnmatch(tool_name, p) for p in self.block_list): return 'block'
        if any(fnmatch(tool_name, p) for p in self.require_approval): return 'approve'
        if any(fnmatch(tool_name, p) for p in self.allow_list): return 'allow'
        return 'block'
```

### TOML

```toml
[a2.ToolService]
module = "a2.tools.service.ToolService"
[a2.env.local]
module = "a2.env.service.EnvService"
[a2.env.local.protocol]
module = "a2.tools.protocol.LocalProtocol"
[a2.env.docker]
module = "a2.env.service.EnvService"
[a2.env.docker.protocol]
module = "a2.tools.protocol.DockerProtocol"
container_image = "python:3.12-slim"
```

### Files

`tools/service.py`、`tools/tool.py`、`tools/protocol.py`（Terminal+Local/Docker/SSH）、`tools/builtin.py`（11工具）、`tools/permission.py`、`env/service.py`

---

## 三、SkillService

### 架构

```
SkillService(AppService), depends=[]
├── protocol: CacheLayer → FileProtocol
├── _skills: dict[str, Skill]
├── get_metadata_prompt() → Level 1 索引
├── get_always_skills_prompt() → 始终加载
├── load_skill(name) → Level 2 正文
├── get_matching(query) → 关键词匹配
└── save_skill(skill) → 持久化
```

### Skill 模型

```python
class Skill(BaseModel):
    name: str; description: str; tags: list[str] = []
    trigger_keywords: list[str] = []; always: bool = False
    body: str = ''; resources: dict[str, str] = {}
    usage_count: int = 0; success_count: int = 0

    def to_markdown(self) -> str:
        frontmatter = yaml.dump({'name': self.name, 'description': self.description,
                                  'tags': self.tags, 'trigger_keywords': self.trigger_keywords})
        return f"---\n{frontmatter}---\n\n{self.body}"

    @classmethod
    def from_markdown(cls, content: str) -> 'Skill': ...
```

### 三级渐进式披露

| Level | 内容 | ~Token | 触发 |
|-------|------|--------|------|
| 1 元数据 | 名 + 描述 + 标签 | ~100/技能 | 始终在 system |
| 2 正文 | 完整执行指令 | ~2K/技能 | 关键词匹配 / LLM 请求 |
| 3 资源 | 附属文档、脚本 | 可变 | 正文 @引用 |

### 结晶（Command 化）

```
任务成功 + tool_calls ≥ 3 → yield CrystallizeSkillCommand
  → yield ExtractPatternCommand → LLMService.chat(capability='fast')
  → SkillService.save_skill(skill)
  → yield NotifyIndexUpdateCommand → L1.set()
```

### TOML

```toml
[a2.SkillService]
module = "a2.skills.service.SkillService"
skills_dir = ".user/skills/"
max_skills = 20
[a2.SkillService.protocol]
module = "bollydog.adapters.composite.CacheLayer"
[a2.SkillService.protocol.protocol]
module = "bollydog.adapters.file.LocalFileProtocol"
path = ".user/skills/"
```

### Files

`skills/service.py`、`skills/skill.py`、`skills/commands.py`

---

## 四、PlannerService

### 架构

```
PlannerService(AppService), depends=["a2.LLMService"]
├── protocol: LocalFileProtocol → TaskList 持久化
├── use_planning / use_reflexion (bool, TOML 配)

Command 三层（通过 yield 组合）:
├── ReActStepCommand  ← Layer 1: 单步 Reason→Act→Observe
├── PlanCommand       ← Layer 2: 生成 TaskList（可选）
└── ReflexionCommand  ← Layer 3: 失败反思重试（可选）
```

### Task / TaskList

```python
class Task(BaseModel):
    id: str; content: str = ""
    status: Literal["pending", "in_progress", "done", "failed"] = "pending"
    result: str | None = None; context: str = ""

    def with_reflection(self, reflection: str) -> "Task":
        return Task(id=self.id, content=self.content, status="pending",
                    context=f"{self.context}\n[Reflection]: {reflection}".strip())

class TaskList(BaseModel):
    run_id: str; goal: str = ""; tasks: list[Task] = []
    def pending(self) -> list[Task]: return [t for t in self.tasks if t.status == "pending"]
    def render_progress(self) -> str: ...
```

### Command 层

- **ReActStepCommand**：循环 `reason → act → observe`，`max_steps=10`
- **PlanCommand**：LLM 分解 goal → TaskList，持久化到 Session + Protocol
- **ReflexionCommand**：`yield ReActStepCommand`，失败 → LLM 反思 → `task.with_reflection()` → 重试，`max_retries=3`

### TOML

```toml
[a2.PlannerService]
module = "a2.planner.service.PlannerService"
use_planning = false
use_reflexion = false
[a2.PlannerService.protocol]
module = "bollydog.adapters.file.LocalFileProtocol"
path = ".agent/tasks/"
```

### Files

`planner/service.py`、`planner/models.py`、`planner/commands.py`

---

## 五、AgentRegistryService

### 架构

```
AgentRegistryService(AppService), depends=[]
├── protocol: KVProtocol → 存储 {name → RoleDef}
├── get(name) / list() / register(role_def)
└── get_available_description() → Orchestrator 可用角色列表
```

### RoleDef

```python
class RoleDef(BaseModel):
    name: str; system_prompt: str
    tools: list[str] = ['*']; model: str | None = None
    max_turns: int = 50; can_spawn: bool = False
    skills_dir: str | None = None
    memory_namespace: str | None = None  # 默认 = name
    use_planning: bool = False; use_reflexion: bool = False
    permissions: PermissionConfig | None = None
```

### SpawnAgentCommand

```python
class SpawnAgentCommand(BaseCommand):
    alias = 'SpawnAgent'; role: str; input: dict = {}

    async def __call__(self):
        registry = AppService._apps['a2.AgentRegistryService']
        role_def = await registry.get(self.role)
        depth = (await session.get('agent_depth')) or 0
        if depth >= 2: return {"success": False, "error": "Max depth"}
        if not role_def.can_spawn and depth > 0:
            return {"success": False, "error": f"'{self.role}' cannot spawn"}
        await session.set('agent_depth', depth + 1)
        result = yield AgentCommand(role_def=role_def, goal=str(self.input.get('task', '')),
                                     session_namespace=f"{role_def.effective_namespace}/{message.trace_id}")
        await session.set('agent_depth', depth)
        return result
```

### 三种编排模式

- **Pipeline**（串行）：顺序 `yield SpawnAgentCommand()`
- **Fan-out**（并行）：`yield [SpawnAgentCommand(...), ...]`
- **Reactive**（v2 事件驱动）：`exchange.publish/subscribe`

### 防递归

| 机制 | 说明 |
|------|------|
| `can_spawn=False`（默认） | 只有 Orchestrator 允许 spawn |
| `agent_depth` | Session 传递，硬限制 ≤ 2 |
| 工具过滤 | 非 can_spawn 角色不含 spawn_agent |

### TOML

```toml
[a2.AgentRegistryService]
module = "a2.registry.service.AgentRegistryService"
[a2.AgentRegistryService.protocol]
module = "bollydog.adapters.kv.KVProtocol"
[a2.AgentRegistryService.roles.default]
system_prompt = "You are a helpful AI assistant."
tools = ["*"]
[a2.AgentRegistryService.roles.explore]
system_prompt = "You are a fast code search agent. READ-ONLY."
tools = ["read", "glob", "grep"]
model = "deepseek/deepseek-chat"
[a2.AgentRegistryService.roles.orchestrator]
system_prompt = "你是任务协调者..."
tools = ["spawn_agent", "ask_user"]
can_spawn = true
```

### Files

`registry/service.py`、`registry/role_def.py`、`registry/commands.py`
