# A2 Agent Framework — 完整执行计划

> 设计文档位于 `docs/` 目录下，各模块详细设计见同目录对应的子文档。

## 待办事项

- [ ] **Command 与 Service 分离**：将当前 Service 中的跨服务直接调用抽取为 Command。分离标准——**Service 之间的相互直接调用都需要 Command 化**。详见 [15-command-separation.md](15-command-separation.md)。

## 一、项目定位

A2 是基于 bollydog 微服务框架构建的 AI Agent 框架。核心思路：bollydog 的 `BaseCommand`（async generator）天然就是 Agent 执行单元，Hub 负责调度和重试，Protocol 链提供存储抽象，StreamState 处理流式输出。在 bollydog 之上，补全 LLM 调用、工具系统、分层记忆、技能系统、子智能体、MCP 集成和安全控制。

**融合三个项目的长处：**
- **bollydog**：Command Pattern + Hub 调度 + Protocol 存储 + Exchange 发布订阅 + Session + Queue
- **hermes-agent**（NousResearch）：Tool 系统 + Skills/程序性记忆 + 子智能体委派 + 多平台网关
- **GenericAgent**（lsdefine）：分层记忆 L0-L4 + 自演化（任务→探索→结晶→回忆）+ 上下文效率 <30K tokens
- **Claude Code**（参考模式）：3 层压缩 + Hooks + 多智能体 + Skill 渐进式披露 + Task DAG + 后台任务

---

## 二、系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Entrypoints (入口层)                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                              │
│  │ HTTP/WS  │  │ CLI REPL │  │   UDS    │                              │
│  │ FastAPI  │  │  click   │  │          │                              │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                              │
│       └──────────────┴──────────────┘                                    │
│                              │                                          │
│                     load_from_config(toml)                              │
│                              │                                          │
├──────────────────────────────┼──────────────────────────────────────────┤
│                              ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                          Hub (bollydog)                          │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │   │
│  │  │ Exchange │  │ Session  │  │  Queue   │  │  before/ │       │   │
│  │  │ (pub/sub)│  │ (KV 会话)│  │ (qos=1) │  │  after   │       │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │   │
│  └────────────────────────┬────────────────────────────────────────┘   │
│                           │ dispatch → AgentCommand                     │
├───────────────────────────┼────────────────────────────────────────────┤
│                           ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              AgentService (top-level orchestrator)               │   │
│  │              depends: [7 个子服务]                                │   │
│  │                                                                 │   │
│  │  ┌─────────────┐  ┌─────────────┐                              │   │
│  │  │ LLMService  │  │ ToolService │                              │   │
│  │  │ (LLM 门面)  │  │ (工具门面)  │                              │   │
│  │  │ →3个能力服务│  │ →3个环境服务│                              │   │
│  │  │             │  │ +权限管理   │                              │   │
│  │  └──────┬──────┘  └──────┬──────┘                              │   │
│  │    ┌────┼────┐      ┌────┴────┐                                │   │
│  │  ┌─┴──┐┌┴──┐┌┴──┐┌─┴──────┐┌┴──────┐┌───────┐              │   │
│  │  │Fast││Cap││Code││ Local  ││ Docker││  SSH  │              │   │
│  │  │LLM ││LLM││LLM ││ToolSvc ││ToolSvc││ToolSvc│              │   │
│  │  │Prot││Pro││Pro ││Protocol││Protocol││Protocol│              │   │
│  │  └────┘└───┘└───┘└────────┘└────────┘└───────┘              │   │
│  │                                                                 │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │   │
│  │  │MemoryService│  │PlannerService│  │  MCPService  │            │   │
│  │  │ (L0-L4 记忆)│  │ (规划决策)  │  │ (MCP 集成)   │            │   │
│  │  │ →5个层级服务│  │ 无 depends  │  │ →ToolService │            │   │
│  │  └──────┬──────┘  └─────────────┘  └─────────────┘            │   │
│  │    ┌────┼────┐                                                 │   │
│  │  ┌─┴──┐┌┴──┐┌┴──┐┌┴──┐┌─┴──┐                                 │   │
│  │  │ L0 ││L1 ││L2 ││L3 ││ L4 │                                 │   │
│  │  │Meta││Idx││Fct││Skl││Sess│                                 │   │
│  │  │Svc ││Svc││Svc││Svc││Svc │                                 │   │
│  │  └────┘└───┘└───┘└───┘└────┘                                 │   │
│  │  └──────┬──────┘  └─────────────┘  └─────────────┘            │   │
│  │         │                                                      │   │
│  │  ┌──────┴──────┐  ┌─────────────┐                              │   │
│  │  │SkillService │  │ContextService│                              │   │
│  │  │ (技能管理)  │  │ (上下文管理) │                              │   │
│  │  │ →MemorySvc  │  │ →MemorySvc  │                              │   │
│  │  └─────────────┘  │ →SkillSvc   │                              │   │
│  │                    └─────────────┘                              │   │
│  │  ┌─────────────────────────────┐                                │   │
│  │  │     SubagentService         │                                │   │
│  │  │     (子智能体管理)           │                                │   │
│  │  │     →LLMService             │                                │   │
│  │  └─────────────────────────────┘                                │   │
│  └────────────────────────┬────────────────────────────────────────┘   │
│                           │                                            │
│                           ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              AgentCommand.__call__()  [async generator]          │   │
│  │                                                                 │   │
│  │  agent_loop:                                                    │   │
│  │      0. yield PlanCommand (可选) ← 生成 TaskList               │   │
│  │      while not done:                                            │   │
│  │      1. ContextService.build_context()  ← L0-L4 + session       │   │
│  │      2. yield ReActStepCommand 或 ReflexionCommand              │   │
│  │         └── ReActStepCommand:                                   │   │
│  │             a. LLMService.chat(msgs,tools) ← LLM 推理           │   │
│  │             b. parse tool_calls ← 解析工具调用                   │   │
│  │             c. for each tool_call:                               │   │
│  │                  ToolService.execute(tool) ← 权限检查+执行       │   │
│  │             d. ContextService.update(result) ← 追加到上下文      │   │
│  │      3. yield stream_chunk              ← 流式输出到 Hub         │   │
│  │      4. check termination (no tool_calls → done)                │   │
│  │      5. check max_turns safety limit                            │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.1.1 服务依赖图（DAG）

