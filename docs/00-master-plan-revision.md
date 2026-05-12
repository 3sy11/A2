# A2 Agent Framework — 设计修订文档

> 本文档基于对 `00-master-plan.md` 的系统性审查，整合所有讨论结论。
> 每条修订都标注来源问题和修改理由。
> **标记说明：** ✅ 已有明确结论 | ⚠️ 存在疑问待决 | ❌ 原设计需废弃 | 🆕 新增设计

---

## 一、核心设计变更（影响整体架构）

### 1.1 ❌ 废弃：多 LLMService 分层

**原设计：**
```
LLMService（门面）
├── FastLLMService   → ModelFallbackProvider → OpenAIProvider
├── CapableLLMService → ModelFallbackProvider → AnthropicProvider
└── CodeLLMService   → ModelFallbackProvider → OpenAIProvider
```

**废弃原因：**
- Fast/Capable/Code 三种 LLMService 的分类维度不一致（能力 vs 任务类型）
- 模型路由是运行时配置问题，不是编译时架构问题
- CodeLLMService 假设"框架知道什么任务用什么模型"，与通用 Agent 定位矛盾
- ModelFallbackProvider 的功能 LiteLLM Router 已完全内置，不需要自实现

**✅ 替换方案：单一 LLMService + LiteLLM Router**
```
LLMService（单一）
└── LLMProvider(Protocol)
    └── adapter = LiteLLM Router 实例
```

TOML 配置：
```toml
[a2.LLMService]
protocol.module = "a2.llm.LiteLLMProvider"
protocol.model = "anthropic/claude-sonnet-4-20250514"
protocol.fallback_models = ["openai/gpt-4o"]
```

- `ModelFallbackProvider` 整个文件删除，由 LiteLLM Router 内置 fallback 替代
- `FastLLMService` / `CapableLLMService` / `CodeLLMService` 三个 Service 文件删除
- Tool 的 `provider` / `capability` 两层解析机制保留，但路由目标简化为 LiteLLM 的 model 字符串

**⚠️ 待决：Tool 的 `capability` 字段原来路由到 Fast/Capable 两个 Service，废弃后路由到什么？**
- 选项 A：`capability` 改为直接指定 model 字符串（最简单，但丧失抽象）
- 选项 B：LLMService 内部维护 `capability → model` 映射表，在 TOML 里配置
- 选项 C：直接废弃 `capability` 字段，统一用默认模型（最激进）

---

### 1.2 ❌ 废弃：三个独立 EnvService

**原设计：**
```
ToolService（门面）
├── LocalToolService(AppService) → LocalProtocol
├── DockerToolService(AppService) → DockerProtocol
└── SSHToolService(AppService) → SSHProtocol
```

**废弃原因：**
- 三个 Service 的业务逻辑几乎为零，差异仅在 Protocol 配置
- 执行环境的切换是运行时配置，不是编译时架构
- bollydog 动态子类机制可以让 TOML 完成这个工作

**✅ 替换方案：单一 EnvService 基类 + TOML 配置三个实例**
```toml
[a2.env.local]
module = "a2.env.EnvService"
protocol.module = "a2.tools.LocalProtocol"

[a2.env.docker]
module = "a2.env.EnvService"
protocol.module = "a2.tools.DockerProtocol"
protocol.container_image = "python:3.12-slim"

[a2.env.ssh]
module = "a2.env.EnvService"
protocol.module = "a2.tools.SSHProtocol"
protocol.host = "remote.server.com"
```

- `env/local/service.py` / `env/docker/service.py` / `env/ssh/service.py` 三个文件删除
- 新增单一 `env/service.py` — `EnvService(AppService)` 基类
- Protocol 实现文件不变（`LocalProtocol` / `DockerProtocol` / `SSHProtocol` 保留）

**开源替代（Protocol 实现层）：**
- `LocalProtocol.execute` → `asyncio.create_subprocess_exec`（标准库，零依赖）
- `DockerProtocol.execute` → `aiodocker`（异步 Docker HTTP API）
- `SSHProtocol.execute` → `asyncssh`（纯 Python 异步 SSH）

