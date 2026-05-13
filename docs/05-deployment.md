# 05 — 部署与验证（Phase 5）

> 配置、入口、文件结构、测试策略、依赖。

---

## 一、配置

### 加载流程

```python
config = toml.load('agent.toml')
config = _deep_substitute(config)       # ${VAR} → 环境变量
hub = await load_from_config(config)    # bollydog: 拓扑排序 → smart_import → _build_protocol → on_start
```

### 完整 agent.toml

```toml
[hub]
domain = "agent"

# ── LLM ──
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

# ── 工具 + 环境 ──
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

# ── 记忆层 ──
[a2.memory.l0]
module = "a2.memory.service.MemoryLayerService"
load_on_start = true
[a2.memory.l0.protocol]
module = "bollydog.adapters.file.FileProtocol"
path = ".agent/rules/"
[a2.memory.l1]
module = "a2.memory.service.MemoryLayerService"
[a2.memory.l1.protocol]
module = "bollydog.adapters.kv.KVProtocol"
[a2.L2GlobalFactsService]
module = "a2.memory.l2.service.L2GlobalFactsService"
[a2.L2GlobalFactsService.protocol]
module = "bollydog.adapters.kv.KVProtocol"
[a2.memory.l4]
module = "a2.memory.service.MemoryLayerService"
[a2.memory.l4.protocol]
module = "bollydog.adapters.composite.CacheLayer"
flush_threshold = 100
[a2.memory.l4.protocol.protocol]
module = "bollydog.adapters.file.FileProtocol"
path = ".agent/sessions/"

# ── 技能 / 规划 / MCP ──
[a2.SkillService]
module = "a2.skills.service.SkillService"
skills_dir = ".user/skills/"
[a2.SkillService.protocol]
module = "bollydog.adapters.composite.CacheLayer"
[a2.SkillService.protocol.protocol]
module = "bollydog.adapters.file.LocalFileProtocol"
path = ".user/skills/"
[a2.PlannerService]
module = "a2.planner.service.PlannerService"
use_planning = false
use_reflexion = false
[a2.PlannerService.protocol]
module = "bollydog.adapters.file.LocalFileProtocol"
path = ".agent/tasks/"
[a2.MCPService]
module = "a2.mcp.service.MCPService"
[a2.MCPService.servers.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_TOKEN = "${GITHUB_TOKEN}" }

# ── 角色仓库 ──
[a2.AgentRegistryService]
module = "a2.registry.service.AgentRegistryService"
[a2.AgentRegistryService.protocol]
module = "bollydog.adapters.kv.KVProtocol"
[a2.AgentRegistryService.roles.default]
system_prompt = "You are a helpful AI assistant."
tools = ["*"]
max_turns = 50
[a2.AgentRegistryService.roles.explore]
system_prompt = "You are a fast code search agent. READ-ONLY."
tools = ["read", "glob", "grep"]
model = "deepseek/deepseek-chat"
[a2.AgentRegistryService.roles.orchestrator]
system_prompt = "你是任务协调者..."
tools = ["spawn_agent", "ask_user"]
can_spawn = true

# ── 上下文 + 顶层 ──
[a2.ContextService]
module = "a2.context.service.ContextService"
token_budget = 128000
compact_threshold = 13000
[a2.AgentService]
module = "a2.agent.service.AgentService"
commands = ["a2.agent.commands"]
[a2.AgentService.router_mapping]
Chat = ["SSE", "/api/chat"]
ClearSession = ["POST", "/api/clear"]
```

---

## 二、CLI + HTTP

### CLI（python-fire）

```python
import fire
from bollydog.service import load_from_config

class CLI:
    @staticmethod
    def service(config='agent.toml'):
        load_from_config(config)

    @staticmethod
    def chat(message: str, config='agent.toml', role='default'):
        load_from_config(config)
        # hub.execute(AgentCommand(goal=message))

def main(): fire.Fire(CLI)
```

### HTTP（Starlette SSE）

通过 `router_mapping` 自动映射，bollydog `SseHandler` 检测 async generator → SSE 流式输出。

---

## 三、文件结构

