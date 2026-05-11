# 13 — File Structure

## Complete File Tree for A2 Agent Module

每个功能模块是一个独立的服务目录，包含 `service.py`（AppService 子类）+ 协议/模型/命令文件。模块间通过 `depends` 声明依赖关系，由 `load_from_config()` 在启动时自动解析和组装。

```
A2/                                          # 项目根目录（独立 Python 包）
├── __init__.py                              # Package init
├── config.py                                # Config loading
├── cli.py                                   # CLI REPL entrypoint
├── http.py                                  # HTTP/WS entrypoint
│
├── llm/                                     # LLMService (门面, depends: 3 个能力服务)
│   ├── __init__.py
│   ├── service.py                           # LLMService(AppService) 门面
│   ├── provider.py                          # LLMProvider(Protocol) + OpenAIProvider + AnthropicProvider
│   ├── fallback.py                          # ModelFallbackProvider(Protocol): 熔断降级
│   ├── fast/
│   │   ├── __init__.py
│   │   └── service.py                       # FastLLMService(AppService): 快速能力
│   ├── capable/
│   │   ├── __init__.py
│   │   └── service.py                       # CapableLLMService(AppService): 强推理能力
│   └── code/
│       ├── __init__.py
│       └── service.py                       # CodeLLMService(AppService): 代码能力
│
├── tools/                                   # ToolService (门面, depends: 3 个环境服务)
│   ├── __init__.py
│   ├── service.py                           # ToolService(AppService) 门面
│   ├── tool.py                              # Tool(BaseCommand) 基类
│   ├── protocol.py                          # TerminalProtocol + Local/Docker/SSH Protocol
│   ├── builtin.py                           # 11 个内置工具（含 upload/download）
│   ├── permission.py                        # PermissionRule, PermissionConfig 权限检查
│   └── commands.py                          # RegisterTool, ListTools
│
├── env/                                     # 环境服务（EnvironmentService 基类 + 各环境子类）
│   ├── __init__.py
│   ├── base.py                              # EnvironmentService(AppService, abstract=True) 基类
│   ├── local/
│   │   ├── __init__.py
│   │   └── service.py                       # LocalToolService(EnvironmentService)
│   ├── docker/
│   │   ├── __init__.py
│   │   └── service.py                       # DockerToolService(EnvironmentService)
│   └── ssh/
│       ├── __init__.py
│       └── service.py                       # SSHToolService(EnvironmentService)
│
├── memory/                                  # MemoryService (门面, depends: 5 个层级服务)
│   ├── __init__.py
│   ├── service.py                           # MemoryService(AppService) 门面
│   ├── commands.py                          # NotifyIndexUpdateCommand
│   ├── l0/
│   │   ├── __init__.py
│   │   └── service.py                       # L0MetaRulesService(AppService) + KVProtocol
│   ├── l1/
│   │   ├── __init__.py
│   │   └── service.py                       # L1InsightIndexService(AppService) + KVProtocol
│   ├── l2/
│   │   ├── __init__.py
│   │   └── service.py                       # L2GlobalFactsService(AppService) + KVProtocol
│   ├── l3/
│   │   ├── __init__.py
│   │   └── service.py                       # L3TaskSkillsService(AppService) + FileProtocol
│   └── l4/
│       ├── __init__.py
│       └── service.py                       # L4SessionArchiveService(AppService) + CacheLayer
│
├── planner/                                 # PlannerService (无 depends)
│   ├── __init__.py
│   ├── service.py                           # PlannerService(AppService) 配置开关 + 持久化
│   ├── models.py                            # Task, TaskList (Pydantic)
│   └── commands.py                          # RunAgent, Plan, Reflexion, ReActStep Commands
│
├── skills/                                  # SkillService (无 depends, CompositeProtocol)
│   ├── __init__.py
│   ├── service.py                           # SkillService(AppService) 门面
│   ├── skill.py                             # Skill(BaseModel) Pydantic 数据模型
│   └── commands.py                          # ExtractPatternCommand, CrystallizeSkillCommand
│
├── subagent/                                # SubagentService (depends: LLMService, ToolService)
│   ├── __init__.py
│   ├── service.py                           # SubagentService(AppService)
│   ├── agent_def.py                         # ExploreAgent, PlanAgent, GeneralAgent
│   └── commands.py                          # SpawnSubagentCommand
│
├── mcp/                                     # MCPService (depends: ToolService)
│   ├── __init__.py
│   ├── service.py                           # MCPService(AppService): 生命周期管理+工具注册
│   ├── server.py                            # MCPServerProtocol(Protocol): MCP连接适配器
│   └── wrapper.py                           # MCPToolWrapper(Tool): 委托到Protocol
│
├── context/                                 # ContextService (depends: MemoryService + LLMService)
│   ├── __init__.py
│   └── service.py                           # ContextService(AppService), 3-layer compact
│
└── agent/                                   # AgentService (depends: 全部 7 个子服务)
    ├── __init__.py
    ├── service.py                           # AgentService(AppService)
    ├── command.py                           # AgentCommand(BaseCommand) - agent loop
    └── commands.py                          # Chat, ClearSession
```

