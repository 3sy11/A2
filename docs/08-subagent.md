# 08 — Subagent System

## Context

子智能体实现上下文隔离——将探索性或专门的工作委派给子 agent，子 agent 在自己的上下文中运行，只返回摘要给父 agent。这防止了父 agent 的上下文被中间工具输出污染。核心权衡：额外的 LLM 调用换取更干净的父上下文。

## 架构

```
SubagentService(AppService)
├── depends: ["a2.LLMService", "a2.ToolService"]  ← LLM 路由 + 子智能体执行工具
│
├── spawn(task, agent_type, capability)     ← 创建子智能体
├── get_result(subagent_id) → summary       ← 获取结果
│
└── agents:
    ├── ExploreAgent    — 只读，Haiku 模型，快速搜索
    ├── PlanAgent       — 只读，继承父模型，架构规划
    └── GeneralAgent    — 全工具，默认模型，多步任务

上下文隔离：
    父上下文: [system, user, assistant, tool, assistant, ...]
    子上下文: [system, delegation_prompt] → [全新循环] → summary
    只有 summary 流回父 agent。
```

## provider / capability 参数

子智能体支持 `provider` 和 `capability` 两个参数，解析优先级：provider > capability > agent_def 默认值。

```python
# 直接指定 provider（最精确）：
await subagent_service.spawn(
    task="Review this code",
    agent_type='general',
    provider='claude-opus',    # ← 直接用 claude-opus provider
)

# 通过 capability 路由（较模糊）：
await subagent_service.spawn(
    task="Review this code",
    agent_type='general',
    capability='capable',      # ← 路由到 CapableLLMService (Claude Opus)
)

# 普通子智能体，不指定 provider/capability：
await subagent_service.spawn(
    task="Search for auth patterns",
    agent_type='explore',       # ← ExploreAgent 内部指定 capability='fast'
)
```

子智能体通过 `LLMService` 获取对应 LLM：

```
SubagentService.spawn(task, provider='claude-opus')
    → SubagentService._llm.chat(messages, provider='claude-opus')
        → LLMService.resolve(provider='claude-opus')
            → _providers['claude-opus'] → AnthropicProvider.chat()

SubagentService.spawn(task, capability='capable')
    → SubagentService._llm.chat(messages, capability='capable')
        → LLMService.resolve(capability='capable')
            → CapableLLMService.chat() → AnthropicProvider.chat()
```

## Class Signatures

### SubagentService (AppService)

```python
class SubagentService(AppService):
    """管理子智能体生命周期：创建、监控、收集结果。"""
    domain = 'a2'
    alias = 'SubagentService'
    depends = ['a2.LLMService', 'a2.ToolService']

    _agents: dict[str, 'AgentDefinition']
    _running: dict[str, asyncio.Task]
    _results: dict[str, str]

    async def on_start(self):
        self._agents = {
            'explore': ExploreAgent(),
            'plan': PlanAgent(),
            'general': GeneralAgent(),
        }
        self._running = {}
        self._results = {}

    async def spawn(self, task: str, agent_type: str = 'general',
                    provider: str = None, capability: str = None,
                    tools: list[str] = None) -> str:
        """创建子智能体。返回 subagent_id。

        优先级：provider > capability > agent_def 默认 capability。
        """
        agent_def = self._agents.get(agent_type, self._agents['general'])
        # capability 优先级：显式参数 > agent_def 默认值
        cap = capability or agent_def.capability
        subagent_id = str(uuid.uuid4())[:8]

        sub_messages = [{'role': 'user', 'content': task}]
        bg_task = asyncio.create_task(
            self._run_subagent(subagent_id, agent_def, sub_messages, provider, cap, tools)
        )
        self._running[subagent_id] = bg_task

        def _cleanup(_):
            self._running.pop(subagent_id, None)
        bg_task.add_done_callback(_cleanup)

        return subagent_id

    async def get_result(self, subagent_id: str, wait: bool = True) -> str:
        """获取子智能体结果。wait=True 时阻塞等待。"""
        if subagent_id in self._results:
            return self._results[subagent_id]
        if wait and subagent_id in self._running:
            await self._running[subagent_id]
            return self._results.get(subagent_id, '(no result)')
        return f'Error: Subagent {subagent_id} not found'

    async def _run_subagent(self, task_id: str, agent_def: 'AgentDefinition',
                            messages: list, provider: str = None,
                            capability: str = None, tools: list = None):
        """在隔离上下文中运行子智能体循环。"""
        system_prompt = agent_def.get_system_prompt()
        sub_messages = [{'role': 'system', 'content': system_prompt}] + messages
        sub_tools = agent_def.get_tool_schemas(tools)

        for turn in range(agent_def.max_turns):
            # 使用指定 provider 或 capability 的 LLM
            response = await self._llm.chat(
                messages=sub_messages,
                tools=sub_tools,
                model=agent_def.model,
                provider=provider,           # ← 优先：直接指定 provider
                capability=capability,       # ← 其次：路由到对应的 LLM 能力服务
            )

            assistant_msg = response.choices[0].message
            sub_messages.append(assistant_msg.model_dump())

            if not assistant_msg.tool_calls:
                summary = assistant_msg.content or '(no summary)'
                self._results[task_id] = summary
                return summary

            for tool_call in assistant_msg.tool_calls:
                result = await self._tool_service.execute(
                    name=tool_call.function.name,
                    params=json.loads(tool_call.function.arguments),
                )
                sub_messages.append({
                    'role': 'tool',
                    'tool_call_id': tool_call.id,
                    'content': str(result)[:50000],
                })

        self._results[task_id] = f'[subagent] reached max turns ({agent_def.max_turns})'
```

