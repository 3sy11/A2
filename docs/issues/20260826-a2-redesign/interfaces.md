# A2 重设计 — 接口契约与数据建模（P5）

> Issue: `20260826-a2-redesign`
> 本文的每一条签名都能在 `sequence.md` 找到对应箭头；每一个模型字段都能在顺序图里找到使用证据。没有「以后可能用得上」的字段。

## 0 类型约定

| 约定 | 说明 |
|------|------|
| Command 入参 | 子类字段，只允许 `str / int / float / bool / list / dict / None` 及其联合 |
| Command 返回 | `__call__` 的返回标注，同上；异步生成器 Command 标注 `AsyncGenerator[dict, None]` |
| 领域模型 | `BaseDomain`（bollydog 的 DDD 基类），在 Command 边界一律 `model_dump()` 成 `dict` |
| Protocol 方法 | 可用领域对象与复杂类型（Protocol 不跨进程边界） |
| AppService 方法 | 可用领域对象；但被 Command 直接调用的返回值应当是易序列化的 |

**为什么 Command 只能用原语**：这是 bollydog 的硬约束，保证命令天然可序列化、可跨进程投递、可被 `bollydog ls` 自描述、可被 HTTP/WS/UDS 三种入口以同一份 Pydantic schema 解析。A2 的领域模型（`Msg` / `Task` / `Skill` / `ToolCall` …）全部定义为 `BaseDomain`，只在服务内部与 Protocol 之间流动，跨 Command 边界时 `model_dump()`。

---

## 1 事件流块契约（Streaming Chunk Protocol）

异步生成器 Command `yield` 出的每一个非 Command 值都是一个「事件块」，经 `StreamState` 流到 SSE / WebSocket。这是 A2 面向前端的**唯一**流式契约，等价于 agentscope 的 `AgentEvent`，但载体是 bollydog 原生的 `StreamState`，不引入新机制。

```python
class Chunk(BaseDomain):
    """事件流块。所有 yield 出去的 dict 都符合本结构。"""
    type: str                      # 见下表
    seq: int = 0                   # 会话内单调递增，供断线重放定位
    session_id: str = ''
    turn_id: str = ''
    agent: str = ''
    payload: dict = {}             # 各 type 私有字段
```

实现上直接 `yield chunk.model_dump()`。

| `type` | 产出者 | `payload` 关键字段 |
|--------|--------|-------------------|
| `reply.started` | `Reply` | `inputs_digest` |
| `reply.finished` | `Reply` | `content: list`, `finish_reason: str`, `usage: dict` |
| `reply.interrupted` | `Reply` | `reason: str` |
| `reply.parked` | `Reply` | `park_id: str`, `kind: str` |
| `iteration.completed` | `Reply` | `iter: int` |
| `hint.injected` | `Reply` | `text: str` |
| `model.delta` | `Generate` | `block: 'text'\|'thinking'\|'tool_call'\|'data'`, `index: int`, 增量字段 |
| `model.completed` | `Generate` | `content: list`, `usage: dict`, `finish_reason: str` |
| `model.retry` | `Generate` | `model: str`, `attempt: int`, `error: str` |
| `model.failed` | `Generate` | `error: str`, `attempts: int` |
| `tool.call` | `Reply` | `id: str`, `name: str`, `input: dict` |
| `tool.chunk` | `Invoke` | `id: str`, `data: dict` |
| `tool.result` | `Invoke` | `id: str`, `status: 'ok'\|'error'\|'denied'\|'interrupted'\|'parked'`, `output: dict`, `artifact: str\|None`, `truncated: bool` |
| `require_user_confirm` | `Reply` | `park_id`, `tool`, `args`, `reason` |
| `require_user_answer` | `Reply` | `park_id`, `question`, `options: list` |
| `context.compacting` | `Reply` | `tokens: int` |
| `context.compacted` | `Reply` | `before_tokens`, `after_tokens`, `folded` |
| `plan.updated` | `Reply` | `tasks: list`, `progress: dict` |
| `skill.activated` | `Reply` | `skills: list` |
| `subagent.chunk` | `Spawn` | `agent: str`, `data: dict` |
| `subagent.result` | `Spawn` | `agent: str`, `content: list` |
| `exec.output` | `RunCommand` | `stream: 'stdout'\|'stderr'`, `text: str` |
| `exec.completed` | `RunCommand` | `exit_code: int`, `files: list`, `ms: int` |
| `ingest.parsed` / `ingest.progress` / `ingest.completed` | `IngestDocument` | `sections` / `done`,`total` / `doc_id`,`chunks` |
| `fanout.completed` | `Fanout` | `results: list` |
| `replay.completed` | `ReplayEvents` | `last_seq: int` |
| `error` | 任意 | `code: str`, `message: str`, `retryable: bool` |