```
AgentService (top-level orchestrator)
├── depends: ["a2.LLMService"]                    ← LLM 门面
│   ├── depends: ["a2.llm.fast.FastLLMService"]
│   │       └── protocol: ModelFallbackProvider → OpenAIProvider
│   ├── depends: ["a2.llm.capable.CapableLLMService"]
│   │       └── protocol: ModelFallbackProvider → AnthropicProvider
│   └── depends: ["a2.llm.code.CodeLLMService"]
│           └── protocol: ModelFallbackProvider → OpenAIProvider
├── depends: ["a2.ToolService"]                    ← 工具门面
│   ├── depends: ["a2.env.local.LocalToolService"]
│   │       └── protocol: LocalProtocol
│   ├── depends: ["a2.env.docker.DockerToolService"]
│   │       └── protocol: DockerProtocol
│   └── depends: ["a2.env.ssh.SSHToolService"]
│           └── protocol: SSHProtocol
├── depends: ["a2.ContextService"]
│   ├── depends: ["a2.MemoryService"]
│   │       ├── depends: ["a2.memory.l0.L0MetaRulesService"] → KVProtocol
│   │       ├── depends: ["a2.memory.l1.L1InsightIndexService"] → KVProtocol
│   │       ├── depends: ["a2.memory.l2.L2GlobalFactsService"] → KVProtocol
│   │       ├── depends: ["a2.memory.l3.L3TaskSkillsService"]
│   │       │       └── depends: ["a2.SkillService"]  ← 弱包装，读取技能数据
│   │       └── depends: ["a2.memory.l4.L4SessionArchiveService"] → CacheLayer
│   ├── depends: ["a2.LLMService"]                    ← 压缩时 LLM 调用
│   └── depends: ["a2.SkillService"]
│           └── protocol: CacheLayer → FileProtocol  ← 技能真正所有者，.user/skills/
├── depends: ["a2.PlannerService"]
├── depends: ["a2.SubagentService"]
│   ├── depends: ["a2.LLMService"]                ← provider/capability 路由
│   └── depends: ["a2.ToolService"]               ← 子智能体执行工具
├── depends: ["a2.MCPService"]
│   └── depends: ["a2.ToolService"]
└── depends: ["a2.SkillService"]                  ← system prompt 技能元数据
    └── protocol: CacheLayer → FileProtocol
```

每个模块是一个 `AppService` 子类，通过 `depends` 声明依赖关系。`load_from_config()` 在启动时解析 TOML 配置，自动实例化所有服务并按依赖顺序启动。

### 2.2 信息架构图（数据流）

```
                        ┌──────────────┐
                        │  用户输入     │
                        │ (text/query) │
                        └──────┬───────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                    System Prompt 组装流水线                        │
│                                                                  │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐            │
│  │ L0 规则  │  │ L1 索引  │  │ L2 事实  │  │ L3 技能  │            │
│  │ MetaRules│  │ Insight │  │  Facts  │  │  Skills │            │
│  │ (静态)   │  │ (路由表) │  │ (检索)  │  │ (触发)   │            │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘         │
│       └──────────────┴──────────────┴──────────────┘              │
│                              │                                    │
│                     ┌────────▼────────┐                           │
│                     │  ContextManager  │                           │
│                     │  组装 + 预算控制  │                           │
│                     └────────┬────────┘                           │
└──────────────────────────────┼────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                        Agent Loop 迭代                            │
│                                                                  │
│  ┌──────────────────────────────────────────────────────┐       │
│  │ messages[] (上下文载体)                                │       │
│  │                                                      │       │
│  │ [system] ← L0-L3 内容 + 动态 prompt                  │       │
│  │ [user]   ← 用户消息                                   │       │
│  │ [assistant] ← LLM 回复 (含 tool_calls)               │       │
│  │ [tool]   ← 工具执行结果                               │       │
│  │ [assistant] ← LLM 回复                               │       │
│  │ ...                                                  │       │
│  └──────────────────────────────────────────────────────┘       │
│                              │                                    │
│              ┌───────────────┼───────────────┐                   │
│              ▼               ▼               ▼                   │
│     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐          │
│     │  LLM 推理     │ │  工具执行     │ │  记忆写回     │          │
│     │  (推理+决策)   │ │  (操作世界)   │ │  (L2/L3/L4)  │          │
│     └──────────────┘ └──────────────┘ └──────────────┘          │
│                                                                  │
│  ┌────────────────────────────────────────────────────────┐     │
│  │ 3 层压缩管线 (当 token 接近预算时触发)                    │     │
│  │                                                        │     │
│  │  Layer 1: MicroCompact (规则驱动, 无 LLM)               │     │
│  │     → 截断旧 tool 输出, 移除旧 system-reminder          │     │
│  │                                                        │     │
│  │  Layer 2: SessionMemory (复用已有摘要, 零成本)            │     │
│  │     → 用缓存的会话摘要替换旧消息                         │     │
│  │                                                        │     │
│  │  Layer 3: FullLLMCompact (LLM 生成结构化摘要)            │     │
│  │     → 9 段式模板: Intent/Concepts/Files/Errors/...      │     │
│  └────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────┘
                               │
                               ▼
                        ┌──────────────┐
                        │  最终输出     │
                        │ (text/stream)│
                        └──────────────┘
```

