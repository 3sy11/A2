# 01 — 系统架构与信息架构

> 本文档是 A2 项目的**架构设计权威来源**。
> 执行计划见 [00-master-plan.md](00-master-plan.md)。

---

## 一、项目定位

A2 是基于 bollydog 微服务框架构建的 **多角色 AI Agent 框架**。核心思路：bollydog 的 `BaseCommand`（async generator）天然就是 Agent 执行单元，Hub 负责调度和重试，Protocol 链提供存储抽象，StreamState 处理流式输出。

**融合四个项目的长处：**
- **bollydog**：Command Pattern + Hub 调度 + Protocol 存储 + Exchange 发布订阅 + Session + Queue
- **hermes-agent**（NousResearch）：Tool 系统 + Skills/程序性记忆 + 子智能体委派
- **GenericAgent**（lsdefine）：分层记忆 L0-L4 + 自演化（任务→探索→结晶→回忆）+ 上下文效率 <30K tokens
- **Claude Code**（参考模式）：3 层压缩 + Hooks + 多智能体 + Skill 渐进式披露 + 三段式 System Prompt

---

## 二、系统架构

### 2.1 架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Entrypoints (入口层)                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                              │
│  │ HTTP/SSE │  │ CLI REPL │  │   UDS    │                              │
│  │ Starlette│  │  fire    │  │          │  ← 复用 bollydog 内置层       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                              │
│       └──────────────┴──────────────┘                                    │
│                              │                                          │
│                     load_from_config(agent.toml)                        │
│                              │                                          │
├──────────────────────────────┼──────────────────────────────────────────┤
│                              ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                          Hub (bollydog)                          │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │   │
│  │  │ Exchange │  │ Session  │  │  Queue   │  │ _run_gen │       │   │
│  │  │ (pub/sub)│  │ (KV 会话)│  │ (qos=1) │  │ (asend)  │       │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │   │
│  └────────────────────────┬────────────────────────────────────────┘   │
│                           │ dispatch → AgentCommand                     │
├───────────────────────────┼────────────────────────────────────────────┤
│                           ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              AgentService (top-level orchestrator)               │   │
│  │                                                                 │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐       │   │
│  │  │ LLMService  │  │ ToolService │  │AgentRegistryService│      │   │
│  │  │ (单一实例)   │  │ (工具门面)  │  │  (角色仓库)       │       │   │
│  │  │ LiteLLM     │  │ +权限管理   │  │  RoleDef 配置    │       │   │
│  │  │ Router      │  │             │  │                  │       │   │
│  │  └─────────────┘  └──────┬──────┘  └──────────────────┘       │   │
│  │                     ┌────┴────┐                                │   │
│  │                   ┌─┴──────┐┌┴──────┐┌───────┐                │   │
│  │                   │ local  ││ docker││  ssh  │                │   │
│  │                   │EnvSvc  ││EnvSvc ││EnvSvc │  ← 同一基类    │   │
│  │                   │Protocol││Protocol││Protocol│  TOML 配实例  │   │
│  │                   └────────┘└────────┘└───────┘                │   │
│  │                                                                 │   │
│  │  ┌──────────────────────────────────────────┐                  │   │
│  │  │ ContextService (上下文管理 + 3 层压缩)     │                  │   │
│  │  │ depends: [L0, L1, L2, SkillService, L4]  │                  │   │
│  │  └──────────────────────────────────────────┘                  │   │
│  │    ┌────┐┌────┐┌─────────────────┐┌────────────┐┌────┐        │   │
│  │    │ L0 ││ L1 ││L2GlobalFactsSvc ││SkillService││ L4 │        │   │
│  │    │TOML││TOML││(有BM25检索逻辑) ││(结晶+三级)  ││TOML│        │   │
│  │    │实例 ││实例 ││独立 Service     ││独立 Service ││实例 │        │   │
│  │    └────┘└────┘└─────────────────┘└────────────┘└────┘        │   │
│  │                                                                 │   │
│  │  ┌─────────────┐  ┌─────────────┐                              │   │
│  │  │PlannerService│  │  MCPService  │                              │   │
│  │  │ (规划决策)   │  │ (MCP 集成)   │                              │   │
│  │  │ depends:LLM │  │ 官方mcp SDK  │                              │   │
│  │  └─────────────┘  └─────────────┘                              │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                           │                                            │
│                           ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              AgentCommand.__call__()  [async generator]          │   │
│  │              qos=0, 通过 globals.app + AppService._apps 访问服务 │   │
│  │                                                                 │   │
│  │  agent_loop:                                                    │   │
│  │      0. yield PlanCommand (可选) ← 生成 TaskList               │   │
│  │      while not done:                                            │   │
│  │      1. ContextService.build_context() ← L0-L4 + RoleDef      │   │
│  │      2. yield ReActStepCommand 或 ReflexionCommand              │   │
│  │      3. yield stream_chunk ← 流式输出到 Hub.StreamState         │   │
│  │      4. check termination / max_turns                           │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 服务依赖图（DAG）

