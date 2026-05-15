# A2 系统架构与信息架构

> A2 是基于 bollydog 微服务框架构建的 **多角色 AI Agent 框架**。本文档是架构设计权威来源。

---

## 一、项目定位

bollydog 的 `BaseCommand`（async generator）天然就是 Agent 执行单元，Hub 负责调度和重试，Protocol 链提供存储抽象，StreamState 处理流式输出。

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
│  │ HTTP/SSE │  │ CLI REPL │  │   UDS    │  ← 复用 bollydog 内置层       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                              │
│       └──────────────┴──────────────┘                                    │
│                              │                                          │
│                     load_a2_config(merged)                              │
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
│  │     AgentService (top-level orchestrator + 角色管理)             │   │
│  │     protocol: CacheLayer → SQLiteProtocol(roles表)              │   │
│  │                                                                 │   │
│  │  ┌─────────────┐  ┌──────────────────┐  ┌─────────────────┐   │   │
│  │  │ LLMService  │  │ EnvService       │  │ SkillService    │   │   │
│  │  │ (单一实例)   │  │ (默认 local,     │  │ (全局 Hub+结晶) │   │   │
│  │  │ LiteLLM     │  │  可替换为        │  │                 │   │   │
│  │  │ Router      │  │  docker/ssh)     │  │                 │   │   │
│  │  └─────────────┘  └──────────────────┘  └─────────────────┘   │   │
│  │                                                                 │   │
│  │  ┌──────────────────────────────────────────────────────────┐  │   │
│  │  │ ContextService (per-role 动态子类, owns memory)           │  │   │
│  │  │ depends: [SkillService, LLMService]  ← 全局引用          │  │   │
│  │  │ owns: L0, L1, L4 (MemoryLayerService) + Facts (BM25)    │  │   │
│  │  └──────────────────────────────────────────────────────────┘  │   │
│  │                                                                 │   │
│  │  ┌─────────────┐  ┌─────────────┐                              │   │
│  │  │PlannerService│  │  MCPService  │                              │   │
│  │  │ depends:LLM │  │ 官方mcp SDK  │                              │   │
│  │  └─────────────┘  └─────────────┘                              │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                           │                                            │
│                           ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              AgentCommand.__call__()  [async generator]          │   │
│  │              qos=0, 通过 globals.app 访问服务依赖                 │   │
│  │                                                                 │   │
│  │  agent_loop:                                                    │   │
│  │      0. yield PlanCommand (可选) ← 生成 TaskList               │   │
│  │      while not done:                                            │   │
│  │      1. role_context.build_messages() ← L0-L4 + RoleDef       │   │
│  │      2. yield ReActStepCommand 或 ReflexionCommand              │   │
│  │      3. yield stream_chunk ← 流式输出到 Hub.StreamState         │   │
│  │      4. check termination / max_turns                           │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 服务依赖图（DAG）

```
AgentService  domain=agent
├── protocol: CacheLayer → SQLiteProtocol(roles表)  ← 角色仓库
├── llm.LLMService                      ← 单 adapter = litellm.Router 管多模型
│   └── protocol: LiteLLMProvider → adapter = litellm.Router(model_list)
├── env.local (默认)                    ← EnvService, 可替换为 env.docker / env.ssh
│   └── protocol: TerminalProtocol (复合)
│       ├── adapter: LocalTerminal      ← 命令执行 (或 Docker/SSH adapter)
│       └── protocol: PermissionProtocol ← 权限校验 + 数据模型
├── skills.SkillService                 ← 全局 Hub + 结晶
│   └── protocol: CacheLayer → FileProtocol
├── planner.PlannerService              ← depends: [llm.LLMService]
├── mcp.MCPService                      ← 官方 mcp SDK, depends: [env.local]
│
├── [per-role] context.ContextService   ← 动态子类, by activate_role()
│   ├── depends: [skills.SkillService, llm.LLMService]  ← 全局引用
│   ├── owns: L0 MemoryLayerService → CacheLayer → SQLiteProtocol(rules)
│   ├── owns: L1 MemoryLayerService → CacheLayer → SQLiteProtocol(insights)
│   ├── owns: L4 MemoryLayerService → CacheLayer → SQLiteProtocol(sessions)
│   └── owns: GlobalFactsService → CacheLayer → SQLiteProtocol(facts) + BM25
```

