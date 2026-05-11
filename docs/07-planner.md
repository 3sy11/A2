# 07 — 规划器

## Context

规划器是 Agent 的"导航仪"——决定任务是否需要预先规划、是否需要事后修正。

**核心设计决策：**
1. **三层都是 BaseCommand**，不是策略对象。`yield SubCommand()` 就是 bollydog 的装饰器模式
2. **PlannerService(AppService)** 极简，只做配置开关 + TaskList 持久化
3. **TaskList 在 session 中管理**，通过 `bollydog.globals.session` 访问
4. **数据模型用 Pydantic**，不用 dataclass

## 架构

```
PlannerService(AppService)
├── depends: []（无外部依赖）
├── protocol: LocalFileProtocol（TaskList 持久化）
│
├── config switches:
│   ├── use_planning: bool = False    ← 是否启用 PlanCommand
│   └── use_reflexion: bool = False   ← 是否启用 ReflexionCommand
│
└── 接口:
    ├── save_task_list(task_list)     ← 持久化到 protocol
    └── load_task_list(run_id)        ← 从 protocol 恢复

Command 层（三层，全部是 BaseCommand 子类）：
├── RunAgentCommand        ← 入口：组合三层，决定套哪层
├── PlanCommand            ← Layer 2：生成任务列表
├── ReflexionCommand       ← Layer 3：失败反思重试
└── ReActStepCommand       ← Layer 1：单步 ReAct 执行（永远存在）

数据流：
    RunAgentCommand
    ├── yield PlanCommand          → 得到 TaskList
    └── for each task:
        ├── yield ReflexionCommand → 内部 yield ReActStepCommand
        │   或
        └── yield ReActStepCommand → 直接执行
```

### 与 bollydog 的对齐

```
经典装饰器（不适用于 bollydog）：
    ReflexionExecutor(inner=ReActExecutor())
    → 构造时绑定 inner 引用

bollydog 风格（原生支持）：
    ReflexionCommand.__call__()
    → result = yield ReActStepCommand(...)   ← 运行时通过 Hub 动态调度
    → yield SubCommand() 就是装饰器委托，语义等价
```

**区别：** 经典装饰器在构造时绑定 inner，bollydog 在运行时通过 Hub 动态调度 inner。后者更灵活——inner 可以根据运行时状态动态选择。

### Session 管理 TaskList

```
bollydog globals：
    session   — 全局 Session KV（进程生命周期）
    message   — 当前 Command（请求作用域）
    protocol  — App 的存储协议（请求作用域）
    app       — 当前 AppService（请求作用域）
    hub       — Hub 实例（进程生命周期）

TaskList 的生命周期：
    1. PlanCommand 生成 TaskList → session.set(run_id, task_list.dict())
    2. RunAgentCommand 从 session.get(run_id) 读取
    3. ReActStepCommand 完成后 → session.update(run_id, task.status, task.result)
    4. 最终 → PlannerService.save_task_list(task_list) 持久化到文件
```

## 数据模型

```python
# planner/models.py
from pydantic import BaseModel, Field
from typing import Literal
import uuid


class Task(BaseModel):
    """单个任务步骤。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    content: str = ""
    status: Literal["pending", "in_progress", "done", "failed"] = "pending"
    result: str | None = None
    context: str = ""  # 反思后追加，丰富下次重试的上下文

    def with_reflection(self, reflection: str) -> "Task":
        """不可变更新：返回新 Task，追加反思内容。"""
        return Task(
            id=self.id,
            content=self.content,
            status="pending",  # 重置为 pending，重试
            context=f"{self.context}\n[Reflection]: {reflection}".strip(),
        )


class TaskList(BaseModel):
    """任务列表。Pydantic 模型，可直接序列化到 session/protocol。"""
    run_id: str
    goal: str = ""
    tasks: list[Task] = Field(default_factory=list)

    def pending(self) -> list[Task]:
        return [t for t in self.tasks if t.status == "pending"]

    def get_task(self, task_id: str) -> Task | None:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def update_task(self, task_id: str, **kwargs) -> None:
        for t in self.tasks:
            if t.id == task_id:
                for k, v in kwargs.items():
                    setattr(t, k, v)
                break

    def render_progress(self) -> str:
        """渲染进度文本，用于注入 system prompt。"""
        lines = [f'# 任务进度: {self.goal}\n']
        for t in self.tasks:
            marker = {'pending': '[ ]', 'in_progress': '[>]', 'done': '[x]', 'failed': '[!]'}[t.status]
            lines.append(f'{marker} {t.content}')
            if t.result and t.status in ('done', 'failed'):
                lines.append(f'   → {t.result[:100]}')
        done = sum(1 for t in self.tasks if t.status == 'done')
        lines.append(f'\n进度: {done}/{len(self.tasks)} 完成')
        return '\n'.join(lines)
```

## Command 层

### Layer 1: ReActStepCommand（基础层，永远存在）