## Module Descriptions

### LLM — LLMService (10 files, ~350 lines)

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | ~5 | Exports |
| `service.py` | ~60 | LLMService(AppService) 门面：capability 路由 |
| `provider.py` | ~100 | LLMProvider(Protocol) + OpenAIProvider + AnthropicProvider |
| `fallback.py` | ~40 | ModelFallbackProvider(Protocol): 熔断降级包装器 |
| `fast/__init__.py` | ~5 | Exports |
| `fast/service.py` | ~30 | FastLLMService(AppService): 快速能力 |
| `capable/__init__.py` | ~5 | Exports |
| `capable/service.py` | ~30 | CapableLLMService(AppService): 强推理能力 |
| `code/__init__.py` | ~5 | Exports |
| `code/service.py` | ~30 | CodeLLMService(AppService): 代码能力 |

### Tools — ToolService (7 files, ~600 lines)

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | ~5 | Exports |
| `service.py` | ~80 | ToolService(AppService) 门面：工具注册 + 环境路由 + schema 注入 + 权限检查 |
| `tool.py` | ~80 | Tool(BaseCommand) 基类、Toolset |
| `protocol.py` | ~180 | TerminalProtocol 基类 + LocalProtocol + DockerProtocol + SSHProtocol |
| `builtin.py` | ~250 | 11 个内置工具（协议无关薄包装，含 upload/download） |
| `permission.py` | ~50 | PermissionRule, PermissionConfig：TOML 配置驱动的权限检查 |
| `commands.py` | ~30 | RegisterTool, ListTools 命令 |

### Env — 环境服务 (9 files, ~150 lines)

| File | Lines | Purpose |
|------|-------|---------|
| `env/__init__.py` | ~5 | Exports |
| `env/base.py` | ~40 | EnvironmentService(AppService, abstract=True) 基类：统一 execute/read_file/write_file/upload/download |
| `env/local/__init__.py` | ~5 | Exports |
| `env/local/service.py` | ~10 | LocalToolService(EnvironmentService)：仅声明 domain/alias |
| `env/docker/__init__.py` | ~5 | Exports |
| `env/docker/service.py` | ~10 | DockerToolService(EnvironmentService)：仅声明 domain/alias |
| `env/ssh/__init__.py` | ~5 | Exports |
| `env/ssh/service.py` | ~10 | SSHToolService(EnvironmentService)：仅声明 domain/alias |