### 2.3 服务清单

| Service 类 | 说明 | 实例 | Protocol |
|-----------|------|------|----------|
| `AgentService` | 顶层编排 + 角色管理 | 1 | CacheLayer → SQLiteProtocol |
| `LLMService` | Router 管多模型 | 1 | LiteLLMProvider |
| `EnvService` | 执行环境 + 拥有 ToolCommand + 权限 | 1（默认 local，可替换） | TerminalProtocol → PermissionProtocol |
| `SkillService` | 全局 Hub + 三级披露 + 结晶 | 1 | CacheLayer → FileProtocol |
| `PlannerService` | 规划决策 | 1 | — |
| `MCPService` | MCP 生命周期管理 | 1 | — |
| `ContextService` | **per-role 动态子类**，上下文组装 + 压缩 + owns memory | N | — |
| `MemoryLayerService` | 基类（L0/L1/L4），由 ContextService 创建 | 3×N | CacheLayer → SQLiteProtocol |
| `GlobalFactsService` | BM25 检索，由 ContextService 创建 | 1×N | CacheLayer → SQLiteProtocol |

> **注意：不再有 ToolService。** EnvService 直接拥有 ToolCommand，agent 通过当前角色绑定的 env 获取可用工具。

### 2.4 Tool/Env 设计 — Env 拥有 Tool

**核心思想**：工具是环境的能力，而非独立实体。

```
EnvService (运行时有且只有一个实例，默认 local，可替换为 docker/ssh)
├── protocol: TerminalProtocol (复合协议)
│   ├── adapter: LocalTerminal / DockerTerminal / SSHTerminal  ← 命令执行
│   └── protocol: PermissionProtocol                           ← 权限校验
│       └── PermissionRule 数据模型 (内存加载)
├── commands: ['a2.env.tools']            ← 注册 ToolCommand
│   └── 共有工具: read_file, write_file, str_replace, ls, grep, bash, python
└── get_tool_schemas(role_filter) → list  ← 供 ReActStep 构建 LLM tools

环境无关工具（Command on AgentService）:
├── present_files  ← Artifact 产物展示
├── ask_user       ← 请求用户澄清
├── spawn_agent    ← SpawnAgentCommand
├── create_role    ← CreateRoleCommand
└── web_search / web_fetch  ← HTTP 工具，不依赖 env protocol
```

**bollydog 如何支撑**：ToolCommand 注册在 EnvService 上，执行时 `globals.protocol` 自动指向该 env 的 TerminalProtocol。同一个 `ReadFileCommand` 类注册在不同 env 实例上，走不同的 protocol 实现。

**工具聚合**（在 ReActStepCommand 中）：

```python
tools = svc._env.get_tool_schemas(role_def.tools)   # env 的工具（protocol 内部已校验权限）
tools += svc.get_agent_tool_schemas()               # agent 级工具 (present_files, ask_user...)
tools += svc._mcp.get_tool_schemas() if svc._mcp else []  # MCP 工具
tools = _filter_by_model(tools, role_def.model)     # 模型兼容性过滤
```

### 2.5 存储方案 — 角色隔离 + 嵌入式数据库

每个角色独立 DB 目录。`SQLiteProtocol(KVProtocol)` 映射 KV 接口到 `(key TEXT PK, value TEXT, updated_at REAL)` 表。