```
AgentService (top-level orchestrator)
├── a2.LLMService                         ← 单一 LLMService + LiteLLM Router
│   └── protocol: LiteLLMProvider(Protocol) → adapter = litellm.Router
├── a2.ToolService                        ← 工具门面
│   ├── a2.env.local                      ← EnvService(TOML实例) → LocalProtocol
│   ├── a2.env.docker                     ← EnvService(TOML实例) → DockerProtocol(aiodocker)
│   └── a2.env.ssh                        ← EnvService(TOML实例) → SSHProtocol(asyncssh)
├── a2.ContextService                     ← 上下文组装 + 3 层压缩
│   ├── a2.memory.l0                      ← MemoryLayerService(TOML实例) → FileProtocol
│   ├── a2.memory.l1                      ← MemoryLayerService(TOML实例) → KVProtocol
│   ├── a2.L2GlobalFactsService           ← 独立 Service，BM25(bm25s) 检索
│   ├── a2.SkillService                   ← 技能管理，CacheLayer → FileProtocol
│   ├── a2.memory.l4                      ← MemoryLayerService(TOML实例) → CacheLayer → FileProtocol
│   └── a2.LLMService                     ← 压缩时 LLM 调用（共享引用）
├── a2.PlannerService                     ← depends: [a2.LLMService]
│   └── protocol: LocalFileProtocol
├── a2.MCPService                         ← 官方 mcp SDK
│   └── a2.ToolService
├── a2.AgentRegistryService               ← 角色仓库
│   └── protocol: KVProtocol
└── a2.SkillService                       ← system prompt 技能元数据（共享引用）
```

### 2.3 服务清单（8 类 + TOML 实例）

| Service 类 | 说明 | 实例数 | Protocol |
|-----------|------|--------|----------|
| `LLMService` | 单一，LiteLLM Router | 1 | LiteLLMProvider |
| `ToolService` | 工具门面 + 权限管理 | 1 | — |
| `EnvService` | 基类，TOML 配执行环境 | 3 TOML | Local/Docker/SSHProtocol |
| `MemoryLayerService` | 基类，TOML 配 L0/L1/L4 | 3 TOML | 各异 |
| `L2GlobalFactsService` | 独立，BM25(bm25s) 检索 | 1 | KVProtocol |
| `SkillService` | 技能管理 + 三级披露 + 结晶 | 1 | CacheLayer → FileProtocol |
| `ContextService` | 上下文组装 + 3 层压缩 | 1 | — |
| `AgentRegistryService` | 角色仓库，存储 RoleDef | 1 | KVProtocol |
| `MCPService` | MCP 生命周期管理 | 1 | — |
| `PlannerService` | 规划决策 + TaskList 持久化 | 1 | LocalFileProtocol |
| `AgentService` | 顶层编排 | 1 | — |

### 2.4 开源替代组件

| 模块 | 原方案 | 替换 | 效果 |
|------|--------|------|------|
| LLM Provider + Fallback | 自实现 adapter + fallback | **LiteLLM Router** | 删 ~400 行 |
| MCP 传输层 | 自实现 stdio/SSE | **官方 mcp SDK** | 协议层全删 |
| L2 BM25 检索 | 自实现 | **bm25s** | 零实现 |
| Token 计数 | 启发式估算 | **litellm.token_counter** | 1 行，跨模型精确 |
| Docker 执行环境 | 自实现 Docker API | **aiodocker** | 删 ~100 行 |
| SSH 执行环境 | 自实现 SSH | **asyncssh** | 删 ~150 行 |

---

## 三、信息架构

### 3.1 System Prompt 三段式结构

```
messages[]:
  [system 0]  静态核心（永远存在）
              = DEFAULT_SYSTEM_PROMPT（~200 tok，框架内置常量）
              + RoleDef.system_prompt（角色身份，从 AgentRegistry 取）
              + L0 环境上下文（working_dir, date, platform）
              + L1 技能元数据（~100 tok/技能）
              + L2 检索结果（~2K tok，按需）

  [system N]  System Reminder（动态注入，按需）
              = L3 技能正文（触发时插入，不修改静态 prompt）

  [user/assistant ...]  对话历史（L4）

tools[]       工具描述（按 RoleDef.tools 过滤）
```