---

### 1.3 ❌ 废弃：MemoryService 门面 + L0/L1/L4 独立 Service

**原设计：**
```
MemoryService（门面）
├── L0MetaRulesService(AppService) → KVProtocol
├── L1InsightIndexService(AppService) → KVProtocol
├── L2GlobalFactsService(AppService) → KVProtocol（有检索逻辑，保留）
├── L3TaskSkillsService(AppService) → SkillService 弱包装
└── L4SessionArchiveService(AppService) → CacheLayer → FileProtocol
```

**废弃原因：**
- L0/L1/L4 的业务逻辑几乎为零，差异仅在 Protocol 配置
- MemoryService 门面是纯透传层，没有任何逻辑
- L3TaskSkillsService 是 SkillService 的弱包装，原文档自己承认（见 3.3 节）
- bollydog 动态子类机制可以让 TOML 完成这个工作

**✅ 替换方案：MemoryLayerService 基类 + TOML 配置**
```toml
[a2.memory.l0]
module = "a2.memory.MemoryLayerService"
load_on_start = true
protocol.module = "bollydog.adapters.file.FileProtocol"
protocol.path = ".agent/rules/"

[a2.memory.l1]
module = "a2.memory.MemoryLayerService"
protocol.module = "bollydog.adapters.kv.KVProtocol"

[a2.memory.l4]
module = "a2.memory.MemoryLayerService"
protocol.module = "bollydog.adapters.composite.CacheLayer"
protocol.protocol.module = "bollydog.adapters.file.FileProtocol"
protocol.protocol.path = ".agent/sessions/"
```

- `memory/service.py`（MemoryService 门面）删除
- `memory/l0/service.py` / `memory/l1/service.py` / `memory/l4/service.py` 删除
- `memory/l3/service.py`（L3TaskSkillsService）删除，ContextService 直接 depends SkillService
- 新增单一 `memory/service.py` — `MemoryLayerService(AppService)` 基类
- `L2GlobalFactsService` 保留独立文件（有 BM25 检索逻辑）

**ContextService depends 调整：**
```
原：ContextService → MemoryService → [L0, L1, L2, L3, L4]
改：ContextService → [a2.memory.l0, a2.memory.l1, L2GlobalFactsService, SkillService, a2.memory.l4]
```

**开源替代（L2 检索层）：**
- BM25 检索：`bm25s`（纯 Python + Numpy，比 rank_bm25 快数个量级，内置 save/load）
- Token 计数：`litellm.token_counter(model, messages)`（跨模型统一接口，替代自估算）

---

### 1.4 ❌ 废弃：SubagentService

**原设计：**
```
SubagentService(AppService)
├── depends: LLMService
├── depends: ToolService
└── agent_def.py — ExploreAgent / PlanAgent / GeneralAgent
```

**废弃原因：**
- SubagentService 的唯一作用是维护三个 AgentDefinition 配置对象
- 这是配置管理，不是服务逻辑
- 引入了新的概念（AgentDefinition）和 RoleDef 概念重叠

**✅ 替换方案：AgentRegistryService + RoleDef（见第二节）**

---

## 二、🆕 新增设计：多角色 Agent 系统

这是本次讨论中发现的原设计中**完全缺失**的一个概念层。

### 2.1 核心问题

原设计把 Agent 当作单一角色的执行机器。但实际上框架需要支持：
- 同一实例扮演不同角色（代码审查员 vs 编程导师）
- 一个 Orchestrator Agent 动态拉起多个 Worker Agent
- 不同角色有各自独立的记忆命名空间、工具权限、提示词

**原设计缺少的概念：**
1. `RoleDef`：角色的完整定义（提示词 + 工具 + 模型 + 记忆配置）
2. `AgentRegistry`：角色仓库，按名字取用
3. 角色记忆隔离：每个角色的 L0-L4 数据按 `role_name` 命名空间隔离

### 2.2 ✅ RoleDef 数据模型