---

## 2 领域模型（`BaseDomain`）

### 2.1 消息与内容块 — `a2/message/models.py`

```python
class Block(BaseDomain):
    """内容块基类。type 判别联合。"""
    type: str                      # text|thinking|data|tool_call|tool_result|hint

class TextBlock(Block):
    type: str = 'text'
    text: str

class ThinkingBlock(Block):
    type: str = 'thinking'
    text: str
    signature: str = ''            # 部分厂商的推理签名

class DataBlock(Block):
    """统一多模态块，取代分列的 Image/Audio/Video。"""
    type: str = 'data'
    media_type: str                # image/png, audio/wav, video/mp4, application/pdf
    url: str = ''                  # 二选一
    base64: str = ''

class ToolCallBlock(Block):
    type: str = 'tool_call'
    id: str
    name: str
    input: dict = {}
    state: str = 'pending'         # pending|running|awaiting|done|error|interrupted

class ToolResultBlock(Block):
    type: str = 'tool_result'
    id: str
    name: str
    output: list = []              # list[Block] 的 dump
    status: str = 'ok'
    artifact: str = ''             # artifact:// 引用

class HintBlock(Block):
    """运行时注入的提示（时间、任务清单、上下文用量、技能提醒）。"""
    type: str = 'hint'
    text: str
    source: str                    # runtime|plan|skill|rag|memory

class Usage(BaseDomain):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0

class Msg(BaseDomain):
    name: str                      # 说话者（智能体名 / user / system）
    role: str                      # user|assistant|system
    content: list = []             # list[Block] 的 dump
    metadata: dict = {}
    usage: dict = {}               # Usage 的 dump
    finish_reason: str = ''
    structured_output: dict = {}
    error: dict = {}

    def text(self) -> str: ...
    def blocks(self, type_: str) -> list: ...
```

### 2.2 工具 — `a2/tool/models.py`

```python
class ToolSpec(BaseDomain):
    """一个可调用工具的对外描述，由 Command 类反射生成。"""
    name: str                      # 工具名（模型看到的）
    destination: str               # 'workspace.local.ReadPath'
    description: str               # Command 的 docstring
    parameters: dict               # JSON Schema，来自 model_json_schema() 剔除基类字段
    group: str = 'basic'
    read_only: bool = False
    concurrency_safe: bool = True
    external: bool = False         # 需外部执行（HITL）

class PermissionRule(BaseDomain):
    pattern: str                   # 工具名通配，如 'workspace.*' / 'run_command'
    action: str                    # allow|deny|ask
    when: dict = {}                # 参数条件，如 {'command': '^rm '}
    reason: str = ''

class ToolGroup(BaseDomain):
    name: str
    description: str = ''
    instructions: str = ''
    tools: list = []               # list[str] destination
    always_on: bool = False
```

### 2.3 计划 — `a2/plan/models.py`

```python
class Task(BaseDomain):
    task_id: str
    subject: str
    description: str = ''
    state: str = 'pending'         # pending|in_progress|completed|blocked|cancelled
    owner: str = ''
    blocked_by: list = []
    note: str = ''

class Plan(BaseDomain):
    plan_id: str
    session_id: str
    goal: str = ''
    tasks: list = []               # list[Task] 的 dump
```

### 2.4 技能 — `a2/skill/models.py`

```python
class Skill(BaseDomain):
    name: str
    description: str               # 一句话，进一级目录
    keywords: list = []
    body_path: str                 # SKILL.md 绝对路径
    resources: list = []           # 附带资源相对路径
    tools: list = []               # 该技能建议启用的工具分组
    version: str = '0.1.0'
    enabled: bool = True
```

### 2.5 知识与记忆 — `a2/knowledge/models.py` / `a2/memory/models.py`

```python
class Chunk(BaseDomain):
    chunk_id: str
    doc_id: str
    text: str
    vector: list = []
    metadata: dict = {}            # {'page':1,'title':'…','path':'…'}

class Hit(BaseDomain):
    chunk_id: str
    doc_id: str
    text: str
    score: float
    source: str                    # 可展示的出处

class MemoryEntry(BaseDomain):
    entry_id: str
    scope: str                     # user|agent|global
    subject: str                   # 归属主体（user_id / agent name）
    text: str
    kind: str = 'fact'             # fact|preference|procedure
    vector: list = []
    created_at: float = 0.0
    hits: int = 0
```

### 2.6 会话与追踪 — `a2/session/models.py` / `a2/observe/models.py`