```
.agent/
├── roles.db                  ← 全局角色定义（AgentService.protocol）
├── roles/
│   ├── default/
│   │   ├── memory.db          ← rules / insights / sessions（同库多表）
│   │   ├── facts.db           ← facts（独立 DB，有 BM25 索引）
│   │   ├── tasks.db           ← PlannerService 持久化（默认开启）
│   │   └── outputs/           ← Artifact 产物目录
│   ├── explorer/
│   │   └── ...
│   └── devops/
│       └── ...
└── skills/                    ← 技能文件（全局 Hub）
```

**优势**：角色完全物理隔离（多租户）、无需 namespace 前缀、DuckDB 可跨角色聚合分析（v2）。

### 2.6 配置方案 — config.py 默认 + agent.toml 覆盖

| 层级 | 来源 | 内容 | 何时生效 |
|------|------|------|---------|
| Python 默认 | `config.py` `SERVICE_DEFAULT` | 全局单例服务参数（LLM/Env/Skill/Planner/MCP） | `build_config()` |
| Python 默认 | `config.py` `ROLE_DEFAULT` | 角色级 memory ServiceSpec | `activate_role()` |
| TOML 覆盖 | `agent.toml` | 各模块配置覆盖（LLM/Env/MCP 等） | `build_config()` 合并 |
| 运行时 | `AgentService.activate_role` | 动态创建 ContextService + owned memory | `on_started()` |

> 系统提示词属于角色定义（`RoleDef.system_prompt`），不作为独立配置项。

**配置流转**：`build_config(toml_overrides)` 深度合并 → `load_a2_config(merged)` → bollydog `smart_import(module).create_from(**conf)` → pop 框架字段 → **剩余字段 = `svc.config`**。

### 2.7 开源替代组件

| 模块 | 原方案 | 替换 | 效果 |
|------|--------|------|------|
| LLM Provider + Fallback | 自实现 adapter + fallback | **LiteLLM Router** | 删 ~400 行 |
| MCP 传输层 | 自实现 stdio/SSE | **官方 mcp SDK** | 协议层全删 |
| L2 BM25 检索 | 自实现 | **bm25s** | 零实现 |
| Token 计数 | 启发式估算 | **litellm.token_counter** | 1 行 |
| Docker 执行环境 | 自实现 Docker API | **aiodocker** | 删 ~100 行 |
| SSH 执行环境 | 自实现 SSH | **asyncssh** | 删 ~150 行 |

---

## 三、信息架构

### 3.1 System Prompt 三段式结构

```
messages[]:
  [system 0]  静态核心（永远存在，prefix cache 友好）
              = RoleDef.system_prompt（角色系统提示词，从 roles.db 取）
              + L0 环境上下文（working_dir, date, platform）
              + L1 技能元数据（~100 tok/技能）
              + L2 检索结果（~2K tok，按需）

  [system N]  System Reminder（动态注入，按需）
              = L3 技能正文（触发时插入，不修改静态 prompt）

  [user/assistant ...]  对话历史（L4）

tools[]       工具描述（env 工具 + agent 工具 + MCP 工具，按 RoleDef.tools 过滤）
```

### 3.2 记忆系统分层

| 层级 | 名称 | 归属 | Protocol | 加载策略 | ~Token |
|------|------|------|----------|---------|--------|
| L0 | Meta Rules | ContextService(owns) | CacheLayer → SQLiteProtocol(rules) | 启动时全量 | ~500 |
| L1 | Insight Index | ContextService(owns) | CacheLayer → SQLiteProtocol(insights) | 启动时元数据 | ~100/技能 |
| L2 | Global Facts | ContextService(owns) | CacheLayer → SQLiteProtocol(facts) + bm25s | 按需检索 | ~2K |
| L3 | Task Skills | SkillService(全局) | CacheLayer → FileProtocol | 关键词触发 | ~2K/技能 |
| L4 | Session Archive | ContextService(owns) | CacheLayer → SQLiteProtocol(sessions) | 最近消息 | 可变 |

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

**压缩前 Memory Flush**：执行 Layer 3 前将即将压缩的对话推给 L1 提取 insights，防信息丢失。

### 3.4 数据流全景

