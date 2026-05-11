# 15 — Command 分离执行计划

## Context

bollydog 的核心原则：**Service 之间不直接调用，通过 Command 在 Hub 中调度。** Command 运行在 Hub 上下文中，通过 `AppService._apps['key']` 访问其他 Service。Service 之间的调用必须通过 `yield SubCommand()` 委托给 Hub。

当前设计中有若干处 Service 直接调用 Service 的情况（详见 [00-master-plan.md](00-master-plan.md) 第九节），需要逐一抽取为 Command。

## 核心原则

> **API 约定**：Command 内部通过 `AppService._apps['key']` 访问其他服务，通过 `globals.app` 访问所属服务。
> 详见 [10-bollydog-integration-conventions.md](10-bollydog-integration-conventions.md)。

```
允许的调用方式：
  Command → AppService._apps['key'].method()  ✅ 通过 _apps 全局注册表
  Command → app.method()                       ✅ globals.app 访问所属服务
  Service → self._injected.method()            ✅ 通过 depends 注入
  Command → yield SubCommand()                  ✅ Hub 调度

禁止的调用方式：
  Service → AppService._apps['key'].method()   ❌ 绕过 depends
  Service → self._not_injected.method()         ❌ 属性不存在
  Tool → self._xxx_service.method()             ❌ Tool 只有 self._service
  Command → app.resolve('key')                  ❌ API 不存在
```

## 需要 Command 化的调用清单

### 1. ExtractPatternCommand

| 属性 | 值 |
|------|-----|
| **调用方** | SkillService（结晶流程） |
| **被调用方** | LLMService |
| **触发场景** | 任务成功后，从执行轨迹中提取可复用模式 |
| **严重程度** | CRITICAL — 当前 `self._llm` 不存在 |

**当前代码（04-skill-system.md）：**
```python
# SkillService._extract_skill_pattern — 直接调用，运行时崩溃
response = await self._llm.chat(
    messages=[{'role': 'user', 'content': prompt}],
    capability='fast',
)
```

**Command 化后：**
```python
# skills/commands.py
from bollydog.models.service import AppService

class ExtractPatternCommand(BaseCommand):
    """从执行轨迹中提取技能模式。"""
    alias = 'ExtractPattern'
    trajectory: str
    capability: str = 'fast'

    async def __call__(self):
        llm = AppService._apps['a2.LLMService']
        return await llm.chat(
            messages=[{'role': 'user', 'content': self.trajectory}],
            capability=self.capability,
        )
```

---

### 2. CrystallizeSkillCommand

| 属性 | 值 |
|------|-----|
| **调用方** | AgentCommand（任务成功后） |
| **被调用方** | SkillService + LLMService + L1InsightIndexService |
| **触发场景** | 多步任务（≥3 次工具调用）成功后，结晶为可复用技能 |
| **严重程度** | HIGH — 编排多个服务的完整流程 |

**编排流程：**
1. yield ExtractPatternCommand → 获取执行模式
2. SkillService.save_skill() → 持久化技能
3. yield NotifyIndexUpdateCommand → 更新 L1 索引

```python
# skills/commands.py
class CrystallizeSkillCommand(BaseCommand):
    """结晶：从执行轨迹创建可复用技能。"""
    alias = 'CrystallizeSkill'
    trajectory: str
    task_content: str
    tool_call_count: int = 0

    async def __call__(self):
        # 门槛检查
        if self.tool_call_count < 3:
            return None

        # 1. 提取模式
        pattern = yield ExtractPatternCommand(
            trajectory=self.trajectory,
            capability='fast',
        )

        # 2. 创建并保存技能
        skill_service = AppService._apps['a2.SkillService']
        skill = Skill.from_pattern(pattern, self.task_content)
        await skill_service.save_skill(skill)

        # 3. 通知 L1 更新索引
        yield NotifyIndexUpdateCommand(
            skill_name=skill.name,
            skill_desc=skill.description,
            action='add',
        )

        return skill
```

---

### 3. NotifyIndexUpdateCommand