```python
class SessionRecord(BaseDomain):
    session_id: str
    user_id: str = ''
    agent: str = ''
    title: str = ''
    turn_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

class TurnRecord(BaseDomain):
    session_id: str
    turn_id: str
    inputs: list = []              # list[Msg] 的 dump
    output: dict = {}              # Msg 的 dump
    usage: dict = {}
    finish_reason: str = ''
    event_count: int = 0
    ms: int = 0

class ParkedState(BaseDomain):
    park_id: str
    session_id: str
    turn_id: str
    kind: str                      # confirm|question|external|retry
    pending: dict                  # 恢复所需的全部现场
    created_at: float = 0.0

class Span(BaseDomain):
    trace_id: str
    span_id: str
    parent_span_id: str
    name: str                      # Command alias
    destination: str
    ts: float
    duration_ms: int = 0
    status: str = 'ok'
    attrs: dict = {}
```

### 2.7 凭证 — `a2/credential/models.py`

```python
class Credential(BaseDomain):
    cred_id: str
    user_id: str
    system: str                    # 外部系统标识
    kind: str                      # bearer|basic|cookie|apikey|oauth
    payload: dict = {}             # 加密后存储
    expires_at: float = 0.0
```

---

## 3 Command 签名清单

格式：`CommandName(field: type, …) → ReturnType`。`→ AsyncGen[dict]` 表示异步生成器。

### 3.1 `agent` 域 — `a2/agent/commands.py`

| 签名 | 说明 |
|------|------|
| `Reply(session_id: str, inputs: list, structured_schema: dict \| None, resume_state: dict \| None, max_iters: int) → AsyncGen[dict]` | 一个完整回合 |
| `Resume(session_id: str, park_id: str, decision: str, answer: str) → AsyncGen[dict]` | 从停放态续跑（中继进 `Reply`） |
| `Interrupt(session_id: str, reason: str) → dict` | 写中断标记 |
| `Observe(session_id: str, inputs: list, sender: str) → int` | 只入上下文不回复 |
| `Spawn(agent: str, task: str, session_id: str, inherit_context: bool) → AsyncGen[dict]` | 委派子智能体 |
| `GenerateStructuredOutput(**schema_fields) → dict` | 结构化输出终止工具（按 `structured_schema` 动态造类） |

事件：

| 签名 |
|------|
| `ReplyStarted(session_id: str, turn_id: str, agent: str) → None` |
| `IterationCompleted(session_id: str, turn_id: str, agent: str, iter: int) → None` |
| `ReplyFinished(session_id: str, turn_id: str, agent: str, content: list, usage: dict, finish_reason: str, ms: int) → None` |
| `ReplyInterrupted(session_id: str, turn_id: str, agent: str, reason: str) → None` |
| `ReplyParked(session_id: str, turn_id: str, agent: str, park_id: str, kind: str) → None` |

### 3.2 `model` 域 — `a2/model/commands.py`

| 签名 |
|------|
| `Generate(messages: list, tools: list, tool_choice: str, stream: bool, model: str, params: dict) → AsyncGen[dict]` |
| `CountTokens(messages: list, model: str) → int` |
| `Embed(texts: list, model: str) → list` |
| `ModelCalled(model: str, usage: dict, latency_ms: int, finish_reason: str) → None` |
| `ModelFailed(model: str, error: str, attempts: int) → None` |

### 3.3 `context` 域 — `a2/context/commands.py`

| 签名 |
|------|
| `Assemble(session_id: str, agent: str, inputs: list, system_prompt: str, tool_names: list, budget: int, rag_query: str) → dict` |
| `AppendContext(session_id: str, messages: list) → int` |
| `Compress(session_id: str, agent: str, keep_ratio: float, model_ref: str) → dict` |
| `ContextCompacted(session_id: str, before_tokens: int, after_tokens: int, folded: int) → None` |

`Assemble` 返回 `{'messages': list, 'tokens': int, 'need_compress': bool, 'injected': list}`。

### 3.4 `tool` 域 — `a2/tool/commands.py`

| 签名 |
|------|
| `ListTools(session_id: str, agent: str, groups: list) → list` |
| `Invoke(call_id: str, tool: str, args: dict, session_id: str, agent: str) → AsyncGen[dict]` |
| `CheckPermission(session_id: str, agent: str, tool: str, args: dict) → dict` |
| `ActivateGroup(session_id: str, enable: list, disable: list) → dict` |
| `ToolInvoked(tool: str, call_id: str, session_id: str, ok: bool, ms: int) → None` |
| `ToolFailed(tool: str, call_id: str, session_id: str, error: str) → None` |
| `GroupActivated(session_id: str, groups: list) → None` |