```
用户输入
  ↓
Hub.dispatch(AgentCommand(goal=..., role_def=...))
  ↓
role_context = svc._active_contexts[ns]
role_context.build_messages(role_def, query, history)
  ├── L0: self._l0.get_all() → rules
  ├── L1/Skill: self._skill.get_metadata_prompt()
  ├── L2: self._facts.get_relevant(query)
  ├── L3: self._skill.get_matching(query)
  └── L4: session history
  ↓
[system(静态核心)] + [system(skill reminder)] + [历史] + [tools]
  ↓
svc._llm.chat(messages, tools, model)
  ↓
解析 tool_calls:
  ├── env 工具 → yield ToolCallCommand → env.protocol 执行
  ├── agent 工具 → yield SpawnAgentCommand / PresentFilesCommand / ...
  └── MCP 工具 → yield MCPCallCommand → MCPServerProtocol.call_tool()
  ↓
observation → 追加到 history → 循环
  ↓
任务完成 → yield 流式输出 → yield CrystallizeSkillCommand(可选)
```

### 3.5 多角色记忆隔离

每个角色拥有独立的物理 DB 目录。**无需 namespace 前缀**。需要跨角色分析时，v2 通过 DuckDB ATTACH 多库查询。

---

## 四、Bollydog 集成约定

### 4.1 服务访问三层模型

```python
# ① Service 层：通过 depends 持有依赖引用
class AgentService(AppService):
    depends = ['llm.LLMService', 'env.local', 'skills.SkillService', ...]  # env 默认 local
    async def on_started(self):
        for dep in self.depends:
            match dep.alias:
                case 'LLMService': self._llm = dep
                case 'local' | 'docker' | 'ssh': self._env = dep  # 单实例
                ...

# ② Command 层：通过 globals.app 间接访问
class ReActStepCommand(BaseCommand):
    async def __call__(self):
        svc = app  # globals.app → AgentService
        response = await svc._llm.chat(...)

# ③ 万不得已：AppService._apps['key']（应反思是否缺少 Command）
```

### 4.2 核心原则 — 如果需要 `_apps`，说明缺少 Command

> 当 Command 发现自己需要 `_apps['key']` 直接访问某个 Service 时，首先应反思：是不是那个 Service 应该暴露一个 Command 让其他服务通过 Hub dispatch？

### 4.3 Domain 命名 — 按目录分域，无 `.`

| 模块位置 | domain | alias | _apps 键 |
|---------|--------|-------|----------|
| `a2/llm/service.py` | `llm` | `LLMService` | `llm.LLMService` |
| `a2/env/service.py` | `env` | `local`（默认，可替换） | `env.local` |
| `a2/memory/service.py` | `memory` | (动态) | ContextService 内部管理 |
| `a2/skills/service.py` | `skills` | `SkillService` | `skills.SkillService` |
| `a2/context/service.py` | `context` | `ContextService` | 动态子类 per-role |
| `a2/planner/service.py` | `planner` | `PlannerService` | `planner.PlannerService` |
| `a2/mcp/service.py` | `mcp` | `MCPService` | `mcp.MCPService` |
| `a2/agent/service.py` | `agent` | `AgentService` | `agent.AgentService` |

### 4.4 _run_gen 双向通信

```
gen.asend(feedback) 三种分支:
  yield Message       → dispatch 子命令，feedback = 子命令返回值
  yield [Msg, Msg]    → 并行 dispatch，feedback = 结果列表
  yield dict/value    → 流式输出到 StreamState，feedback = None
```

### 4.5 关键约定速查

| 场景 | 正确 | 错误 |
|------|------|------|
| Service 访问依赖 | `self.depends` → `self._llm`（on_started 赋值） | `AppService._apps` |
| Command 访问所属服务 | `globals.app.method()` | `self._service.method()` |
| Command 访问跨域 | `app._llm.chat()`（Service 已持有依赖） | 直接 `_apps['key']` |
| 跨域且无 depends | **反思：目标 Service 是否该暴露 Command** | 直接 `_apps` |
| 流式 Command | `qos=0` | 默认 `qos=1` |
| CLI | python-fire | click |
| HTTP | Starlette | FastAPI |