### 2.3 记忆系统分层

```
┌─────────────────────────────────────────────────────────────────┐
│                     LayeredMemory (L0-L4)                        │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ L0: Meta Rules (元规则)                                  │   │
│  │ · 加载: 每次启动, 全量                                    │   │
│  │ · 来源: CLAUDE.md / AGENTS.md / 系统 prompt               │   │
│  │ · Token: ~500                                            │   │
│  │ · bollydog: Protocol(无需存储, 启动时扫描文件)              │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ L1: Insight Index (洞察索引)                              │   │
│  │ · 加载: 每次启动, 元数据                                   │   │
│  │ · 内容: 技能名 + 描述 (路由表)                             │   │
│  │ · Token: ~100/技能                                       │   │
│  │ · bollydog: KVProtocol                                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ L2: Global Facts (全局事实)                               │   │
│  │ · 加载: LLM 辅助语义检索                                  │   │
│  │ · 内容: 用户偏好 / 项目知识 / 架构决策                      │   │
│  │ · Token: ~2K                                             │   │
│  │ · bollydog: KVProtocol + LLM 检索                        │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ L3: Task Skills (任务技能)                                │   │
│  │ · 加载: 关键词匹配时触发                                   │   │
│  │ · 内容: 结晶的执行路径 (工作流)                             │   │
│  │ · Token: ~2K/技能                                        │   │
│  │ · bollydog: FileProtocol（技能以 .md 文件存储）             │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ L4: Session Archive (会话档案)                            │   │
│  │ · 加载: 最近消息 (受 token 预算约束)                       │   │
│  │ · 内容: 当前对话历史                                       │   │
│  │ · Token: 可变                                             │   │
│  │ · bollydog: CompositeProtocol (MemoryProtocol + FileProtocol) │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、关键设计点

### 3.1 Agent Loop — 心跳

Agent 的本质是一个 `while` 循环：发送消息 → 收到回复 → 判断是否继续 → 执行工具 → 把结果送回。所有机制（规划、压缩、子智能体、技能）都是在这个循环的"缝隙"里插入逻辑。

**与 bollydog 的集成点：** `AgentCommand.__call__` 是 async generator，yield dict（流式 chunk）到 Hub 的 `_run_gen`，Hub 通过 `StreamState` 将 chunk 传递给前端。这完全复用了 bollydog 已有的 Countdown 命令的模式。

> 详细设计: [01-agent-loop.md](01-agent-loop.md)

### 3.2 工具系统 — 双手

设计原则：**开放封闭**——对新工具开放（注册一行），对循环封闭（dispatch 逻辑不变）。

**核心设计决策：环境即服务。** 每个执行环境（Local/Docker/SSH）是一个独立的 AppService，持有自己的 Protocol。ToolService 作为门面，统一管理工具注册和执行路由。

- Tool 继承 BaseCommand，`__call__` 直接可调用，不经过 Hub dispatch
- Tool 是协议无关的薄包装，通过 `self._service` 回调执行
- TerminalProtocol 定义完整环境能力：`execute` / `read_file` / `write_file` / `upload` / `download`
- ToolService 通过 `env` 参数路由到对应的环境服务
- Tool 声明 `provider`（精确指定）和 `capability`（模糊指定）属性，Agent Loop 通过两层解析路由到正确的 LLM
- 11 个内置工具覆盖文件操作、Shell 执行、Python 执行、跨环境传输、子智能体委派和用户交互

> 详细设计: [02-tool-system.md](02-tool-system.md)

### 3.3 分层记忆 — 长期记忆

GenericAgent 的核心创新：5 层记忆（L0-L4），每层有不同的持久化策略、访问模式和 token 成本。

**核心设计决策：每层是独立的 AppService，各自管理自己的 Protocol。MemoryService 作为门面，通过 depends 声明依赖所有子服务。** 与 LLMService / ToolService 的"门面 → 子服务 → Protocol"模式完全对称。

- L0MetaRulesService(AppService) → KVProtocol：系统规则，启动时扫描
- L1InsightIndexService(AppService) → KVProtocol：技能索引，路由表
- L2GlobalFactsService(AppService) → KVProtocol：全局事实，多模式检索（BM25/LLM/Embedding 独立开关）
- L3TaskSkillsService(AppService) → FileProtocol：任务技能，结晶时通知 L1 更新索引（服务间调用）
- L4SessionArchiveService(AppService) → CacheLayer → FileProtocol：会话档案

**跨层操作：** L3TaskSkillsService 是 SkillService 的弱包装，通过 depends 读取 SkillService 的数据。SkillService 独立存在，持有 CompositeProtocol（CacheLayer + FileProtocol）做持久化。

> 详细设计: [03-layered-memory.md](03-layered-memory.md)

### 3.4 技能系统 — 专业知识库

解决 token 预算问题：不是把所有知识塞进 system prompt（~20K 浪费），而是只放元数据（~100 tok/技能），正文按需加载。

**核心设计决策：SkillService(AppService) 是技能的真正所有者，持有 CompositeProtocol 做持久化。** L3TaskSkillsService 是 Memory 系统视角的弱包装，通过 depends 读取 SkillService 的数据。Skill 数据模型使用 Pydantic。技能存储在 `.user/skills/` 目录下。

**三级渐进式披露：**
- Level 1: 元数据（始终在 system prompt）
- Level 2: 正文（触发时加载，~2K tok）
- Level 3: 资源/脚本（按需引用，脚本只返回输出）

**自演化：** 任务成功后，SkillService 通过 LLMService（capability='fast'）提取执行路径结晶为可复用技能，通过 L3TaskSkillsService 持久化。L3 内部通知 L1 更新索引（服务间调用）。

> 详细设计: [04-skill-system.md](04-skill-system.md)

### 3.5 上下文管理 — 上下文工程

Agent 框架设计的核心就是在 Agent Loop 中管理上下文。**ContextService(AppService)** 负责：
1. 组装 L0-L4 内容到 messages[]（通过 MemoryService）
2. 控制 token 预算
3. 触发 3 层压缩（通过 LLMService 做 LLM 压缩）

**3 层压缩管线：**
- MicroCompact：规则驱动，无 LLM 调用，截断旧 tool 输出
- SessionMemory：复用已有摘要，零成本
- FullLLMCompact：LLM 生成 9 段式结构化摘要（隐式 CoT + 反工具调用保护）

> 详细设计: [05-context-management.md](05-context-management.md)

### 3.6 LLM Provider — 推理引擎

LLMProvider 是 Protocol（资源适配器），LLMService 是 AppService 门面。与工具系统完全对称：ToolService 按 env 路由到环境服务，LLMService 按 capability 路由到能力服务。

**两层选择机制：** Tool 可以用两种方式指定 LLM，优先级从高到低：
1. `tool.provider = 'claude-opus'` → 直接用该 provider 实例（最精确）
2. `tool.capability = 'capable'` → 路由到对应能力服务（较模糊）
3. 都没指定 → 用 Agent Loop 当前默认 capability

- LLMService 门面：维护 `_providers` 注册表和 `_capabilities` 映射，`resolve()` 方法实现两层解析
- FastLLMService / CapableLLMService / CodeLLMService：每个能力服务持有自己的 Protocol 链
- OpenAIProvider / AnthropicProvider：具体 API 适配器
- ModelFallbackProvider：Protocol 包装器，主模型连续失败时熔断降级
- Tool.provider / Tool.capability：工具声明 LLM 需求，Agent Loop 按两层解析路由

> 详细设计: [06-llm-provider.md](06-llm-provider.md)

### 3.7 规划器 — 导航仪

**核心洞察：在 bollydog 中，`yield SubCommand()` 就是装饰器模式。** 三层都是 BaseCommand 子类，通过 yield 委托连接。

三层 Command：
- **ReActStepCommand**（基础层，永远存在）：Reason→Act→Observe 循环
- **PlanCommand**（可选）：生成任务分解列表，存入 session
- **ReflexionCommand**（可选）：失败时反思，`yield ReActStepCommand()` 重试

**TaskList：** Pydantic 模型，session 管理运行时状态，Protocol 持久化最终结果。扁平列表，无依赖关系。

**开关控制：** `use_planning` / `use_reflexion` 两个布尔开关，不需要启发式判断。

> 详细设计: [07-planner.md](07-planner.md)

### 3.8 子智能体 — 分身

核心思想：**用计算换上下文空间。** 子 Agent 消耗额外 LLM 调用，但换来父 Agent 上下文的纯净。

子智能体支持 `provider` 和 `capability` 参数，通过 LLMService 的两层解析路由到正确的 LLM。优先级：provider > capability > agent_def 默认值。

三个内置子智能体：
- **Explore**：只读搜索，capability='fast'，快速便宜
- **Plan**：只读规划，capability='capable'，强推理
- **General**：全工具，capability='fast'（可通过 spawn 的 provider/capability 参数覆盖）

防递归：子智能体的工具列表不包含 `subagent` 工具。

> 详细设计: [08-subagent.md](08-subagent.md)

### 3.9 MCP 集成 — 外部工具生态

**核心设计决策：MCPServer 是 Protocol 子类（资源适配器），MCPService(AppService) 只做生命周期管理。** MCP 服务器是外部资源（进程 + 连接），Protocol 模式天然适配：`adapter` 持有 MCP 客户端连接，`on_start()`/`on_stop()` 管理生命周期。

- MCPServerProtocol(Protocol)：MCP 连接适配器，`adapter` = MCP 客户端，`call_tool()` 是资源访问
- MCPService(AppService)：只做管理——使用 `smart_import()` 从 TOML config 动态创建 Protocol 实例，发现工具，注册 MCPToolWrapper
- MCPToolWrapper(Tool)：持有 Protocol 引用，委托执行到 `server_protocol.call_tool()`
- `module` 字段支持自定义 MCPServerProtocol 子类（自定义传输、认证、解析）
- 工具命名空间：`mcp__{server}__{tool}`，避免冲突

> 详细设计: [09-mcp-integration.md](09-mcp-integration.md)

---

## 四、bollydog 复用与适配

bollydog **零修改**。TerminalProtocol 及其 backend 实现放在 A2 的 `tools/protocol.py` 中，先做模式验证。验证通过后再考虑提升到 bollydog 的 `adapters/` 层。

### 4.1 TerminalProtocol（A2 侧实现）

`A2/tools/protocol.py` 定义执行环境的完整能力接口，继承 bollydog 的 `Protocol` 基类：

| 方法 | 必须/可选 | 用途 |
|------|----------|------|
| `execute(command, timeout)` | 必须 | 执行 shell 命令 |
| `read_file(path, offset, limit)` | 必须 | 读取文件内容 |
| `write_file(path, content)` | 必须 | 写入文件内容 |
| `upload(local, remote)` | 可选 | 本地文件 → 该环境 |
| `download(remote, local)` | 可选 | 该环境 → 本地文件 |

| 类 | self.adapter | 特点 |
|---|---|---|
| TerminalProtocol | — | 抽象基类，继承 Protocol |
| LocalProtocol | None | 直接操作文件系统 |
| DockerProtocol | container_id | 文件操作通过 docker exec/cp |
| SSHProtocol | None | 文件操作通过 ssh/scp |

> 详见 [12-bollydog-modifications.md](12-bollydog-modifications.md)

### 4.2 不修改 bollydog 的设计决策

| A2 需求 | 为什么不改 bollydog | A2 层面的解法 |
|---------|-------------------|-------------|
| 流式 chunk 有类型信息 | StreamState 是通用组件，不应绑定 agent 语义 | AgentCommand 的 `__call__` yield `{'type': 'text', 'content': ...}` dict，Hub 的 `else` 分支已能处理 |
| Hub 显式处理 agent yield | `_run_gen` 的 `else` 分支已能处理任意 value | A2 自己控制 yield 格式，不需要 Hub 区分 |
| agent context proxy | 上下文在 Session 中控制 | 使用 CompositeProtocol（MemoryProtocol + FileProtocol）管理会话上下文 |
| L3 技能存储 | FileProtocol 更适合（技能是 .md 文件） | L3 使用 FileProtocol，技能目录即存储 |

### 4.3 完全可复用的 bollydog 机制

| 机制 | 复用方式 |
|------|---------|
| `BaseCommand.__init_subclass__` | Tool 继承 BaseCommand，自动注册到 MessageRegistry |
| `BaseCommand.__call__` | Tool 重定义 `__call__` 为直接可调用（不经过 dispatch） |
| `BaseCommand.is_async_gen` | 自动检测 `__call__` 是否为 async generator |
| `AppService.create_from` | 已支持 config（bollydog 侧额外支持） |
| Hub `_execute` | before → runner → after 流程 |
| Hub `_run_gen` `else` 分支 | Agent yield dict 已能正确处理 |
| Exchange topic 匹配 | Agent 事件发布（如 SubagentEvent） |
| Session KV + CompositeProtocol | L4 SessionArchive（内存缓存 + 文件持久化） |
| FileProtocol | L3 TaskSkills（技能以 .md 文件存储） |
| Queue qos=1 | 长任务的可靠队列 |
| Protocol `_build_protocol` 嵌套 | LayeredMemory L0-L4 链式组装 + LLMProvider 降级链 |
| `Protocol` 基类 | LLMProvider 继承，TerminalProtocol 继承，MCPServerProtocol 继承，复用生命周期和 adapter 模式 |
| `smart_import()` | MCPService 从 TOML config 动态加载 Protocol 子类 |
| `AppService._load_commands` | 动态导入 agent 命令模块 |
| `AppService.resolve_app` | 基于 destination 的路由 |

---

## 五、设计疑问与待决事项

以下是设计过程中发现的需要讨论或进一步明确的问题：

### 5.1 Tool 继承 BaseCommand 的实现细节

**决策：** Tool 继承 BaseCommand，重定义 `__call__` 使其实例可直接调用。

**实现要点：**
- Tool 基类继承 BaseCommand，具体工具类继承 Tool 基类
- Tool 的 `__call__` 签名为 `async def __call__(self, **kwargs) -> str`，直接执行工具逻辑
- 不依赖 Hub dispatch 管线，不依赖 `_with_context` 上下文
- 在 Tool 基类上增加 `to_schema()` 方法，返回 OpenAI function-calling 格式
- `ToolService.execute()` 直接调用 `tool(**kwargs)` 而不经过 `hub.dispatch`
- **destination 保持默认行为**（`_._.{alias}`），不需要 `abstract=True` 跳过

### 5.2 L4 SessionArchive 使用 CompositeProtocol

**决策：** L4 使用 bollydog 的 CompositeProtocol（CacheLayer + FileProtocol），不在 globals.py 增加 agent proxy。

**实现：**
- Session 的默认 MemoryProtocol 替换为 CompositeProtocol
- 内层：MemoryProtocol（内存缓存，快速读写）
- 外层：FileProtocol（文件持久化，重启后恢复）
- bollydog 的 `CacheLayer` 已实现这个模式（内存缓存 + 内层持久化，flush-on-threshold）

**TOML 配置：**
```toml
[a2.memory.MemoryService.protocol.protocol.protocol.protocol.protocol]
module = "bollydog.adapters.composite.CacheLayer"
flush_threshold = 100