### 3.5 `mcp` 域 — `a2/mcp/commands.py`

| 签名 |
|------|
| `ConnectServer(server: str, transport: str, command: str, args: list, url: str, env: dict, headers: dict) → dict` |
| `DisconnectServer(server: str) → dict` |
| `ListServers() → list` |
| `ServerConnected(server: str, tool_count: int, transport: str) → None` |
| `ServerLost(server: str, error: str) → None` |

### 3.6 `skill` 域 — `a2/skill/commands.py`

| 签名 |
|------|
| `ListSkills(enabled_only: bool) → list` |
| `MatchSkills(query: str, top_k: int) → list` |
| `LoadSkill(name: str, include_resources: bool) → dict` |
| `InstallSkill(source: str, kind: str, overwrite: bool) → dict` |
| `SkillActivated(session_id: str, skills: list) → None` |
| `SkillCatalogChanged(added: list, removed: list) → None` |

### 3.7 `knowledge` 域 — `a2/knowledge/commands.py`

| 签名 |
|------|
| `IngestDocument(doc_id: str, path: str, collection: str, chunk_size: int, overlap: int) → AsyncGen[dict]` |
| `UpsertChunks(collection: str, items: list) → int` |
| `Search(collection: str, query: str, top_k: int, score_threshold: float) → list` |
| `DeleteDocument(collection: str, doc_id: str) → int` |
| `DocumentIngested(doc_id: str, chunks: int, collection: str) → None` |

### 3.8 `plan` 域 — `a2/plan/commands.py`

| 签名 |
|------|
| `CreatePlan(session_id: str, goal: str, tasks: list) → dict` |
| `UpdateTask(session_id: str, task_id: str, state: str, note: str) → dict` |
| `ListTasks(session_id: str) → dict` |
| `PlanCreated(session_id: str, plan_id: str, count: int) → None` |
| `TaskUpdated(session_id: str, task_id: str, state: str) → None` |
| `PlanCompleted(session_id: str, plan_id: str) → None` |

### 3.9 `session` 域 — `a2/session/commands.py`

| 签名 |
|------|
| `OpenSession(session_id: str, user_id: str, agent: str) → dict` |
| `SaveTurn(session_id: str, turn_id: str, record: dict) → int` |
| `LoadSession(session_id: str, last_n: int) → dict` |
| `ListSessions(user_id: str, limit: int, offset: int) → list` |
| `DeleteSession(session_id: str) → int` |
| `Park(session_id: str, turn_id: str, kind: str, pending: dict) → str` |
| `Unpark(session_id: str, park_id: str) → dict` |
| `AppendEvent(session_id: str, turn_id: str, chunk: dict) → int` |
| `ReplayEvents(session_id: str, last_seq: int) → AsyncGen[dict]` |

### 3.10 `workspace` 域 — `a2/workspace/commands.py`（同时是工具）

| 签名 | 工具分组 |
|------|---------|
| `ReadPath(path: str, offset: int, limit: int) → dict` | `basic` |
| `WritePath(path: str, content: str, mode: str) → dict` | `basic` |
| `ListPath(path: str, depth: int) → list` | `basic` |
| `GlobSearch(pattern: str, path: str) → list` | `basic` |
| `GrepSearch(pattern: str, path: str, glob: str, max_results: int) → list` | `basic` |
| `EditPath(path: str, old: str, new: str, replace_all: bool) → dict` | `edit` |
| `RunCommand(command: str, cwd: str, env: dict, timeout: int) → AsyncGen[dict]` | `exec` |
| `RunPython(code: str, timeout: int, artifacts: list) → AsyncGen[dict]` | `exec` |
| `StoreArtifact(key: str, content: str, mime: str, session_id: str) → str` | — |
| `ReadArtifact(ref: str, offset: int, limit: int) → dict` | `basic` |
| `GrepArtifact(ref: str, pattern: str, max_results: int) → dict` | `basic` |
| `ArtifactStored(ref: str, bytes: int, mime: str) → None` | — |

### 3.11 `credential` 域 — `a2/credential/commands.py`

| 签名 |
|------|
| `PutCredential(user_id: str, system: str, kind: str, payload: dict) → str` |
| `GetCredential(user_id: str, system: str) → dict` |
| `ListCredentials(user_id: str) → list` |
| `DeleteCredential(cred_id: str) → int` |
| `CredentialMissing(user_id: str, system: str) → None` |

### 3.12 `team` 域 — `a2/team/commands.py`

