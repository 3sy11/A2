# 01 — Agent Loop 核心

## Context

Agent Loop 是整个 A2 框架的心跳。它是一个 `while` 循环，驱动 LLM 推理 + 工具执行直到任务完成。关键洞察：bollydog 的 `BaseCommand.__call__`（async generator）+ Hub 的 `_run_gen` 已经提供了执行脚手架——我们只需接入 LLM 调用 + 工具分发 + 上下文管理。

**核心设计决策：AgentCommand(BaseCommand) 是 async generator，yield dict（流式 chunk）到 Hub。AgentService(AppService) 通过 depends 依赖全部 7 个子服务。**

## 架构

```
AgentService(AppService)
├── depends: [
│     "a2.LLMService",
│     "a2.ToolService",
│     "a2.ContextService",
│     "a2.PlannerService",
│     "a2.SubagentService",
│     "a2.MCPService",
│     "a2.SkillService"
│   ]
│
├── build_system_prompt() → str       ← 组装系统提示词
└── _agent_command: AgentCommand      ← Agent 循环命令

AgentCommand(BaseCommand) — 入口编排
├── __call__() → AsyncGenerator[dict] ← async generator，yield 流式 chunk
└── 两阶段架构：
    ├── Phase 1: yield PlanCommand（可选）→ 得到 TaskList
    └── Phase 2: 逐任务 yield ReflexionCommand 或 ReActStepCommand

ReActStepCommand(BaseCommand) — 单步 ReAct 执行
├── __call__()                        ← Reason → Act → Observe 循环
├── task: Task                        ← 当前任务
└── max_steps: int = 10               ← 单任务最大步数
```

### Agent Loop 流程

```
AgentCommand.__call__()  [async generator, yield StreamState chunks]
    │
    ├── Phase 1: 规划（可选）
    │   └── yield PlanCommand(goal) → TaskList
    │
    └── Phase 2: 逐任务执行
        └── for each task in TaskList.pending():
            ├── yield ReflexionCommand(task) 或 ReActStepCommand(task)
            │   │
            │   └── ReActStepCommand.__call__():
            │       ├── while not done:
            │       │   1. LLM 推理 → action
            │       │   2. if final_answer → return result
            │       │   3. ToolService.execute(tool)
            │       │   4. 收集 observation → 继续
            │       └── 超过 max_steps → return failure
            │
            ├── 更新 TaskList 状态
            └── yield progress chunk → Hub
```

## Class Signatures

### AgentCommand (BaseCommand)

```python
class AgentCommand(BaseCommand, abstract=True):
    """Agent 命令基类。使用 bollydog 的 async generator 模式：
    yield SubCommand() 用于顺序执行，yield [cmd1, cmd2] 用于并行扇出。"""
    alias: ClassVar[str] = 'AgentCommand'
    max_turns: ClassVar[int] = 50
    stream: ClassVar[bool] = True

    def __init__(self, *, service: 'AgentService', **kwargs):
        super().__init__(**kwargs)
        self._service = service
        self._turn_count = 0

    async def __call__(self, **kwargs) -> AsyncGenerator[dict, None]:
        """主 Agent 循环——async generator，yield 流式 chunk。

        两阶段架构：
        1. 规划阶段：yield PlanCommand（可选）生成 TaskList
        2. 执行阶段：逐任务 yield ReflexionCommand 或 ReActStepCommand
        """
        goal = kwargs.get('user_message', '')
        planner = self._service.planner

        # ── Phase 1: 规划（可选）──
        if planner.use_planning:
            # yield PlanCommand → Hub 调度 → 返回 TaskList
            task_list = yield PlanCommand(goal=goal, run_id=message.trace_id)
        else:
            task_list = TaskList(
                run_id=message.trace_id,
                goal=goal,
                tasks=[Task(content=goal)],
            )

        # ── Phase 2: 逐任务执行 ──
        for task in task_list.pending():
            # 更新状态
            task_list.update_task(task.id, status="in_progress")
            yield {'type': 'progress', 'task_id': task.id, 'status': 'in_progress'}

            # 套哪层
            if planner.use_reflexion:
                result = yield ReflexionCommand(task=task, run_id=task_list.run_id)
            else:
                result = yield ReActStepCommand(task=task, run_id=task_list.run_id)

            # 更新结果
            status = "done" if result["success"] else "failed"
            task_list.update_task(task.id, status=status, result=result.get("result"))
            yield {'type': 'progress', 'task_id': task.id, 'status': status}

            # 失败时停止
            if not result["success"]:
                yield {'type': 'text', 'content': f"任务失败: {task.content}\n{result.get('result', '')}"}
                return

        # 完成
        yield {'type': 'text', 'content': task_list.render_progress()}
```