```python
@dataclass
class RoleDef:
    name: str                        # "sales_agent"
    system_prompt: str               # 角色身份描述，从数据库取
    tools: list[str]                 # 允许使用的工具名列表
    model: str | None = None         # 覆盖默认模型（可选）
    max_turns: int = 50
    can_spawn: bool = False          # 是否能再 spawn 子 Agent（防递归）
    skills_dir: str | None = None    # 角色专属技能目录
    memory_namespace: str | None = None  # 记忆隔离命名空间，默认等于 name
    use_planning: bool = False
    use_reflexion: bool = False
```

**关键设计：原来的 SubagentDef（ExploreAgent/PlanAgent/GeneralAgent）统一变成 RoleDef，不需要两套系统。**

### 2.3 ✅ AgentRegistryService

```
AgentRegistryService(AppService)
└── protocol = KVProtocol（或 FileProtocol）
    存储：{role_name → RoleDef}
    接口：get(name) / list() / register(role_def)
```

TOML 配置角色：
```toml
[a2.registry]
protocol.module = "bollydog.adapters.kv.KVProtocol"

[a2.roles.orchestrator]
system_prompt = "你是任务协调者，负责分析用户意图并调度专业 Agent 完成任务..."
tools = ["spawn_agent", "ask_user"]
can_spawn = true
max_turns = 20

[a2.roles.sales_agent]
system_prompt = "你是销售专员，专注于定价、优惠和客户关系..."
tools = ["crm_query", "pricing_api"]
model = "openai/gpt-4o-mini"
memory_namespace = "sales"

[a2.roles.code_reviewer]
system_prompt = "你是代码审查员，专注于发现安全漏洞和性能问题..."
tools = ["read_file", "bash"]
skills_dir = ".agent/skills/code_review/"
```

### 2.4 ✅ 角色记忆隔离

每个角色的所有记忆按 `memory_namespace` 隔离：

```
.agent/
├── roles/
│   ├── orchestrator/
│   │   ├── rules/        ← L0
│   │   ├── skills/       ← L1/L3
│   │   ├── facts.db      ← L2
│   │   └── sessions/     ← L4
│   ├── sales_agent/
│   │   └── ...
│   └── code_reviewer/
│       └── ...
```

`AgentCommand` 启动时从 `RoleDef.memory_namespace` 解析各层 Protocol 的路径，不同角色的记忆完全不共享。

### 2.5 ✅ SpawnAgentCommand 统一主/子 Agent

原来的 `SpawnSubagentCommand` 升级为通用的 `SpawnAgentCommand`：

```python
class SpawnAgentCommand(BaseCommand):
    role: str          # role_name，从 AgentRegistry 取 RoleDef
    input: dict
    max_turns: int | None = None

    async def __call__(self) -> AgentResult:
        registry = app.services["a2.registry"]
        role_def = await registry.get(self.role)
        result = yield AgentCommand(
            role_def=role_def,
            input=self.input,
            session_namespace=f"{self.role}/{message.trace_id}",
        )
        return AgentResult(role=self.role, ...)
```

**防递归升级：** 原设计只靠"工具列表不含 subagent 工具"防递归，不充分。
新方案：`RoleDef.can_spawn = False`（默认）+ `agent_depth` 计数器（通过 SessionContext 传递），硬限制最大深度为 2。

---

## 三、提示词系统重新设计

原设计把 L0 当作"系统规则文件"，没有明确区分"框架固定规则"和"角色身份描述"，也没有明确 System Prompt 的组装方式。

### 3.1 ❌ 原 L0 设计问题

原设计：L0 来源于 CLAUDE.md / AGENTS.md，启动时扫描文件。

问题：
- 没有 CLAUDE.md 时 L0 为空，Agent 没有任何行为约束
- 框架固定规则和用户自定义规则混在一起
- 没有"角色身份"的位置

### 3.2 ✅ System Prompt 三段式结构

参考 Claude Code 的 System Reminder 模式，messages[] 组装方式改为：