```python
# planner/commands.py
from bollydog.models.base import BaseCommand
from bollydog.globals import session, message, app
from bollydog.models.service import AppService
from .models import Task, TaskList


class ReActStepCommand(BaseCommand):
    """单步 ReAct 执行：Reason → Act → Observe 循环直到完成。
    最内层，永远存在。"""
    alias = 'ReActStep'

    task: Task
    run_id: str
    max_steps: int = 10

    async def __call__(self):
        history = []
        llm_service = AppService._apps['a2.LLMService']
        tool_service = AppService._apps['a2.ToolService']

        for step in range(self.max_steps):
            # Reason: LLM 决策
            action = await self._reason(llm_service, self.task, history)

            if action["type"] == "final_answer":
                return {"success": True, "result": action["content"]}

            # Act: 执行工具（通过 hub.dispatch 或直接调用）
            observation = await tool_service.execute(
                name=action["tool"],
                params=action["params"],
            )

            history.append({"action": action, "observation": observation})

        return {"success": False, "result": "超过最大步数"}

    async def _reason(self, llm, task: Task, history: list) -> dict:
        """调用 LLM 决策下一步。"""
        context_part = f"\n\n之前的反思:\n{task.context}" if task.context else ""
        history_part = ""
        if history:
            history_part = "\n\n之前的尝试:\n" + "\n".join(
                f"- 执行 {h['action']['tool']}: {str(h['observation'])[:200]}"
                for h in history[-3:]
            )

        prompt = f"""任务: {task.content}{context_part}{history_part}

决定下一步：
- 如果需要工具，返回 {{"type": "tool_call", "tool": "工具名", "params": {{...}}}}
- 如果任务完成，返回 {{"type": "final_answer", "content": "结果"}}

只返回 JSON。"""

        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            capability="fast",
        )
        return json.loads(response.content)
```

### Layer 2: PlanCommand（可选，生成任务列表）

```python
class PlanCommand(BaseCommand):
    """生成任务分解列表，存入 session。"""
    alias = 'Plan'

    goal: str
    run_id: str

    async def __call__(self):
        llm_service = AppService._apps['a2.LLMService']

        # LLM 生成计划
        prompt = f"""分析以下任务，分解为编号步骤。

任务: {self.goal}

要求：
1. 每步应该是可独立执行的原子操作
2. 步骤之间按依赖顺序排列
3. 3-8 步为宜

输出 JSON 格式:
{{"steps": ["步骤1描述", "步骤2描述", ...]}}

只返回 JSON。"""

        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            capability="fast",
        )

        data = json.loads(response.content)
        tasks = [Task(content=step) for step in data["steps"]]
        task_list = TaskList(run_id=self.run_id, goal=self.goal, tasks=tasks)

        # 存入 session（运行时共享）
        await session.set(self.run_id, task_list.model_dump())

        planner_svc = AppService._apps['a2.PlannerService']
        await planner_svc.save_task_list(task_list)

        return task_list
```

### Layer 3: ReflexionCommand（可选，失败反思重试）

```python
class ReflexionCommand(BaseCommand):
    """Reflexion 装饰器：失败时反思，更新上下文，重试。
    yield ReActStepCommand() 就是装饰器委托。"""
    alias = 'Reflexion'

    task: Task
    run_id: str
    max_retries: int = 3

    async def __call__(self):
        current_task = self.task
        llm_service = AppService._apps['a2.LLMService']

        for attempt in range(self.max_retries):
            # yield 委托给内层 = 装饰器的 self._inner.run()
            result = yield ReActStepCommand(
                task=current_task,
                run_id=self.run_id,
            )

            if result["success"]:
                return result

            # 失败：反思 → 丰富上下文 → 用新 task 重试
            reflection = await self._reflect(llm_service, current_task, result)
            current_task = current_task.with_reflection(reflection)

            # 更新 session 中的任务状态
            raw = await session.get(self.run_id)
            if raw:
                task_list = TaskList(**raw)
                task_list.update_task(current_task.id, status="pending")
                await session.set(self.run_id, task_list.model_dump())

        return result

    async def _reflect(self, llm, task: Task, failed_result: dict) -> str:
        """生成自我反思。"""
        prompt = f"""任务: {task.content}
结果: 失败
错误信息: {failed_result.get('result', '')}

分析失败原因，100 字以内：
1. 什么出了问题？
2. 下次应该怎么做？"""

        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            capability="fast",
        )
        return response.content
```

### 入口: RunAgentCommand（组合三层）