---

## 五、Command 分离纪律

### 5.1 层次模型

```
                    ┌─────────────────────┐
 Service 层 ──────▶ │ depends 声明 + 注入   │ ← 合法的跨服务引用
                    └──────────┬──────────┘
                               │ on_started 后赋值为实例属性
                               ▼
                    ┌─────────────────────┐
 Command 层 ──────▶ │ globals.app.xxx     │ ← 通过 Service 间接访问
                    └──────────┬──────────┘
                               │ 需要跨域协调？
                               ▼
                    ┌─────────────────────┐
 Hub 调度 ────────▶ │ yield SubCommand()  │ ← 目标 Service 暴露 Command
                    └─────────────────────┘
```

### 5.2 Command 全景调用链

```
Hub dispatch
└── AgentCommand (agent.AgentService)
    ├── yield PlanCommand → app._planner 判断 → app._llm.chat()
    └── yield ReActStepCommand
        ├── app._llm.chat() 推理
        ├── env 工具 → yield ToolCallCommand → env.protocol 执行
        │   └── spawn_agent → yield SpawnAgentCommand → yield AgentCommand(sub_role)
        ├── MCP 工具 → yield MCPCallCommand → MCPServerProtocol.call_tool()
        └── yield CrystallizeSkillCommand
            ├── yield ExtractPatternCommand → LLM
            ├── app._skill.save_skill()
            └── yield NotifyIndexUpdateCommand → L1
```

---

## 六、bollydog 复用机制

bollydog **零修改**。A2 作为纯上层应用。

| 机制 | A2 复用方式 |
|------|------------|
| `Hub._run_gen` + `gen.asend` | AgentCommand 双向通信 |
| `AppService.depends` | 启动后解析为实例列表 |
| `AppService.create_from` | TOML / config.py 驱动实例化 |
| Protocol `_build_protocol` | CacheLayer → SQLiteProtocol 复合链 |
| `SQLiteProtocol(table=)` | 统一嵌入式数据库，多表隔离 |
| `smart_import()` | 动态子类 + MCP 加载 |
| Session KV | agent_depth、task_list |
| Exchange | v2 Reactive Network |
| `globals.app/protocol/session` | 请求作用域访问 |
| fire CLI / Starlette HTTP | 复用内置入口 |
| `abstract=True` | 阻止 ToolCommand 自动注册到 Hub |

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [00-architecture.md](00-architecture.md) | 本文档 — 系统架构 + 信息架构 |
| [01-data-models.md](01-data-models.md) | 数据模型（RoleDef / Task / Skill / ServiceSpec 等） |
| [02-mod-llm.md](02-mod-llm.md) | LLMService 模块（无依赖） |
| [03-mod-env.md](03-mod-env.md) | EnvService 模块（无依赖，含 ToolCommand + 权限） |
| [04-mod-memory.md](04-mod-memory.md) | Memory 模块（无依赖，基类） |
| [05-mod-skills.md](05-mod-skills.md) | SkillService 模块（依赖 LLM） |
| [06-mod-planner.md](06-mod-planner.md) | PlannerService 模块（依赖 LLM） |
| [07-mod-mcp.md](07-mod-mcp.md) | MCPService 模块（依赖 Env） |
| [08-mod-context.md](08-mod-context.md) | ContextService 模块（依赖 Skills+LLM，owns Memory） |
| [09-mod-agent.md](09-mod-agent.md) | AgentService 模块（依赖全部） |
| [10-mod-config.md](10-mod-config.md) | 配置 + CLI + 部署 |
| [11-dev-plan.md](11-dev-plan.md) | 开发计划 |
| [A1-open-questions.md](A1-open-questions.md) | 问题附录 |
| [A2-feature-reference.md](A2-feature-reference.md) | 外部特性参考（DeerFlow / CrewAI 等） |