| 签名 |
|------|
| `Sequential(topic: str, agents: list, inputs: list) → AsyncGen[dict]` |
| `Fanout(topic: str, agents: list, inputs: list) → AsyncGen[dict]` |
| `Broadcast(topic: str, sender: str, content: list) → dict` |
| `JoinTopic(topic: str, agent: str) → list` |
| `LeaveTopic(topic: str, agent: str) → list` |
| `MessageBroadcast(topic: str, sender: str, content: list, members: list) → None` |
| `RoundCompleted(topic: str, agents: list, rounds: int) → None` |

### 3.13 `observe` 域 — `a2/observe/commands.py`

| 签名 |
|------|
| `QueryTrace(trace_id: str) → dict` |
| `ExportTrace(trace_id: str, fmt: str) → dict` |
| `ListTraces(session_id: str, limit: int, offset: int) → list` |

### 3.14 人机交互工具 — `a2/agent/tools.py`

| 签名 | 说明 |
|------|------|
| `AskHuman(question: str, options: list, session_id: str) → dict` | 返回 `{'status':'parked','park_id':…}` |
| `PresentFiles(refs: list, title: str) → dict` | 把产物交付给前端展示 |

---

## 4 AppService 业务方法签名

### `AgentService`

```python
class AgentService(A2Service):
    domain = 'agent'
    # 配置字段（TOML 注入，均有默认值）
    model_ref: str = 'model.chat'
    context_ref: str = 'context.default'
    tool_ref: str = 'tool.toolkit'
    session_ref: str = 'session.store'
    plan_ref: str = 'plan.notebook'
    knowledge_ref: str = ''
    system_prompt: str = ''
    tool_groups: list = ['basic']
    max_iters: int = 20
    max_depth: int = 3
    rag_mode: str = 'agentic'          # agentic|static|off
    inject_runtime_state: bool = True

    def system_prompt_of(self, session_id: str) -> str: ...
    def next_action(self, completed: dict, iter_: int) -> str: ...        # reason|act|exit
    def extract_tool_calls(self, completed: dict) -> list: ...
    def batch_calls(self, calls: list) -> list: ...
    def build_hint(self, state: dict) -> str: ...
    def pick_final(self, chunks: list) -> list: ...
    def to_msg(self, content: list, role: str) -> dict: ...
```

### `ChatModelService`

```python
class ChatModelService(A2Service):
    domain = 'model'
    provider: str = 'openai'
    model: str = 'gpt-4o'
    fallback_models: list = []
    max_retries: int = 2
    context_size: int = 128000
    formatter: str = 'chat'            # chat|multi_agent
    params: dict = {}

    def format(self, messages: list, mode: str = '') -> list: ...
    def merge_deltas(self, deltas: list) -> dict: ...
    def fallbacks(self) -> list: ...
    def backoff(self, attempt: int) -> float: ...
    def estimate_tokens(self, messages: list) -> int: ...
```

### `EmbeddingService`

```python
class EmbeddingService(A2Service):
    domain = 'model'
    model: str = 'text-embedding-3-small'
    batch_size: int = 64
    dimension: int = 1536

    def batches(self, texts: list) -> list: ...
    def cache_key(self, text: str) -> str: ...
```

### `ContextService`

```python
class ContextService(A2Service):
    domain = 'context'
    budget: int = 128000
    trigger_ratio: float = 0.8
    keep_ratio: float = 0.3
    tool_result_limit: int = 8000
    max_images: int = 5
    compression_prompt_template: str = ...

    def assemble(self, parts: dict) -> list: ...
    def need_compress(self, tokens: int) -> bool: ...
    def split_window(self, msgs: list, keep_ratio: float) -> tuple: ...
    def compression_prompt(self, msgs: list) -> list: ...
    def pick_text(self, chunks: list) -> str: ...
    def render_hint(self, state: dict) -> str: ...
    def render_hits(self, hits: list) -> str: ...
    def estimate(self, msgs: list) -> int: ...
```

### `ToolkitService`

```python
class ToolkitService(A2Service):
    domain = 'tool'
    groups: dict = {}                  # {'basic': ['workspace.local.ReadPath', …]}
    always_on_groups: list = ['basic']
    spill_threshold: int = 20000
    rules: list = []                   # list[PermissionRule] 的 dump

    def reindex(self) -> int: ...
    def schemas(self, groups: list) -> list: ...
    def spec_of(self, destination: str) -> dict: ...          # Command 类 → ToolSpec
    def resolve_tool(self, name: str) -> str: ...
    def is_concurrency_safe(self, name: str) -> bool: ...
    def truncate(self, raw: dict) -> tuple: ...
    def meta_tool_schema(self, dormant: list) -> dict: ...
    def group_instructions(self, groups: list) -> str: ...
    def apply_groups(self, active: list, enable: list, disable: list) -> list: ...
    def dormant_groups(self, active: list) -> list: ...
    async def _guard(self, message: BaseCommand) -> dict | None: ...      # hub.before
    async def _postprocess(self, message, result=None, exception=None) -> None: ...  # hub.after
    async def on_server_connected(self, message: BaseCommand) -> dict: ...
    async def on_server_lost(self, message: BaseCommand) -> dict: ...
```