```
messages[]:
  [system 0]  静态核心（永远存在，框架固定）
              = DEFAULT_SYSTEM_PROMPT（~200 tok，框架内置常量）
              + RoleDef.system_prompt（~300 tok，角色身份）
              + L0 环境上下文（working_dir, date, platform）
              + L1 技能元数据（~100 tok/技能）
              + L2 检索结果（~2K tok，按需）

  [system N]  System Reminder（动态注入，按需）
              = L3 技能正文（触发时插入，不污染静态 prompt）

  [user/assistant ...]  对话历史（L4）

tools[]       工具描述（按 RoleDef.tools 过滤）
```

**关键变化：**
- L3 技能触发 **不修改 static system prompt**，而是在 messages[] 中插入 `role: "system"` 的 System Reminder
- 框架内置 `DEFAULT_SYSTEM_PROMPT` 常量（工具使用规则、安全边界、输出格式），CLAUDE.md 内容追加在后面，不是替换

### 3.3 ⚠️ 待决：DEFAULT_SYSTEM_PROMPT 的内容边界

需要明确哪些内容属于框架固定、哪些属于角色自定义：

| 内容 | 归属 | 来源 |
|------|------|------|
| 工具使用政策 | 框架固定 | `DEFAULT_SYSTEM_PROMPT` 常量 |
| 安全边界 | 框架固定 | `DEFAULT_SYSTEM_PROMPT` 常量 |
| 输出格式要求 | 框架固定 | `DEFAULT_SYSTEM_PROMPT` 常量 |
| 当前日期/工作目录 | 框架固定（动态值） | 启动时注入 |
| 角色身份描述 | 角色自定义 | `RoleDef.system_prompt` |
| 项目特定规则 | 用户自定义 | `CLAUDE.md` |
| 技能元数据 | 框架动态 | `L1 InsightIndex` |

**⚠️ 疑问：`DEFAULT_SYSTEM_PROMPT` 是否需要支持覆盖？** 比如某个角色需要完全不同的行为规则。建议：允许 `RoleDef` 设置 `override_default_prompt = true` 跳过框架默认值，但默认不跳过。

---

## 四、多 Agent 编排模式

原设计只有单一的 SubagentTool，没有系统性地描述多 Agent 之间的组合模式。

### 4.1 ✅ 三种任务关系模式

**模式 A：Pipeline（流水线）**
- 场景：有严格依赖的审批流、工作流
- 实现：顺序 `yield SpawnAgentCommand()`，前序结果传给后续
- 特征：任意步失败则终止；前序 output 是后序 input

```python
# 串行，依赖前序结果
risk_r = yield SpawnAgentCommand(role="risk_agent", input=order)
if not risk_r.success: return
inventory_r = yield SpawnAgentCommand(role="inventory_agent",
    input={**order, "is_vip": risk_r.data["is_vip"]})  # 依赖上一步
```

**模式 B：Parallel Fan-out（并行扇出）**
- 场景：独立查询后汇总（询价、多源信息聚合）
- 实现：`asyncio.gather` + 多个 `SpawnAgentCommand`
- 特征：耗时 = 最慢的单个 Worker；单个失败可降级

```python
# 并行，互不等待
sales_r, inventory_r, finance_r = await asyncio.gather(
    hub.execute(SpawnAgentCommand(role="sales_agent", input=ctx)),
    hub.execute(SpawnAgentCommand(role="inventory_agent", input=ctx)),
    hub.execute(SpawnAgentCommand(role="finance_agent", input=ctx)),
)
# 全部完成后聚合
reply = yield SpawnAgentCommand(role="reply_agent",
    input={"sales": sales_r.data, "inventory": inventory_r.data, ...})
```

**模式 C：Reactive Network（响应网络）**
- 场景：自动化监控、告警、持续运行的系统
- 实现：`exchange.publish` / `exchange.subscribe`，Service.on_started 注册
- 特征：事件驱动，Agent 自治，新增逻辑只需加 subscribe