### AgentService (AppService)

```python
class AgentService(AppService):
    """Agent 服务：顶层编排，依赖全部 7 个子服务。"""
    domain = 'a2'
    alias = 'AgentService'
    depends = [
        'a2.LLMService',
        'a2.ToolService',
        'a2.ContextService',
        'a2.PlannerService',
        'a2.SubagentService',
        'a2.MCPService',
        'a2.SkillService',
    ]

    def build_system_prompt(self) -> str:
        """组装系统提示词：静态段 + 动态段。"""
        sections = [
            self._static_identity_section(),
            self._static_system_rules(),
            self._static_task_execution(),
            self._static_action_safety(),
            self._static_tool_usage(),
            self._static_tone_style(),
            SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
            self._dynamic_session_guidance(),
            self._dynamic_skill_index(),
            self._dynamic_env_info(),
            self._dynamic_memory_context(),
        ]
        return '\n\n'.join(filter(None, sections))

    def _dynamic_skill_index(self) -> str:
        """动态段：技能索引（来自 SkillService）。"""
        return self._skill_service.get_metadata_prompt()

    def _dynamic_memory_context(self) -> str:
        """动态段：记忆上下文（来自 MemoryService）。"""
        # 由 ContextService 在 build_context 中处理
        return ''
```

## 与 bollydog Hub 的集成

关键集成点是 Hub 的 `_run_gen` 方法，它已经处理 async generators：

```python
# 在 Hub._run_gen（bollydog/service/app.py）中：
async def _run_gen(self, message):
    async for chunk in message():
        if isinstance(chunk, list):
            # 并行扇出：yield [cmd1, cmd2]
            results = await self.gather(chunk)
            for r in results:
                message.state.put_nowait(r)
        elif isinstance(chunk, BaseCommand):
            # 顺序：yield SubCommand()
            await self.dispatch(chunk)
            await chunk.state
            message.state.put_nowait(chunk.state.result())
        else:
            # 普通数据：yield dict（流式输出）
            message.state.put_nowait(chunk)
    message.state.put_nowait(None)  # 信号完成
```

`AgentCommand.__call__` yield dict（流式 chunk），通过 `_run_gen` 流入 `StreamState`，支持 `await agent_cmd`（获取所有结果）和 `async for chunk in agent_cmd`（流式）两种模式。

## 错误恢复

错误恢复在两个层面处理：

**ReActStepCommand 内部（单任务）：**
- 工具执行错误 → 错误信息作为 observation，LLM 自动调整方法
- 超过 max_steps → 返回 failure

**ReflexionCommand 层（跨任务重试）：**
- 任务失败 → 反思生成 → 丰富上下文 → 用新 Task 重试
- 超过 max_retries → 返回最终失败

## Files to Create

| File | Purpose |
|------|---------|
| `agent/__init__.py` | Exports |
| `agent/service.py` | AgentService(AppService)：顶层编排 |
| `agent/command.py` | AgentCommand(BaseCommand)：agent loop |
| `agent/commands.py` | Chat、ClearSession 命令 |

## Dependencies

- `bollydog.models.base.BaseCommand` — 父类
- `bollydog.models.service.AppService` — 父类
- `bollydog.service.app.Hub._run_gen` — async generator runner
- 全部 7 个子服务（通过 depends 声明）