### Memory — MemoryService (13 files, ~445 lines)

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | ~5 | Exports |
| `service.py` | ~60 | MemoryService(AppService) 门面：depends 5 个子服务、get_context 组装 |
| `commands.py` | ~25 | NotifyIndexUpdateCommand：技能变更时通知 L1 更新索引 |
| `l0/__init__.py` | ~5 | Exports |
| `l0/service.py` | ~50 | L0MetaRulesService(AppService): CLAUDE.md 扫描、系统规则 |
| `l1/__init__.py` | ~5 | Exports |
| `l1/service.py` | ~60 | L1InsightIndexService(AppService): 技能元数据、路由表 |
| `l2/__init__.py` | ~5 | Exports |
| `l2/service.py` | ~100 | L2GlobalFactsService(AppService): 全局事实、BM25/LLM/Embedding 多模式检索 |
| `l3/__init__.py` | ~5 | Exports |
| `l3/service.py` | ~30 | L3TaskSkillsService(AppService): 弱包装 SkillService，无自己的 Protocol |
| `l4/__init__.py` | ~5 | Exports |
| `l4/service.py` | ~50 | L4SessionArchiveService(AppService): 会话档案、CacheLayer+FileProtocol |

### Planner — PlannerService (4 files, ~250 lines)

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | ~5 | Exports |
| `service.py` | ~40 | PlannerService(AppService): 配置开关 + TaskList 持久化 |
| `models.py` | ~60 | Task, TaskList（Pydantic） |
| `commands.py` | ~140 | RunAgentCommand, PlanCommand, ReflexionCommand, ReActStepCommand |

### Skills — SkillService (4 files, ~200 lines)

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | ~5 | Exports |
| `service.py` | ~100 | SkillService(AppService) 门面：技能管理、三级披露、结晶、CompositeProtocol |
| `skill.py` | ~50 | Skill(BaseModel) Pydantic 数据模型 |
| `commands.py` | ~50 | ExtractPatternCommand, CrystallizeSkillCommand |

### Subagent — SubagentService (4 files, ~180 lines)

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | ~5 | Exports |
| `service.py` | ~50 | SubagentService(AppService): depends LLMService, spawn/get_result 接口 |
| `agent_def.py` | ~100 | ExploreAgent, PlanAgent, GeneralAgent 定义 |
| `commands.py` | ~30 | SpawnSubagent, GetSubagentResult 命令 |

### MCP — MCPService (4 files, ~150 lines)

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | ~5 | Exports |
| `service.py` | ~50 | MCPService(AppService): 生命周期管理 + 工具注册，smart_import 动态加载 Protocol |
| `server.py` | ~60 | MCPServerProtocol(Protocol): adapter = MCP 客户端，on_start/on_stop 管理进程 |
| `wrapper.py` | ~40 | MCPToolWrapper(Tool): 持有 Protocol 引用，委托 execute 到 call_tool() |

### Context — ContextService (2 files, ~180 lines)

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | ~5 | Exports |
| `service.py` | ~180 | ContextService(AppService): 上下文组装、3 层压缩、token 预算（depends MemoryService + LLMService） |

### Agent — AgentService (4 files, ~250 lines)

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | ~5 | Exports |
| `service.py` | ~80 | AgentService(AppService): depends 全部 7 个子服务、系统 prompt 组装 |
| `command.py` | ~120 | AgentCommand(BaseCommand): async generator agent loop |
| `commands.py` | ~50 | Chat, ClearSession 命令 |

### Root — 包入口 (4 files, ~180 lines)

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | ~10 | Package init, exports |
| `config.py` | ~60 | Config loading, env var substitution |
| `cli.py` | ~50 | CLI REPL with click |
| `http.py` | ~60 | FastAPI HTTP + WebSocket endpoints |

## Total Estimated Lines

| Module | Files | Lines |
|--------|-------|-------|
| LLM | 10 | ~350 |
| Tools | 7 | ~600 |
| Env | 9 | ~150 |
| Memory | 12 | ~420 |
| Planner | 4 | ~250 |
| Skills | 3 | ~150 |
| Subagent | 4 | ~180 |
| MCP | 4 | ~150 |
| Context | 2 | ~180 |
| Agent | 4 | ~250 |
| Root | 4 | ~180 |
| **Total** | **~65** | **~3010** |

## Files to Create (Summary)

A2 侧 ~65 个新文件，分布在 `A2/` 项目根目录下的 10 个服务模块目录 + 根目录入口文件。bollydog 零修改。