```python
# 发布方（不等待响应）
await exchange.publish("inventory.low", {"sku": sku, "qty": qty})

# 订阅方（Service.on_started 注册）
exchange.subscribe("inventory.low", self._on_inventory_low)
```

### 4.2 ✅ 建议实现顺序

**v1 实现 A + B**（覆盖绝大多数业务场景，bollydog 原生支持）
**v2 引入 C**（bollydog Exchange 已支持，但增加系统复杂度和调试难度）

### 4.3 ⚠️ 待决：Orchestrator 的决策机制

Orchestrator Agent 如何决定召唤哪些 Worker？三个选项：

| 选项 | 方式 | 优点 | 缺点 |
|------|------|------|------|
| A | system_prompt 写死路由规则 | 可预测、可测试 | 不灵活 |
| B | LLM 动态决策（推荐） | 灵活、通用 | 额外 LLM 调用，决策不确定 |
| C | 意图分类器（小模型） | 成本低、可预测 | 需要维护分类规则 |

**建议选 B**（Orchestrator 的 system_prompt 里列出所有可用 Worker 的名字和职责描述，LLM 自己决定调用哪些），但这需要 AgentRegistry 能向 Orchestrator 提供所有可用角色的描述列表。

---

## 五、MCP 集成优化

### 5.1 ✅ MCPServerProtocol 传输层替换为官方 SDK

**原设计：** 自实现 stdio/SSE 客户端协议层。

**替换方案：** 官方 `mcp` Python SDK，`ClientSession` 作为 `MCPServerProtocol.adapter`。

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPServerProtocol(Protocol):
    async def on_start(self):
        server_params = StdioServerParameters(command=self.command, args=self.args)
        read, write = await stdio_client(server_params).__aenter__()
        self.adapter = ClientSession(read, write)
        await self.adapter.initialize()

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self.adapter.call_tool(name, arguments)
        return result.content[0].text
```

- `mcp/server.py` 的协议解析层全部删除，替换为 SDK 调用
- bollydog Protocol 适配层（`on_start`/`on_stop`/`call_tool`）保留，约 50 行

---

## 六、上下文管理补充设计

### 6.1 ⚠️ MicroCompact 截断逻辑需要明确

原设计只说"截断旧 tool 输出"，没有定义"旧"的判断标准，实现时容易截断仍在引用的内容。

**建议明确：按两个维度决定截断**
- 消息距离：距当前轮次超过 N 轮（建议 N=10，可配置）
- 消息类型优先级：`tool_result` > `tool_use` > `assistant` > `user`（越靠前越优先截断）
- 保护规则：最近 2 轮的所有消息不截断，无论类型

**⚠️ 待决：N 的具体值需要在实测后确定，建议在 `context/service.py` 里作为可配置参数。**

### 6.2 ✅ Token 计数统一方案

原设计没有明确 token 计数实现，自估算误差可达 20-30%。

**替换方案：** `litellm.token_counter(model, messages)` 统一接口，内部对不同模型使用正确的 tokenizer，跨模型通用。

```python
from litellm import token_counter
current_tokens = token_counter(model=self.model, messages=self.messages)
```

---

## 七、权限模型补充设计

**原设计问题：** 文档在多处提到"权限检查"，但从未定义权限模型是什么（粒度、存储位置、修改权限）。

### 7.1 ✅ 建议采用三层权限模型

参考 Claude Code 安全模型：

```python
@dataclass
class PermissionConfig:
    allow_list: list[str]     # 明确允许的工具/路径前缀
    block_list: list[str]     # 明确禁止的工具/路径前缀
    require_approval: list[str]  # 需要用户确认的操作