[a2.memory.MemoryService.protocol.protocol.protocol.protocol.protocol.protocol]
module = "bollydog.adapters.file.LocalFileProtocol"
path = ".agent/session/"
```

### 5.3 规划器的层选择

**决策：** 开关控制，不使用启发式。

**当前设计：** `use_planning` / `use_reflexion` 两个布尔开关，在 PlannerService 配置中设置。

**备注：** 后期可增加启发式自动判断或 LLM 辅助判断作为可选覆盖层。

### 5.4 技能结晶的质量控制

**决策：** 设置结晶门槛和数量上限，具体参数备注到代码处。

**控制策略：**
- 结晶门槛：只有多步任务（≥3 次工具调用）才触发结晶
- 技能数量上限：L1 索引最多 20 个技能
- 技能淘汰：success_rate 低于阈值的技能自动降级或删除

**备注：** 结晶门槛、上限、淘汰阈值等参数备注到 `skill.py` 的 `crystallize()` 方法处，便于后续调优。

### 5.5 子智能体的 token 成本控制

**决策：** 硬性限制 + 结果截断，具体参数备注到代码处。

**控制策略：**
- 子智能体硬性 max_turns 限制（Explore: 30, Plan: 20, General: 30）
- 子智能体的工具结果截断到 50K 字符
- 父智能体只能获取摘要，不获取中间过程

**备注：** max_turns、截断长度等参数备注到 `subagent.py` 的 AgentDefinitions 处，便于后续调优。

### 5.6 MCP 服务器的生命周期管理

**决策：** 方案 A — AgentService 启动时启动所有 MCP 服务器，关闭时全部终止。

**原因：** MCP 服务器启动可能较慢（如 `npx` 需要下载），提前启动避免首次调用延迟。空闲关闭可作为后续优化。

### 5.7 多轮对话中的记忆一致性

**决策：** 采用类似 Claude Code 的 extractMemories 逻辑。

**实现：**
- L2 写入操作在 Agent Loop 的每轮结束后检查
- 使用 LLM 判断当前对话是否包含值得记忆的信息（类似 Claude Code 的 `extractMemories`）
- 写入后下一轮 `build_context` 自动包含新事实

---

## 六、分阶段执行计划

按 DAG 拓扑排序决定构建顺序。每一层只依赖前一层已构建的服务。

```
Layer 0 (无依赖)          Layer 1 (依赖 L0)           Layer 2 (依赖 L0+L1)       Layer 3 (依赖 L2)    Layer 4 (顶层)
─────────────────         ──────────────────          ──────────────────          ──────────────       ──────────
LLMService                L0MetaRulesService          ContextService              AgentService         config/cli/http
├─ FastLLMService         L1InsightIndexService       └─ MemoryService            └─ 7 子服务
├─ CapableLLMService      L2GlobalFactsService            ├─ L0-L2, L4
└─ CodeLLMService         L4SessionArchiveService         └─ L3 → SkillService
                          SubagentService