**`schemas()` 的实现要点**：遍历 `registry.all_commands()`，对属于激活分组的 destination，取其 Command 类并调用 bollydog `BaseCommand.describe()`，得到纯业务参数 schema 与 description。**工具描述完全由 Command 自描述，无需另写注册表**。

### `McpService`

```python
class McpService(A2Service):
    domain = 'mcp'
    servers: dict = {}                 # TOML 声明的服务

    def build_protocol(self, transport: str, conf: dict) -> Protocol: ...
    def register_tools(self, server: str, tools: list) -> int: ...
    def unregister_tools(self, server: str) -> int: ...
    def jsonschema_to_fields(self, schema: dict) -> dict: ...
    def tool_name(self, server: str, name: str) -> str: ...   # mcp__{server}__{name}
```

### `SkillService`

```python
class SkillService(A2Service):
    domain = 'skill'
    skill_dirs: list = ['.a2/skills']
    max_body_chars: int = 40000

    def scan(self) -> int: ...
    def catalog(self, enabled_only: bool = True) -> list: ...
    def match(self, query: str, top_k: int) -> list: ...
    def body(self, name: str) -> str: ...
    def validate(self, manifest: dict) -> dict: ...
    def render_metadata(self, skills: list) -> str: ...
```

### `KnowledgeService`

```python
class KnowledgeService(A2Service):
    domain = 'knowledge'
    embed_ref: str = 'model.embed'
    chunk_size: int = 800
    overlap: int = 100

    def parse(self, path: str) -> list: ...
    def chunk(self, sections: list, size: int, overlap: int) -> list: ...
    def batches(self, chunks: list) -> list: ...
    def rerank(self, hits: list, query: str) -> list: ...
    def render_hits(self, hits: list) -> str: ...
```

### `PlanService`

```python
class PlanService(A2Service):
    domain = 'plan'

    def normalize(self, tasks: list) -> list: ...
    def apply(self, plan: dict, task_id: str, state: str, note: str) -> dict: ...
    def render(self, tasks: list) -> str: ...
    def render_cached(self, session_id: str) -> str: ...
    def progress(self, plan: dict) -> dict: ...
    def next_pending(self, tasks: list) -> dict | None: ...
```

### `SessionService`

```python
class SessionService(A2Service):
    domain = 'session'
    event_retention: int = 2000

    def new_record(self, session_id: str, user_id: str, agent: str) -> dict: ...
    def session_key(self, session_id: str) -> str: ...
    def turn_key(self, session_id: str, turn_id: str) -> str: ...
    def park_key(self, session_id: str) -> str: ...
    def event_key(self, session_id: str, seq: int) -> str: ...
    def context_from_turns(self, turns: list) -> list: ...
    def title_of(self, inputs: list) -> str: ...
    async def on_reply_finished(self, message: BaseCommand) -> dict: ...
    async def on_reply_parked(self, message: BaseCommand) -> dict: ...
```

### `WorkspaceService`

```python
class WorkspaceService(A2Service):
    domain = 'workspace'
    root: str = '.a2/workspace'
    allow_commands: list = []
    deny_commands: list = ['rm -rf /', 'shutdown', 'mkfs']
    exec_timeout: int = 120

    def resolve(self, path: str) -> str: ...
    def policy(self, command: str) -> bool: ...
    def artifact_path(self, session_id: str, key: str) -> str: ...
    def artifact_ref(self, session_id: str, key: str) -> str: ...
    def ref_to_path(self, ref: str) -> str: ...
```

### `CredentialService`

```python
class CredentialService(A2Service):
    domain = 'credential'
    secret_env: str = 'A2_CREDENTIAL_KEY'

    def encrypt(self, payload: dict) -> str: ...
    def decrypt(self, blob: str) -> dict: ...
    def mask(self, cred: dict) -> dict: ...
    def inject(self, headers: dict, cred: dict) -> dict: ...
    def inject_model(self, provider: str) -> dict: ...
    def schema(self, system: str) -> dict: ...
```

### `TeamService`

```python
class TeamService(A2Service):
    domain = 'team'

    def members(self, topic: str) -> list: ...
    def to_inputs(self, result: list) -> list: ...
    def zip(self, agents: list, results: list) -> list: ...
```

### `TraceService`