### 3.2 记忆系统分层

| 层级 | 名称 | Service 类型 | Protocol | 加载策略 | ~Token |
|------|------|-------------|----------|---------|--------|
| L0 | Meta Rules | MemoryLayerService(TOML) | FileProtocol | 启动时全量 | ~500 |
| L1 | Insight Index | MemoryLayerService(TOML) | KVProtocol | 启动时元数据 | ~100/技能 |
| L2 | Global Facts | L2GlobalFactsService(独立) | KVProtocol + bm25s | 按需检索 | ~2K |
| L3 | Task Skills | SkillService(独立) | CacheLayer → FileProtocol | 关键词触发 | ~2K/技能 |
| L4 | Session Archive | MemoryLayerService(TOML) | CacheLayer → FileProtocol | 最近消息 | 可变 |

**为什么 L0/L1/L4 用基类、L2/L3 独立：** L0/L1/L4 无业务逻辑（纯存取），L2 有 BM25 检索，L3 有三级披露 + 结晶。

### 3.3 三层上下文压缩

```
超过 compact_threshold?
├── 否 → 原样返回
└── 是 → Layer 1: MicroCompact（规则驱动，无 LLM）
          ├── 远距消息按类型优先级截断（tool_result > tool_use > assistant > user）
          ├── 旧 system reminder → 移除
          ├── 保护最近 2 轮
          ├── 仍超标? → Layer 2: SessionMemory（零成本）
          │   └── 已有摘要? → 替换旧消息区段
          └── 仍超标? → Layer 3: FullLLMCompact（LLM 9 段式摘要）
```

### 3.4 数据流全景

```
用户输入
  ↓
Hub.dispatch(AgentCommand(goal=..., role_def=...))
  ↓
ContextService.build_messages(role_def, query, history)
  ├── L0: get_all() → rules
  ├── L1/Skill: get_metadata_prompt() → skill index
  ├── L2: get_relevant(query) → BM25 facts
  ├── L3: get_matching(query) → skill bodies (System Reminder)
  └── L4: session history
  ↓
[system(静态核心)] + [system(skill reminder)] + [历史] + [tools]
  ↓
LLMService.chat(messages, tools, model)
  ↓
解析 tool_calls → ToolService.execute(name, params)
  ├── 内置 Tool → EnvService.protocol.execute()
  ├── MCP Tool → MCPServerProtocol.call_tool()
  └── spawn_agent → Hub.execute(SpawnAgentCommand) → AgentCommand(sub_role)
  ↓
observation → 追加到 history → 循环
  ↓
任务完成 → yield 流式输出 → CrystallizeSkillCommand(可选)
```

### 3.5 多角色记忆隔离

每个角色的记忆按 `RoleDef.memory_namespace` 隔离：

```
.agent/roles/{namespace}/
├── rules/        ← L0 FileProtocol.path
├── index/        ← L1 KVProtocol namespace
├── facts.db      ← L2 KVProtocol namespace
├── skills/       ← SkillService.skills_dir
└── sessions/     ← L4 FileProtocol.path
```

---

## 四、Bollydog 集成约定

### 4.1 服务访问模式

```python
# Command 内访问所属 Service
from bollydog.globals import app, protocol, session, message
class MyCommand(BaseCommand):
    async def __call__(self):
        app.do_something()              # ✅ globals.app → 当前 Service
        await protocol.get('key')       # ✅ 当前 Service 的 Protocol

# Command 跨域访问
from bollydog.models.service import AppService
class CrossCommand(BaseCommand):
    async def __call__(self):
        llm = AppService._apps['a2.LLMService']  # ✅
        # ❌ app.resolve('a2.LLMService')  ← 不存在
```

### 4.2 Domain 与 _apps 键命名

```python
key = f'{self.domain}.{self.alias}'   # bollydog/models/service.py
```