ToolService               MCPService
├─ LocalToolService
├─ DockerToolService
└─ SSHToolService

SkillService              PlannerService
(CompositeProtocol)
```

### Phase 1: 叶子服务（零依赖）
> 目标：所有不依赖其他 A2 服务的 AppService。每个可独立实例化和测试。

**1. LLMService** (`llm/` + `llm/fast/` + `llm/capable/` + `llm/code/`)
- `llm/service.py` — LLMService(AppService) 门面，provider 注册表 + 两层解析（provider > capability > default）
- `llm/provider.py` — LLMProvider(Protocol) + OpenAIProvider + AnthropicProvider
- `llm/fallback.py` — ModelFallbackProvider（熔断降级包装器）
- `llm/fast/service.py` — FastLLMService(AppService)
- `llm/capable/service.py` — CapableLLMService(AppService)
- `llm/code/service.py` — CodeLLMService(AppService)

**2. ToolService** (`tools/` + `env/`)
- `tools/service.py` — ToolService(AppService) 门面，工具注册 + 环境路由
- `tools/tool.py` — Tool(BaseCommand)、Toolset
- `tools/protocol.py` — TerminalProtocol + LocalProtocol + DockerProtocol + SSHProtocol
- `tools/builtin.py` — 11 个内置工具（含 upload/download）
- `tools/commands.py` — RegisterTool、ListTools 命令
- `env/local/service.py` — LocalToolService(AppService)，持有 LocalProtocol
- `env/docker/service.py` — DockerToolService(AppService)，持有 DockerProtocol
- `env/ssh/service.py` — SSHToolService(AppService)，持有 SSHProtocol

**3. SkillService** (`skills/`)
- `skills/service.py` — SkillService(AppService)，无外部依赖，持有 CompositeProtocol（CacheLayer + FileProtocol）
- `skills/skill.py` — Skill(BaseModel) Pydantic 数据模型
- 技能存储在 `.user/skills/`，通过 CompositeProtocol 做缓存 + 文件持久化
- 注：结晶功能需要 LLMService，通过 Command 化调用（待实现），不声明 depends

**4. PlannerService** (`planner/`)
- `service.py` — PlannerService(AppService)：配置开关 + TaskList 持久化
- `models.py` — Task、TaskList（Pydantic）
- `commands.py` — RunAgentCommand、PlanCommand、ReflexionCommand、ReActStepCommand

**验证：** 每个服务可独立实例化，无外部依赖解析失败。

> 详细: [06-llm-provider.md](06-llm-provider.md) + [02-tool-system.md](02-tool-system.md) + [04-skill-system.md](04-skill-system.md) + [07-planner.md](07-planner.md)

### Phase 2: 单层依赖服务（只依赖 Phase 1）
> 目标：依赖 Phase 1 叶子服务的服务。构建后可独立测试与叶子服务的交互。

**6. L0MetaRulesService** (`memory/l0/`)
- `memory/l0/service.py` — L0MetaRulesService(AppService)，启动时扫描 CLAUDE.md
- Protocol: KVProtocol

**7. L1InsightIndexService** (`memory/l1/`)
- `memory/l1/service.py` — L1InsightIndexService(AppService)，技能元数据路由表
- Protocol: KVProtocol

**8. L2GlobalFactsService** (`memory/l2/`)
- `memory/l2/service.py` — L2GlobalFactsService(AppService)，BM25/LLM/Embedding 多模式检索
- Protocol: KVProtocol

**9. L4SessionArchiveService** (`memory/l4/`)
- `memory/l4/service.py` — L4SessionArchiveService(AppService)，CacheLayer + FileProtocol
- Protocol: CacheLayer → FileProtocol

**10. SubagentService** (`subagent/`)
- `subagent/service.py` — SubagentService(AppService)，depends: LLMService
- `subagent/agent_def.py` — ExploreAgent、PlanAgent、GeneralAgent

**11. MCPService** (`mcp/`)
- `mcp/service.py` — MCPService(AppService)，只做生命周期管理 + 工具注册
- `mcp/server.py` — MCPServerProtocol(Protocol)，MCP 连接适配器（adapter = MCP 客户端）
- `mcp/wrapper.py` — MCPToolWrapper(Tool)，持有 Protocol 引用，委托执行

**验证：** 服务间依赖正确解析；SubagentService 能通过 LLMService 路由；MCPService 能注册工具到 ToolService。

> 详细: [03-layered-memory.md](03-layered-memory.md) + [08-subagent.md](08-subagent.md) + [09-mcp-integration.md](09-mcp-integration.md)

### Phase 3: 多层依赖服务（依赖 Phase 1+2）
> 目标：有跨层依赖的服务。需要 Phase 1+2 的服务全部就绪。

**12. MemoryService** (`memory/`)
- `memory/service.py` — MemoryService(AppService) 门面，depends 5 个子服务，get_context 组装
- 注：L3TaskSkillsService 是 SkillService 的弱包装，depends: SkillService（Phase 1）
- 注：L0/L1/L2/L4 在 Phase 2 已构建

**13. ContextService** (`context/`)
- `context/service.py` — ContextService(AppService)，depends: MemoryService + LLMService
- 上下文组装 + 3 层压缩（MicroCompact/SessionMemory/FullLLMCompact）

**验证：** MemoryService.get_context 正确组装 L0-L4；ContextService.build_context 正确控制 token 预算。

> 详细: [03-layered-memory.md](03-layered-memory.md) + [05-context-management.md](05-context-management.md)

### Phase 4: 顶层编排
> 目标：AgentService + AgentCommand 组装所有服务。

**14. AgentService** (`agent/`)
- `agent/service.py` — AgentService(AppService)，depends: 全部子服务
- `agent/command.py` — AgentCommand(BaseCommand)，async generator agent loop
- `agent/commands.py` — Chat、ClearSession 命令

**关键设计点：**
- AgentCommand 的 `__call__` 是 async generator，yield dict 到 Hub 的 `_run_gen`
- AgentCommand 通过 `self._service` 访问所有子服务（构造函数注入）
- AgentService 在 `on_started` 中解析所有依赖引用

**验证：** 完整 agent loop 运行：用户输入 → LLM 推理 → 工具执行 → 流式输出。

> 详细: [01-agent-loop.md](01-agent-loop.md)

### Phase 5: 入口与集成
> 目标：生产就绪的 Agent 框架。

**15. 配置与入口** (`./`)
- `config.py` — 配置加载
- `cli.py` — CLI REPL（click）
- `http.py` — HTTP/WS 入口（FastAPI）
- `example.toml` — 完整示例配置
- 测试套件

**验证：** CLI/HTTP/WS 入口可用；TOML 配置驱动完整服务组装；示例配置可直接运行。

> 详细: [11-configuration.md](11-configuration.md) + [14-verification.md](14-verification.md)

---

## 七、文件清单

### bollydog — 零修改

bollydog 核心代码零修改。TerminalProtocol 放在 A2 的 `tools/protocol.py` 中实现，先做模式验证。

### A2 新建（10 个模块，~65 个文件）

每个模块是一个服务目录，包含 `service.py`（AppService 子类）+ 协议/模型/命令文件。

| 模块 | 目录 | 文件 | 用途 | 阶段 |
|------|------|------|------|------|
| LLM | `llm/` | `__init__.py` `service.py` `provider.py` `fallback.py` + `fast/` `capable/` `code/` 各含 `__init__.py` `service.py` | LLMService 门面 + 3 个能力服务 + LLMProvider(Protocol) | P1 |
| Tools | `tools/` | `__init__.py` `service.py` `tool.py` `protocol.py` `builtin.py` `commands.py` | ToolService 门面 + TerminalProtocol + 11 个内置工具 | P1 |
| Env | `env/` | `__init__.py` `local/service.py` `docker/service.py` `ssh/service.py` | 环境服务: 每个执行环境一个 AppService + Protocol | P1 |
| Memory | `memory/` | `__init__.py` `service.py` `commands.py` + `l0/` `l1/` `l2/` `l3/` `l4/` 各含 `__init__.py` `service.py` | MemoryService 门面 + 5 个层级服务(AppService) + NotifyIndexUpdateCommand | P1 |
| Planner | `planner/` | `__init__.py` `service.py` `models.py` `commands.py` | PlannerService: 开关 + TaskList + 三层 Command | P1 |
| Skills | `skills/` | `__init__.py` `service.py` `skill.py` `commands.py` | SkillService: 技能管理、结晶 | P2 |
| Subagent | `subagent/` | `__init__.py` `service.py` `agent_def.py` `commands.py` | SubagentService: 子智能体委派 | P2 |
| MCP | `mcp/` | `__init__.py` `service.py` `server.py` `wrapper.py` | MCPService(管理) + MCPServerProtocol(Protocol) + MCPToolWrapper(Tool) | P2 |
| Context | `context/` | `__init__.py` `service.py` `manager.py` `commands.py` | ContextService: 上下文组装、3 层压缩 | P3 |
| Agent | `agent/` | `__init__.py` `service.py` `command.py` `commands.py` | AgentService: 顶层编排、Agent Loop | P4 |
| 入口 | `./` | `__init__.py` `config.py` `cli.py` `http.py` | 包初始化、配置、CLI/HTTP 入口 | P5 |

> 详细文件树: [13-file-structure.md](13-file-structure.md)

---

## 八、验证策略

- **单元测试：** 每个组件独立测试（Tool、Memory、Context、LLM Provider）
- **集成测试：** 子系统交互（Agent Loop + Tool + LLM）
- **端到端测试：** 完整工作流（CLI 输入 → Agent 执行 → 输出）
- **对抗测试：** 无限循环、上下文溢出、工具幻觉、路径逃逸、递归子智能体

> 详细: [14-verification.md](14-verification.md)

---

## 九、设计审查 — 矛盾与问题清单

基于全部设计文档的交叉审查，按严重程度分类。

### CRITICAL（运行时崩溃）

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| C1 | SubagentTool 引用不存在的 `self._subagent_service` | 02-tool-system.md | Tool 基类只有 `self._service`（ToolService），运行时 AttributeError |
| C2 | SkillService 调用 `self._llm.chat()` 但 `depends = []` | 04-skill-system.md | `self._llm` 不会被框架注入，运行时 AttributeError |

### HIGH（设计违反）

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| H1 | AgentService 调用 `self._skill_service` 但 depends 中无 SkillService | 01-agent-loop.md | `build_system_prompt()` 直接调用，depends 缺失 |
| H2 | L3 → L1 跨层通知无 depends 也无 Command | 03-layered-memory.md | "自动通知 L1InsightIndexService.add_skill()" 未声明调用机制 |
| H3 | SubagentTool 直接调用 SubagentService | 02-tool-system.md | Tool → Service 直接调用，应通过 Command |

### MEDIUM（文档不一致）

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| M1 | PlannerService 配置字段名不匹配 | 07 vs 11 | 代码：`use_planning`/`use_reflexion`；TOML：`default_strategy`/`task_dag_path` |
| M2 | SubagentService depends 不一致 | 08 vs DAG | 架构图只写 LLMService；代码写 LLMService + ToolService |

### LOW（模式偏差，可接受）

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| L1 | MCPService 手动调 `server.on_start()` | 09-mcp-integration.md | 需要动态 N 个 Protocol 实例，已文档化理由 |
| L2 | ToolService 含 `_ask_user` 业务逻辑 | 02-tool-system.md | 门面中混入用户交互，可后续抽取 |

---

## 十、Command 分离 — 跨服务调用治理

### 核心原则

在 bollydog 中：
- **Command 可以通过 `app.resolve()` 访问任意 Service**（Command 运行在 Hub 上下文中）
- **Service 不应直接调用另一个 Service**（绕过 Hub 调度）
- **当 Service A 需要调用 Service B 时**：将操作封装为 B 暴露的 Command，由调用方 yield 到 Hub

### 需要 Command 化的调用

| 调用方 | 被调用方 | Command | 触发场景 |
|--------|----------|---------|----------|
| SkillService | LLMService | ExtractPatternCommand | 结晶时提取执行模式 |
| CrystallizeSkillCommand | L1InsightIndexService | NotifyIndexUpdateCommand | 技能变更时更新索引 |
| SubagentTool | SubagentService | SpawnSubagentCommand | Agent 委派子智能体 |
| AgentService | SkillService | 加 depends（非 Command） | system prompt 组装 |

### 调用链（Command 化后）

```
Hub dispatch
└── AgentCommand
    ├── yield PlanCommand()
    │   └── app.resolve(LLMService).chat()           ← Command 内，允许
    │
    └── yield ReActStepCommand(task)
        ├── app.resolve(LLMService).chat()            ← Command 内，允许
        ├── app.resolve(ToolService).execute()         ← Command 内，允许
        │   └── SubagentTool.execute()
        │       └── yield SpawnSubagentCommand()       ← Hub 调度
        │           └── SubagentService.spawn()        ← depends 调用
        │
        └── (任务成功后)
            └── yield CrystallizeSkillCommand()        ← Hub 调度
                ├── yield ExtractPatternCommand()      ← Hub 调度
                │   └── app.resolve(LLMService).chat()
                ├── skill_service.save_skill()
                └── yield NotifyIndexUpdateCommand()   ← Hub 调度
                    └── L1.add_skill()
```

> 详细执行计划: [15-command-separation.md](15-command-separation.md)