| 属性 | 值 |
|------|-----|
| **调用方** | CrystallizeSkillCommand、L3TaskSkillsService |
| **被调用方** | L1InsightIndexService |
| **触发场景** | 技能新增/更新/删除时，同步 L1 路由表 |
| **严重程度** | HIGH — 当前未声明任何调用机制 |

```python
# memory/commands.py
class NotifyIndexUpdateCommand(BaseCommand):
    """技能变更时通知 L1 更新索引。"""
    alias = 'NotifyIndexUpdate'
    skill_name: str
    skill_desc: str
    action: str = 'add'  # 'add' | 'remove' | 'update'

    async def __call__(self):
        l1 = AppService._apps['a2.memory.l1.L1InsightIndexService']
        if self.action == 'add':
            await l1.add_skill(self.skill_name, self.skill_desc)
        elif self.action == 'remove':
            await l1.remove_skill(self.skill_name)
        elif self.action == 'update':
            await l1.update_skill(self.skill_name, self.skill_desc)
```

---

### 4. SpawnSubagentCommand

| 属性 | 值 |
|------|-----|
| **调用方** | SubagentTool |
| **被调用方** | SubagentService |
| **触发场景** | Agent 委派子智能体执行子任务 |
| **严重程度** | CRITICAL — 当前引用不存在的 `self._subagent_service` |

**当前代码（02-tool-system.md）：**
```python
# SubagentTool.execute — 运行时崩溃
async def execute(self, agent_type: str, prompt: str, **kw) -> str:
    result = await self._subagent_service.spawn(
        task=prompt, agent_type=agent_type)
    return result.summary
```

**Command 化后：**
```python
# subagent/commands.py
class SpawnSubagentCommand(BaseCommand):
    """委派子智能体执行任务。"""
    alias = 'SpawnSubagent'
    task: str
    agent_type: str = 'general'
    provider: str = None
    capability: str = None

    async def __call__(self):
        subagent_svc = AppService._apps['a2.SubagentService']
        return await subagent_svc.spawn(
            task=self.task,
            agent_type=self.agent_type,
            provider=self.provider,
            capability=self.capability,
        )
```

**SubagentTool 修改：**
```python
# tools/builtin.py
class SubagentTool(Tool):
    name = 'subagent'
    description = '委派子任务给专门的子智能体'

    async def execute(self, agent_type: str, prompt: str, **kw) -> str:
        from bollydog.globals import hub
        cmd = SpawnSubagentCommand(task=prompt, agent_type=agent_type)
        result = await hub.execute(cmd)
        return str(await result.state)
```

---

### 5. AgentService depends 补全

| 属性 | 值 |
|------|-----|
| **调用方** | AgentService |
| **被调用方** | SkillService |
| **触发场景** | `build_system_prompt()` 获取技能元数据 |
| **严重程度** | HIGH — 属性不存在 |

**不是 Command 化场景**——AgentService 是顶层编排，SkillService 是数据提供者。通过 depends 注入即可（与 ContextService → MemoryService 同模式）。

**修改：**
```python
# agent/service.py
class AgentService(AppService):
    depends = [
        'a2.LLMService',
        'a2.ToolService',
        'a2.ContextService',
        'a2.PlannerService',
        'a2.SubagentService',
        'a2.MCPService',
        'a2.SkillService',     # 新增
    ]
```

---

## 不需要 Command 化的调用

以下调用已符合 bollydog 模式，无需修改：

| 调用 | 方式 | 原因 |
|------|------|------|
| ReActStepCommand → LLMService | `AppService._apps['key']` | Command 内，标准模式 |
| ReActStepCommand → ToolService | `AppService._apps['key']` | Command 内，标准模式 |
| PlanCommand → LLMService | `AppService._apps['key']` | Command 内，标准模式 |
| ReflexionCommand → LLMService | `AppService._apps['key']` | Command 内，标准模式 |
| ContextService → MemoryService | `self._memory` | depends 注入 |
| ContextService → LLMService | `self._llm` | depends 注入 |
| SubagentService → LLMService | `self._llm` | depends 注入 |
| SubagentService → ToolService | `self._tool_service` | depends 注入 |
| MCPService → ToolService | `tool_service` | depends 注入，初始化阶段 |
| MemoryService → L0-L4 | `self._l0` ~ `self._l4` | depends 注入 |
| L3TaskSkillsService → SkillService | `self._skill_service` | depends 注入 |