```

- 配置存储在 TOML 里，按 `RoleDef` 绑定（不同角色有不同权限）
- 每次工具调用前过一遍三层检查
- `block_list` 优先于 `allow_list`

```toml
[a2.roles.sales_agent.permissions]
allow_list = ["crm_query", "pricing_api"]
block_list = ["bash", "write_file"]
require_approval = ["send_email"]
```

**⚠️ 待决：权限检查放在 ToolService 还是 AgentCommand？**
- 放 ToolService：统一入口，但 ToolService 需要知道当前是哪个 Role
- 放 AgentCommand：靠近业务逻辑，但工具层没有保护

---

## 八、PlannerService 依赖关系修正

**原设计问题（P10）：** PlannerService 列为"零依赖叶子服务"，但 PlanCommand / ReflexionCommand 在运行时需要调用 LLMService，通过 `AppService._apps['a2.LLMService']` 访问。这是**隐式运行时依赖**，在 depends 里不声明，导致独立实例化时运行时 KeyError。

**✅ 修复方案（选一）：**
- 选项 A（推荐）：`PlannerService` 显式 `depends: ["a2.LLMService"]`，诚实反映依赖关系
- 选项 B：Phase 1 验收标准里注明"PlanCommand 需要 LLMService 启动后才可测试"

---

## 九、简化后的服务清单对比

### 原设计（~20 个 Service 类/文件）

```
LLMService + FastLLMService + CapableLLMService + CodeLLMService
ToolService + LocalToolService + DockerToolService + SSHToolService
MemoryService + L0Service + L1Service + L2Service + L3Service + L4Service
SkillService + ContextService + PlannerService + SubagentService + MCPService + AgentService
```

### 修订后（8 个 Service 类 + TOML 配置实例）

| Service 类 | 说明 | 实例数 |
|-----------|------|--------|
| `LLMService` | 单一，LiteLLM Router | 1（TOML 配置模型） |
| `ToolService` | 不变，工具门面 | 1 |
| `EnvService` | 基类，TOML 配三个执行环境 | 3 个 TOML 实例 |
| `MemoryLayerService` | 基类，TOML 配 L0/L1/L4 | 3 个 TOML 实例 |
| `L2GlobalFactsService` | 独立，有 BM25 检索逻辑 | 1 |
| `SkillService` | 不变，有结晶逻辑 | 1 |
| `ContextService` | 调整 depends，直接依赖各层 | 1 |
| `AgentRegistryService` | **🆕 新增**，存储所有 RoleDef | 1 |
| `MCPService` | 不变 | 1 |
| `PlannerService` | 不变，修正 depends | 1 |
| `AgentService` | 调整 depends | 1 |

**删除的文件（相比原计划）：**
- `llm/fast/service.py` / `llm/capable/service.py` / `llm/code/service.py`
- `llm/fallback.py`（ModelFallbackProvider）
- `env/local/service.py` / `env/docker/service.py` / `env/ssh/service.py`
- `memory/service.py`（MemoryService 门面）
- `memory/l0/service.py` / `memory/l1/service.py` / `memory/l4/service.py`
- `memory/l3/service.py`（L3TaskSkillsService）
- `subagent/service.py` / `subagent/agent_def.py`

**新增的文件：**
- `registry/service.py`（AgentRegistryService）
- `registry/role_def.py`（RoleDef Pydantic 模型）
- `registry/commands.py`（RegisterRole、ListRoles）
- `agent/spawn.py`（SpawnAgentCommand，替代 SpawnSubagentCommand）

---

## 十、开源替代组件汇总

| 模块 | 原方案 | 替换组件 | 节省 |
|------|--------|---------|------|
| LLM Provider + Fallback | 自实现 OpenAI/Anthropic adapter + ModelFallbackProvider | **LiteLLM Router** | ~400 行，future provider 扩展免费 |
| MCP 传输层 | 自实现 stdio/SSE 客户端 | **官方 mcp Python SDK** | 协议层全部删除 |
| L2 BM25 检索 | rank_bm25 或自实现 | **bm25s**（纯 Python+Numpy，内置持久化） | 性能提升 + 零实现 |
| Token 计数 | 自估算（误差 20-30%） | **litellm.token_counter()** | 1 行代码，跨模型精确 |
| Docker 执行环境 | 自实现 Docker API | **aiodocker**（异步 Docker HTTP） | ~100 行 |
| SSH 执行环境 | 自实现 SSH 客户端 | **asyncssh**（纯 Python 异步 SSH） | ~150 行 |

---

## 十一、待决问题汇总

以下问题在讨论中尚未得出明确结论，需要在执行前决策：

| # | 问题 | 影响范围 | 选项 |
|---|------|---------|------|
| Q1 | Tool 的 `capability` 字段废弃 LLMService 分层后如何路由 | LLMService + 所有 Tool | A: 改为 model 字符串 / B: TOML 维护 capability→model 映射 / C: 废弃 capability |
| Q2 | `DEFAULT_SYSTEM_PROMPT` 是否支持 RoleDef 级别的覆盖 | ContextService + RoleDef | A: 支持 override / B: 不支持，强制框架默认 |
| Q3 | Orchestrator 的 Worker 决策机制 | AgentRegistryService + Orchestrator RoleDef | A: 写死路由 / B: LLM 动态决策 / C: 意图分类器 |
| Q4 | 权限检查放在 ToolService 还是 AgentCommand | ToolService + AgentCommand | A: ToolService（统一入口）/ B: AgentCommand（靠近业务） |
| Q5 | PlannerService 依赖修正选哪个方案 | Phase 1 验收 | A: 显式 depends LLMService / B: 注释说明 |
| Q6 | MicroCompact 的消息距离阈值 N | ContextService | 建议 N=10，实测后调整 |
| Q7 | 模式 C（Reactive Network）是否纳入 v1 | AgentService + Exchange | A: v1 不做 / B: v1 做基础骨架 |
| Q8 | 角色的 system_prompt 是否支持模板变量 | AgentRegistryService | 比如 `{current_date}` `{working_dir}` 动态插值 |

---

## 十二、修订后的分阶段执行计划

### Phase 1：叶子服务（零依赖）

1. **LLMService**（简化版）
   - `llm/service.py` — LLMService(AppService)，单一实例
   - `llm/provider.py` — LLMProvider(Protocol)，adapter = LiteLLM Router
   - 删除：fast/capable/code 子目录，fallback.py

2. **ToolService + EnvService**
   - `tools/service.py` — ToolService(AppService) 不变
   - `tools/tool.py` / `tools/protocol.py` / `tools/builtin.py` 不变
   - `env/service.py` — EnvService(AppService) 基类（新）
   - `tools/protocol.py` — LocalProtocol + DockerProtocol(aiodocker) + SSHProtocol(asyncssh)
   - 删除：env/local/ env/docker/ env/ssh/ 三个子目录

3. **SkillService** — 不变

4. **PlannerService** — 修正 depends（Q5 决策后执行）

5. **AgentRegistryService**（新）
   - `registry/service.py` — AgentRegistryService(AppService)
   - `registry/role_def.py` — RoleDef(BaseModel)
   - `registry/commands.py` — RegisterRole、ListRoles

### Phase 2：单层依赖服务

6. **MemoryLayerService 基类 + L0/L1/L4 TOML 实例**
   - `memory/service.py` — MemoryLayerService(AppService) 基类（新）
   - 删除：l0/ l1/ l3/ l4/ 子目录

7. **L2GlobalFactsService** — 保留，替换 BM25 实现为 bm25s

8. **MCPService** — 替换传输层为官方 mcp SDK

### Phase 3：多层依赖服务

9. **ContextService**（调整 depends）
   - 直接 depends：[a2.memory.l0, a2.memory.l1, L2GlobalFactsService, SkillService, a2.memory.l4]
   - 删除对 MemoryService 门面的依赖
   - 替换 token 计数为 litellm.token_counter()
   - 明确 MicroCompact 截断逻辑（Q6 决策后执行）

### Phase 4：顶层编排

10. **AgentService**（调整 depends + 新增多角色支持）
    - depends 新增 AgentRegistryService
    - `agent/spawn.py` — SpawnAgentCommand（替代 SpawnSubagentCommand）
    - agent_depth 计数器（防递归升级）
    - 权限检查集成（Q4 决策后执行）

### Phase 5：入口与集成

11. 配置/CLI/HTTP — 不变，补充 RoleDef TOML 示例配置