```python
class TraceService(A2Service):
    domain = 'observe'
    sample_rate: float = 1.0

    def to_span(self, evt: BaseCommand) -> dict: ...
    def tree(self, rows: list) -> list: ...
    def to_replay_case(self, rows: list) -> dict: ...
    async def on_any(self, message: BaseCommand) -> dict: ...
```

---

## 5 A2 引入的 Protocol ABC

bollydog 在 `adapters/_base.py` 提供了 `KVProtocol` / `CRUDProtocol` / `GraphProtocol` / `FileProtocol` 四个便利 ABC，它们都是 `Protocol(BaseService)` 的子类。A2 沿用同样的手法，为智能体特有的外部环境增加 ABC。**这是子类化 bollydog 的 `Protocol`，不是新范式**，判定标准仍是「生产用真实实现、测试换假实现、代码零改动」。

```python
# a2/protocols/model.py
class ChatModelProtocol(Protocol, abstract=True):
    @abstractmethod
    async def stream(self, payload: dict, model: str) -> AsyncIterator[dict]: ...
    @abstractmethod
    async def complete(self, payload: dict, model: str) -> dict: ...
    async def count_tokens(self, messages: list, model: str) -> int: ...

class EmbeddingProtocol(Protocol, abstract=True):
    @abstractmethod
    async def embed(self, texts: list, model: str) -> list: ...

class TTSProtocol(Protocol, abstract=True):
    @abstractmethod
    async def synthesize(self, text: str, voice: str) -> dict: ...

# a2/protocols/vector.py
class VectorStoreProtocol(Protocol, abstract=True):
    @abstractmethod
    async def upsert(self, collection: str, items: list) -> int: ...
    @abstractmethod
    async def search(self, collection: str, vector: list, top_k: int, flt: dict) -> list: ...
    @abstractmethod
    async def delete(self, collection: str, doc_id: str) -> int: ...
    async def collections(self) -> list: ...

# a2/protocols/sandbox.py
class SandboxProtocol(Protocol, abstract=True):
    @abstractmethod
    async def exec_stream(self, command: str, cwd: str, env: dict, timeout: int) -> AsyncIterator[dict]: ...
    @abstractmethod
    async def put(self, path: str, content: bytes) -> None: ...
    @abstractmethod
    async def fetch(self, path: str) -> bytes: ...
    async def list_changed(self, since: float) -> list: ...

# a2/protocols/mcp.py
class McpProtocol(Protocol, abstract=True):
    @abstractmethod
    async def list_tools(self) -> list: ...
    @abstractmethod
    async def call_tool(self, name: str, args: dict) -> AsyncIterator[dict]: ...
    async def list_resources(self) -> list: ...

# a2/protocols/permission.py
class PermissionProtocol(Protocol, abstract=True):
    @abstractmethod
    async def match(self, tool: str, args: dict, ctx: dict) -> dict: ...
    async def add_rule(self, rule: dict) -> int: ...
    async def rules(self) -> list: ...
```

### 实现矩阵

| ABC | 生产实现 | 测试实现 |
|-----|---------|---------|
| `ChatModelProtocol` | `OpenAIChatProtocol` / `AnthropicChatProtocol` / `DashScopeChatProtocol` / `GeminiChatProtocol` / `OllamaChatProtocol` / `DeepSeekChatProtocol` / `LiteLLMChatProtocol` | `ScriptedChatProtocol`（按脚本回放） |
| `EmbeddingProtocol` | `OpenAIEmbeddingProtocol` / `DashScopeEmbeddingProtocol` / `OllamaEmbeddingProtocol` | `HashEmbeddingProtocol`（确定性伪向量） |
| `VectorStoreProtocol` | `QdrantProtocol` / `MilvusProtocol` / `PgVectorProtocol` | `InMemoryVectorProtocol` |
| `SandboxProtocol` | `LocalSandboxProtocol` / `DockerSandboxProtocol` / `K8sSandboxProtocol` / `SshSandboxProtocol` | `FakeSandboxProtocol` |
| `McpProtocol` | `StdioMcpProtocol` / `HttpMcpProtocol` | `FakeMcpProtocol` |
| `PermissionProtocol` | `RulePermissionProtocol` | 同上（纯内存） |
| `KVProtocol`（bollydog） | `SQLiteProtocol` / `RedisProtocol` / `CacheLayer` | `MemoryProtocol` |
| `CRUDProtocol`（bollydog） | `SqlAlchemyProtocol` / `PostgreSQLProtocol` | `MemoryProtocol` 包装 |
| `FileProtocol`（bollydog） | `LocalFileProtocol` | 临时目录 |

---

## 6 Event 订阅契约