| TOML 节名 | domain | alias | _apps 键 |
|-----------|--------|-------|----------|
| `a2.LLMService` | `a2` | `LLMService` | `a2.LLMService` |
| `a2.ToolService` | `a2` | `ToolService` | `a2.ToolService` |
| `a2.env.local` | `a2.env` | `local` | `a2.env.local` |
| `a2.memory.l0` | `a2.memory` | `l0` | `a2.memory.l0` |
| `a2.L2GlobalFactsService` | `a2` | `L2GlobalFactsService` | `a2.L2GlobalFactsService` |
| `a2.SkillService` | `a2` | `SkillService` | `a2.SkillService` |
| `a2.AgentRegistryService` | `a2` | `AgentRegistryService` | `a2.AgentRegistryService` |
| `a2.PlannerService` | `a2` | `PlannerService` | `a2.PlannerService` |
| `a2.MCPService` | `a2` | `MCPService` | `a2.MCPService` |

### 4.3 Tool 不经过 Hub dispatch

```python
class Tool(BaseCommand, abstract=True):  # abstract=True → 不自动注册
    async def __call__(self, **kwargs) -> str:
        return await self.execute(**kwargs)
# Tool 由 ToolService.execute() 直接调用
```

### 4.4 AgentCommand globals 模式

```python
class AgentCommand(BaseCommand):
    qos: int = 0  # 流式不走队列
    async def __call__(self):
        planner = AppService._apps['a2.PlannerService']
        # 不通过构造函数注入
```

### 4.5 _run_gen 双向通信

```
gen.asend(feedback) 三种分支:
  yield Message       → dispatch 子命令，feedback = 子命令返回值
  yield [Msg, Msg]    → 并行 dispatch，feedback = 结果列表
  yield dict/value    → 流式输出到 StreamState，feedback = None
```

### 4.6 关键约定速查

| 场景 | 正确 | 错误 |
|------|------|------|
| Command 访问所属服务 | `globals.app.method()` | `self._service.method()` |
| Command 跨域访问 | `AppService._apps['key']` | `app.resolve('key')` |
| Service 间依赖 | `depends` 注入 → `self._children` | 直接引用 |
| Tool 注册 | `abstract=True` | 不设 abstract |
| 流式 Command | `qos=0` | 默认 `qos=1` |
| CLI | python-fire | click |
| HTTP | Starlette | FastAPI |

---

## 五、Command 分离纪律

### 5.1 允许/禁止

| 主体 | → 目标 | 机制 | 是否允许 |
|------|--------|------|---------|
| Command | → 同 Service | `globals.app` | ✅ |
| Command | → 跨 Service | `AppService._apps` | ✅ |
| Command | → 子 Command | `yield SubCommand()` | ✅ |
| Service | → 自身 Protocol | `self.protocol` | ✅ |
| Service | → depends | `self._children` | ✅ |
| Service A | → Service B 业务 | 直接调用 | ❌ 封装 Command |
| Tool | → 非 ToolService | 直接调用 | ❌ yield SubCommand |

### 5.2 需要 Command 化的场景

| Command | 触发 | 调用链 |
|---------|------|--------|
| `ExtractPatternCommand` | 技能结晶需 LLM | → `AppService._apps['a2.LLMService'].chat()` |
| `CrystallizeSkillCommand` | 任务成功 + ≥3 tool_calls | → ExtractPattern → SkillService.save → NotifyIndex |
| `NotifyIndexUpdateCommand` | 新技能后更新 L1 | → `AppService._apps['a2.memory.l1'].set()` |
| `SpawnAgentCommand` | spawn_agent 工具 | → Registry.get(role) → yield AgentCommand(role_def) |

### 5.3 Command 全景调用链

```
Hub dispatch
└── AgentCommand
    ├── yield PlanCommand → LLMService.chat()
    └── yield ReActStepCommand
        ├── LLMService.chat() 推理
        ├── ToolService.execute()
        │   └── spawn_agent → yield SpawnAgentCommand → yield AgentCommand(sub_role)
        └── yield CrystallizeSkillCommand
            ├── yield ExtractPatternCommand → LLM
            ├── SkillService.save_skill()
            └── yield NotifyIndexUpdateCommand → L1.set()
```

---

## 六、bollydog 复用机制

bollydog **零修改**。A2 作为纯上层应用。

| 机制 | A2 复用方式 |
|------|------------|
| `Hub._run_gen` + `gen.asend` | AgentCommand 双向通信 |
| `AppService._apps` | Command 内跨域访问 |
| `AppService.create_from` | TOML 驱动实例化 |
| Protocol `_build_protocol` | L4 CacheLayer→FileProtocol |
| `smart_import()` | MCP 动态加载 |
| Session KV | agent_depth、task_list |
| Exchange | v2 Reactive Network |
| `globals.app/protocol/session` | 请求作用域访问 |
| fire CLI / Starlette HTTP | 复用内置入口 |
| `abstract=True` | Tool 阻止自动注册 |