```
A2/
├── pyproject.toml
├── agent.toml
├── cli.py                   # fire CLI
├── a2/
│   ├── __init__.py
│   ├── config.py            # 配置加载
│   ├── agent/               # 顶层编排
│   │   ├── service.py       #   AgentService
│   │   ├── command.py       #   AgentCommand (async generator)
│   │   └── commands.py      #   Chat, ClearSession
│   ├── llm/                 # LLM
│   │   ├── service.py       #   LLMService
│   │   └── provider.py      #   LiteLLMProvider
│   ├── tools/               # 工具系统
│   │   ├── service.py       #   ToolService
│   │   ├── tool.py          #   Tool 基类
│   │   ├── protocol.py      #   TerminalProtocol + Local/Docker/SSH
│   │   ├── builtin.py       #   11 内置工具
│   │   └── permission.py    #   PermissionConfig
│   ├── env/                 # 环境
│   │   └── service.py       #   EnvService 基类
│   ├── memory/              # 记忆
│   │   ├── service.py       #   MemoryLayerService 基类
│   │   ├── commands.py      #   NotifyIndexUpdateCommand
│   │   └── l2/
│   │       └── service.py   #   L2GlobalFactsService
│   ├── skills/              # 技能
│   │   ├── service.py       #   SkillService
│   │   ├── skill.py         #   Skill 模型
│   │   └── commands.py      #   Extract/Crystallize
│   ├── context/             # 上下文
│   │   └── service.py       #   ContextService
│   ├── planner/             # 规划
│   │   ├── service.py       #   PlannerService
│   │   ├── models.py        #   Task/TaskList
│   │   └── commands.py      #   Plan/ReAct/Reflexion
│   ├── registry/            # 角色仓库
│   │   ├── service.py       #   AgentRegistryService
│   │   ├── role_def.py      #   RoleDef
│   │   └── commands.py      #   SpawnAgent
│   └── mcp/                 # MCP
│       ├── service.py       #   MCPService
│       ├── server.py        #   MCPServerProtocol
│       └── wrapper.py       #   MCPToolWrapper
├── tests/
│   ├── conftest.py
│   ├── test_llm.py          ├── test_tools.py
│   ├── test_memory.py       ├── test_skills.py
│   ├── test_context.py      ├── test_planner.py
│   ├── test_registry.py     ├── test_mcp.py
│   └── test_agent.py
└── docs/                     # 本文档集
```

**~40 文件，~2200 行**

---

## 四、验证策略

### 单元测试

| 模块 | 重点 |
|------|------|
| LLMService | capability 映射、Router fallback |
| ToolService | 注册、权限、cast/validate |
| MemoryLayerService | get/set/get_all |
| L2GlobalFactsService | BM25 search、budget |
| SkillService | from_markdown 往返、三级披露 |
| ContextService | build_messages 三段式、压缩 |
| PlannerService | TaskList CRUD |
| AgentRegistryService | RoleDef TOML 加载 |

### 集成测试

| 场景 | 验证 |
|------|------|
| Agent Loop | dispatch → 流式输出 |
| LLM fallback | 主模型 429 → 备选 |
| MCP 发现 | on_start → ToolService 注册 |
| 多角色 spawn | SpawnAgent → sub-AgentCommand |
| 上下文压缩 | 超标 → MicroCompact → LLM 摘要 |
| 权限拦截 | block_list → 拒绝 |

### 对抗性测试

| # | 场景 | 预期 |
|---|------|------|
| A1 | 无限工具调用 | max_steps=10 |
| A2 | prompt 注入 | L0 不含用户输入 |
| A3 | 结果溢出 | 截断 30K |
| A4 | 幻觉工具名 | "Unknown tool" |
| A5 | 递归 spawn | depth ≤ 2 |
| A6 | 路径逃逸 | 权限 + 沙箱 |
| A7 | MCP 挂掉 | 隔离，不影响内置 |
| A8 | token 超标 | 三层压缩 |

### 分阶段清单

- Phase 1: 各叶子服务独立可用
- Phase 2: Memory/MCP 依赖正确
- Phase 3: ContextService 完整组装
- Phase 4: Agent Loop 端到端
- Phase 5: CLI/HTTP + 对抗测试

---

## 五、Dependencies

```toml
[project]
dependencies = ["bollydog>=0.1.0"]

[project.optional-dependencies]
llm = ["litellm>=1.0.0"]
mcp = ["mcp>=1.0.0"]
env = ["aiodocker>=0.21.0", "asyncssh>=2.14.0"]
search = ["bm25s>=0.2.0"]
all = ["litellm>=1.0.0", "mcp>=1.0.0", "aiodocker>=0.21.0", "asyncssh>=2.14.0", "bm25s>=0.2.0"]
```

bollydog **零修改**。所有 A2 概念在 A2 仓库内实现，未来验证稳定后可考虑上浮 TerminalProtocol。
