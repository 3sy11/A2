# 模块：PlannerService

> domain=`planner` | 单一实例 | depends: [LLMService]

---

## 架构

```
PlannerService(AppService), domain="planner", depends=["llm.LLMService"]
├── 无 protocol（默认模式）
├── use_planning / use_reflexion (bool)
├── persist_tasks: bool = True        ← 开关：是否落 tasks.db（默认开启）

Command 三层（通过 yield 组合）:
├── ReActStepCommand  ← Layer 1: 单步 Reason→Act→Observe
├── PlanCommand       ← Layer 2: 生成 TaskList（可选）
└── ReflexionCommand  ← Layer 3: 失败反思重试（可选）
```

## Tasks 持久化 — 开关模式

默认 `persist_tasks=True`：TaskList 自动写入 `{role.base_dir}/tasks.db`，支持中断恢复和历史回溯。

```python
class PlannerService(AppService):
    depends = ['llm.LLMService']
    persist_tasks: bool = True
    _tasks_store: MemoryLayerService | None = None

    async def ensure_tasks_store(self, base_dir: str):
        if self._tasks_store: return self._tasks_store
        spec = ServiceSpec(key='tasks', module='a2.memory.service.MemoryLayerService', db='tasks.db', table='tasks')
        self._tasks_store = _create_from_spec(base_dir, spec)
        await self._tasks_store.maybe_start()
        return self._tasks_store

    async def save_task_list(self, task_list: TaskList, base_dir: str):
        if not self.persist_tasks: return
        store = await self.ensure_tasks_store(base_dir)
        await store.set(task_list.run_id, task_list.model_dump_json())

    async def load_task_list(self, run_id: str, base_dir: str) -> TaskList | None:
        if not self.persist_tasks: return None
        store = await self.ensure_tasks_store(base_dir)
        raw = await store.get(run_id)
        return TaskList.model_validate_json(raw) if raw else None
```

| persist_tasks | 行为 | 适用场景 |
|--------------|------|---------|
| `True`（默认） | 写入 `{role.base_dir}/tasks.db`，支持中断恢复 | 所有场景 |
| `False` | TaskList 存 session，run 结束释放 | 极简单 agent |

## Command 层

- **ReActStepCommand**：循环 `reason → act → observe`，`max_steps=10`
- **PlanCommand**：LLM 分解 goal → TaskList → 可选持久化
- **ReflexionCommand**：`yield ReActStepCommand`，失败 → LLM 反思 → `task.with_reflection()` → 重试，`max_retries=3`

## Orchestrator 决策机制

Orchestrator 是 `can_spawn=True` 的特殊角色，**LLM 就是决策引擎**。System prompt 注入可用角色描述（由 `AgentService.get_available_description()` 生成），LLM 自主判断 spawn 哪个角色。

Orchestrator 通过 `spawn_agent` / `ask_user` / `create_role` 三个 Tool 完成决策和执行。PlanCommand 可选。

## 配置 → config.py

```python
'planner.PlannerService': {
    'module': 'a2.planner.service.PlannerService',
    'depends': ['llm.LLMService'],
    'use_planning': False,
    'use_reflexion': False,
    'persist_tasks': True,
},
```

## Files

`planner/service.py`、`planner/models.py`（Task/TaskList）、`planner/commands.py`（ReAct/Plan/Reflexion）