### AgentDefinition

```python
class AgentDefinition:
    """定义子智能体类型：工具、模型、系统提示。"""
    name: str
    description: str
    model: str = None               # None = 使用 capability 对应的默认模型
    capability: str = 'fast'        # 默认使用的 LLM 能力
    max_turns: int = 30
    allowed_tools: list[str]

    def get_system_prompt(self) -> str:
        raise NotImplementedError

    def get_tool_schemas(self, override_tools: list[str] = None) -> list[dict]:
        tools = override_tools or self.allowed_tools
        return [t for t in self._all_schemas if t['function']['name'] in tools]
```

### 内置 Agent Definitions

```python
class ExploreAgent(AgentDefinition):
    """只读搜索专家。快速、便宜、上下文隔离。"""
    name = 'explore'
    description = 'Fast read-only search agent for locating code.'
    capability = 'fast'             # ← 用 fast 能力（DeepSeek-V3）
    max_turns = 30
    allowed_tools = ['read', 'glob', 'grep']

    def get_system_prompt(self):
        return """You are a fast code search agent. Find relevant files, functions, and code patterns.

Rules:
- READ-ONLY. Never create, edit, or delete files.
- Maximize parallel tool calls.
- Report: file paths, line numbers, relevant code snippets.
- Keep responses under 500 words."""


class PlanAgent(AgentDefinition):
    """架构规划 agent。只读，使用强推理模型。"""
    name = 'plan'
    description = 'Software architect agent for designing implementation plans.'
    capability = 'capable'          # ← 用 capable 能力（Claude Opus）
    max_turns = 20
    allowed_tools = ['read', 'glob', 'grep']

    def get_system_prompt(self):
        return """You are a software architect. Design implementation plans.

Rules:
- READ-ONLY. Never create, edit, or delete files.
- Explore codebase to understand existing patterns.
- Design: steps, files, dependencies, risks.
- Consider trade-offs."""


class GeneralAgent(AgentDefinition):
    """通用 agent，全工具。"""
    name = 'general'
    description = 'General-purpose agent for multi-step tasks.'
    capability = 'fast'             # ← 默认 fast，可通过 spawn 参数覆盖
    allowed_tools = ['*']

    def get_system_prompt(self):
        return """You are a general-purpose agent. Complete the task fully.

Rules:
- Don't over-engineer.
- Don't leave work half-done.
- Report what you accomplished concisely."""
```

## Anti-Recursion

子智能体的工具列表排除 `subagent` 工具，防止无限嵌套：

```python
def get_tool_schemas(self, override_tools=None):
    tools = super().get_tool_schemas(override_tools)
    return [t for t in tools if t['function']['name'] != 'subagent']
```

## 完整流程

```
Agent Loop (capability='fast'):
    │
    ├── 用户: "Review this code and then run the tests"
    │
    ├── LLMService.chat(capability='fast') → DeepSeek-V3
    │   → tool_calls: [code_review, bash]
    │
    ├── code_review (tool.provider = 'claude-opus'):
    │   ├── provider 优先级最高
    │   ├── spawn subagent (provider='claude-opus')
    │   │   └── LLMService.resolve('claude-opus')
    │   │       → _providers['claude-opus'] → AnthropicProvider.chat()
    │   │       → code review 结果
    │   └── result → 追加到父 messages
    │
    ├── bash (tool.provider = None, tool.capability = None):
    │   ├── 都未指定 → 用当前 'fast'
    │   ├── tool_service.execute('bash', {command: 'pytest'})
    │   └── result → 追加到父 messages
    │
    └── LLMService.chat(capability='fast') → 最终回复
```

## Files to Create

| File | Purpose |
|------|---------|
| `subagent/__init__.py` | Exports |
| `subagent/service.py` | SubagentService(AppService): spawn/get_result，capability 路由 |
| `subagent/agent_def.py` | AgentDefinition + ExploreAgent + PlanAgent + GeneralAgent |
| `subagent/commands.py` | SpawnSubagent, GetSubagentResult 命令 |

## Dependencies

- `a2.LLMService` — 通过 capability 参数获取对应 LLM
- `a2.ToolService` — 子智能体中的工具执行