---

## 文件变更清单

### 新增文件

| 文件 | Commands | 行数 |
|------|----------|------|
| `skills/commands.py` | ExtractPatternCommand, CrystallizeSkillCommand | ~50 |
| `memory/commands.py` | NotifyIndexUpdateCommand | ~25 |
| `subagent/commands.py` | SpawnSubagentCommand | ~25 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `tools/builtin.py` | SubagentTool.execute() 改为 yield SpawnSubagentCommand |
| `agent/service.py` | depends 加 `'a2.SkillService'` |
| `01-agent-loop.md` | AgentService depends 加 SkillService |
| `02-tool-system.md` | SubagentTool 代码改为 Command 化版本 |
| `04-skill-system.md` | SkillService 结晶流程改为 yield CrystallizeSkillCommand |
| `11-configuration.md` | PlannerService TOML 字段改为 `use_planning`/`use_reflexion` |
| `00-master-plan.md` | DAG 加 SubagentService → ToolService；补充问题清单 |

---

## 执行顺序

按依赖关系排序——先建 Command，再修改调用方：

```
Step 1: 创建 Command 文件（无依赖）
  ├── skills/commands.py     → ExtractPatternCommand
  ├── memory/commands.py     → NotifyIndexUpdateCommand
  └── subagent/commands.py   → SpawnSubagentCommand

Step 2: 创建编排 Command（依赖 Step 1）
  └── skills/commands.py     → CrystallizeSkillCommand（yield ExtractPattern + NotifyIndexUpdate）

Step 3: 修改调用方
  ├── tools/builtin.py       → SubagentTool 改为 yield SpawnSubagentCommand
  ├── agent/service.py       → depends 加 SkillService
  └── 04-skill-system.md     → 结晶流程改为 yield CrystallizeSkillCommand

Step 4: 修复文档不一致
  ├── 01-agent-loop.md       → depends 加 SkillService
  ├── 02-tool-system.md      → SubagentTool 代码
  ├── 08-subagent.md         → depends 加 ToolService（已完成）
  ├── 11-configuration.md    → PlannerService TOML 字段
  └── 00-master-plan.md      → DAG + 问题清单（已完成）
```

---

## Command 化后的调用链全景

```
Hub dispatch
└── AgentCommand.__call__()
    │
    ├── yield PlanCommand(goal)
    │   └── AppService._apps['a2.LLMService'].chat()       ← Command 内
    │
    └── for task in task_list.pending():
        │
        ├── yield ReflexionCommand(task) 或 ReActStepCommand(task)
        │   │
        │   └── ReActStepCommand.__call__():
        │       ├── AppService._apps['a2.LLMService'].chat()  ← Command 内
        │       ├── AppService._apps['a2.ToolService'].execute()
        │       │   └── SubagentTool.execute()
        │       │       └── hub.execute(SpawnSubagentCommand)  ← Hub 调度
        │       │           └── SubagentService.spawn()
        │       │               ├── self._llm.chat()           ← depends
        │       │               └── self._tool_service         ← depends
        │       │
        │       └── ContextService.update(result)              ← Command 内
        │
        └── (任务成功，tool_call_count ≥ 3)
            └── yield CrystallizeSkillCommand()                ← Hub 调度
                ├── yield ExtractPatternCommand()              ← Hub 调度
                │   └── AppService._apps['a2.LLMService'].chat()
                ├── AppService._apps['a2.SkillService'].save_skill()
                └── yield NotifyIndexUpdateCommand()           ← Hub 调度
                    └── AppService._apps['a2.memory.l1...'].add_skill()
```

**所有跨服务调用都经过 Hub 调度或 depends 注入，无直接 Service → Service 调用。**
**Command 通过 `AppService._apps['key']` 访问其他服务（见 [10-bollydog-integration-conventions.md](10-bollydog-integration-conventions.md)）。**