```python
class RunAgentCommand(BaseCommand):
    """入口命令。唯一决定"用哪几层"的地方。
    组合逻辑极简：开关 + yield。"""
    alias = 'RunAgent'

    goal: str
    use_planning: bool = True      # ← 开关：是否启用 PlanCommand
    use_reflexion: bool = False    # ← 开关：是否启用 ReflexionCommand

    async def __call__(self):
        run_id = message.trace_id

        # Step 1: 规划（可选）
        if self.use_planning:
            task_list = yield PlanCommand(goal=self.goal, run_id=run_id)
        else:
            # 不规划，直接把 goal 作为单个任务
            task_list = TaskList(
                run_id=run_id,
                goal=self.goal,
                tasks=[Task(content=self.goal)],
            )
            await session.set(run_id, task_list.model_dump())

        # Step 2: 逐任务执行
        for task in task_list.pending():
            # 更新状态为 in_progress
            task_list.update_task(task.id, status="in_progress")
            await session.set(run_id, task_list.model_dump())
            yield {"type": "progress", "task_id": task.id, "status": "in_progress"}

            # 决定套哪层
            if self.use_reflexion:
                result = yield ReflexionCommand(task=task, run_id=run_id)
            else:
                result = yield ReActStepCommand(task=task, run_id=run_id)

            # 更新结果
            status = "done" if result["success"] else "failed"
            task_list.update_task(task.id, status=status, result=result.get("result"))
            await session.set(run_id, task_list.model_dump())

            planner_svc = AppService._apps['a2.PlannerService']
            await planner_svc.save_task_list(task_list)

            yield {"type": "progress", "task_id": task.id, "status": status}

            # 失败时停止后续任务
            if not result["success"]:
                yield {"type": "text", "content": f"任务失败: {task.content}\n{result.get('result', '')}"}
                return

        yield {"type": "text", "content": task_list.render_progress()}
```

## PlannerService (AppService)

```python
class PlannerService(AppService):
    """规划服务：配置开关 + TaskList 持久化。
    极简——所有逻辑在 Command 层。"""
    domain = 'a2'
    alias = 'PlannerService'
    depends = []

    use_planning: bool = False
    use_reflexion: bool = False

    async def on_start(self):
        self.use_planning = self.config.get('use_planning', False)
        self.use_reflexion = self.config.get('use_reflexion', False)

    async def save_task_list(self, task_list: 'TaskList'):
        """持久化 TaskList 到 protocol（文件）。"""
        await self.protocol.write(
            f'{task_list.run_id}.json',
            task_list.model_dump_json(indent=2),
        )

    async def load_task_list(self, run_id: str) -> 'TaskList | None':
        """从 protocol 恢复 TaskList。"""
        try:
            content = await self.protocol.read(f'{run_id}.json')
            return TaskList.model_validate_json(content)
        except FileNotFoundError:
            return None
```

**TOML 配置：**
```toml
["a2.PlannerService"]
module = "a2.planner.service.PlannerService"
use_planning = false
use_reflexion = false

["a2.PlannerService".protocol]
module = "bollydog.adapters.file.LocalFileProtocol"
path = ".agent/tasks/"
```

## 组合示例

```
用户: "修复 login 页面的 CSS bug"
    → use_planning=False, use_reflexion=False
    → RunAgentCommand → ReActStepCommand
    → 最简执行，单任务

用户: "重构 auth 模块并添加测试"
    → use_planning=True, use_reflexion=True
    → RunAgentCommand → PlanCommand → TaskList(3 步)
    → 逐步: ReflexionCommand → ReActStepCommand
    → 失败时反思重试

用户: "部署到生产环境"
    → use_planning=True, use_reflexion=True
    → 最严格：规划 + 每步反思验证
```

## Session 与 Protocol 的分工

```
Session（运行时，内存 KV）：
    ├── 用途：Command 之间共享的运行时状态
    ├── 生命周期：进程级别，重启丢失
    ├── 访问：from bollydog.globals import session
    └── 存储：TaskList（当前执行中的任务状态）

Protocol（持久化，文件）：
    ├── 用途：压缩后存活的持久化存储
    ├── 生命周期：文件级别，重启保留
    ├── 访问：self.protocol（AppService 的 Protocol）
    └── 存储：TaskList（最终结果，用于恢复）
```

**读写路径：**
```
写入：
    Command 执行中 → session.set(run_id, task_list.dict())    ← 运行时
    任务完成/失败  → planner_svc.save_task_list(task_list)    ← 持久化

读取：
    Command 执行中 → session.get(run_id)                      ← 运行时
    恢复上下文后   → planner_svc.load_task_list(run_id)       ← 从文件恢复
```

## 演化路径

```
Phase 1（当前）：
    RunAgentCommand + PlanCommand + ReflexionCommand + ReActStepCommand
    TaskList 扁平列表，session + protocol 双层存储
    use_planning / use_reflexion 开关

Phase 2（按需）：
    Task 加可选 blocked_by 字段（退化为扁平列表 = 无依赖）
    PlanCommand 支持并行执行独立任务 → yield [cmd1, cmd2]

Phase 3（按需）：
    启发式层选择（替代手动开关）
    LLM 辅助判断（可选覆盖层）
```

## Files to Create

| File | Purpose |
|------|---------|
| `planner/__init__.py` | Exports |
| `planner/service.py` | PlannerService(AppService)：配置开关 + 持久化 |
| `planner/models.py` | Task、TaskList（Pydantic） |
| `planner/commands.py` | RunAgentCommand、PlanCommand、ReflexionCommand、ReActStepCommand |

## Dependencies

- `bollydog.globals.session` — 运行时 TaskList 共享
- `bollydog.globals.message` — 当前 Command 数据
- `bollydog.globals.app` — 解析 LLMService、ToolService
- `bollydog.adapters.file.LocalFileProtocol` — TaskList 持久化