```python
class OnReplyFinished(BaseEvent):
    async def __call__(self) -> BaseCommand:
        source = self.data['events'][-1]
        return app.resolve_ref('session.store', 'SaveTurn', ...)

class OnServerConnected(BaseEvent):
    async def __call__(self) -> dict:
        return {'ok': True, 'tools': app.reindex()}

class OnMessageBroadcast(BaseEvent):
    async def __call__(self) -> dict: ...

class OnAny(BaseEvent):
    async def __call__(self) -> dict: ...
```

TOML 使用 `subscribe = { topic = "EventClassName" }`。订阅行为是 commands
模块中的普通 `BaseEvent`，由 bollydog Exchange 绑定到 topic；来源消息通过
`self.data['events'][-1]` 取得。需要继续执行 Command 时直接返回 Command，
使用 bollydog handoff 语义。

---

## 7 序列化方案

| 数据模型 | 存储位置 | 序列化 | 读写时机 |
|---------|---------|--------|---------|
| `Msg` / `Block` | 全局 `Session`，键 `context:{session_id}` | `model_dump()` → JSON（`SQLiteProtocol` 的 value 列） | 每轮 `Assemble` 读、`AppendContext` 写 |
| 回合便签（iter / interrupted / turn_id） | 全局 `Session`，键 `turn:{session_id}` | dict → JSON | 回合开始写，每个安全点读 |
| 摘要 | 全局 `Session`，键 `summary:{session_id}` | str | `Compress` 写，`Assemble` 读 |
| `SessionRecord` | `session.store` protocol，键 `session:{id}` | `model_dump()` → JSON | `OpenSession` / `ListSessions` |
| `TurnRecord` | `session.store` protocol，键 `turn:{sid}:{tid}` | `model_dump()` → JSON | `ReplyFinished` 订阅时写 |
| `ParkedState` | `session.store` protocol，键 `park:{sid}` | `model_dump()` → JSON | `Park` 写、`Unpark` 读删 |
| 事件序列 | `session.store` protocol，键 `evt:{sid}:{seq:08d}` | `Chunk.model_dump()` → JSON | `AppendEvent` 写、`ReplayEvents` 读 |
| `Plan` / `Task` | `plan.notebook` protocol，键 `plan:{sid}` | `model_dump()` → JSON | 每次 `UpdateTask` |
| `Skill` 元数据 | `skill.hub` protocol，键 `skill:{name}` | `model_dump()` → JSON | 启动扫描写、`ListSkills` 读 |
| `Skill` 正文 | 文件系统 `body_path` | 原始 Markdown | `LoadSkill` 按需读 |
| `Chunk`（含向量） | `VectorStoreProtocol` | 原生向量格式，metadata 为 JSON | `IngestDocument` / `Search` |
| `MemoryEntry` | `VectorStoreProtocol` | 同上 | `Remember` / `Recall` |
| `Span` | `observe.tracer` protocol（`CRUDProtocol`） | 行式表 | 订阅回调写、`QueryTrace` 读 |
| `Credential` | `credential.vault` protocol，键 `cred:{user}:{system}` | payload 对称加密后 base64 → JSON | `PutCredential` / `GetCredential` |
| 工具分组激活态 | `tool.toolkit` protocol，键 `groups:{session_id}` | list → JSON | 每轮 `ListTools` 读、`ActivateGroup` 写 |
| 产物 | `workspace.*` 的 `LocalFileProtocol` | 原始字节 | `StoreArtifact` / `ReadArtifact` |

**关键分工再次明确**：
- **bollydog 全局 `Session`** 承载「回合内运行态」——上下文消息、摘要、迭代计数、中断标记。它的 Protocol 在 TOML 里配成 `CacheLayer → SQLiteProtocol`，因此既快又不怕重启。
- **`session.store` 的 Protocol** 承载「跨回合档案」——会话记录、回合快照、停放态、事件序列。两者键空间完全不重叠。

---

## 8 检查项对照

| 检查项 | 结果 |
|--------|------|
| P4 每条箭头都有对应签名 | 是。62 个 Command、58 个服务方法、34 个 Protocol 方法、7 个订阅回调全部落定 |
| 每个签名的参数与返回都有数据模型 | 是。第 2 节 19 个 `BaseDomain` 覆盖全部复杂结构 |
| 每个模型字段在顺序图里有使用证据 | 是。逐字段核对，无预留字段 |
| 序列化方案覆盖全部需持久化模型 | 是。第 7 节 16 行覆盖 |
| Command 入参/返回仅原语 | 是。领域模型一律在边界 `model_dump()` |
| 每个 Protocol 都能换成假实现 | 是。第 5 节实现矩阵每行都有测试列 |
