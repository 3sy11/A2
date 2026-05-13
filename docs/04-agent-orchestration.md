# 04 — Agent 编排（Phase 4）

> 顶层编排：AgentService + AgentCommand + 多角色。

---

## 一、AgentService

```python
class AgentService(AppService):
    domain = 'a2'; alias = 'AgentService'
    depends = ['a2.LLMService', 'a2.ToolService', 'a2.ContextService',
               'a2.PlannerService', 'a2.MCPService', 'a2.SkillService',
               'a2.AgentRegistryService']
```

纯编排角色，无自有业务逻辑。所有实际执行在 Command 层。

---

## 二、AgentCommand

```python
class AgentCommand(BaseCommand):
    alias = 'AgentCommand'; qos: int = 0  # 流式不走队列
    goal: str = ''; role_def: 'RoleDef | None' = None
    session_namespace: str | None = None

    async def __call__(self):
        if not self.role_def:
            registry = AppService._apps['a2.AgentRegistryService']
            self.role_def = await registry.get('default')

        planner = AppService._apps['a2.PlannerService']
        context_svc = AppService._apps['a2.ContextService']

        # Phase 1: 规划（可选）
        if planner.use_planning or self.role_def.use_planning:
            task_list = yield PlanCommand(goal=self.goal, run_id=message.trace_id)
        else:
            task_list = TaskList(run_id=message.trace_id, goal=self.goal,
                                tasks=[Task(content=self.goal)])

        # Phase 2: 逐任务执行
        for task in task_list.pending():
            task_list.update_task(task.id, status="in_progress")
            yield {'type': 'progress', 'task_id': task.id, 'status': 'in_progress'}

            use_reflexion = planner.use_reflexion or self.role_def.use_reflexion
            if use_reflexion:
                result = yield ReflexionCommand(task=task, run_id=task_list.run_id)
            else:
                result = yield ReActStepCommand(task=task, run_id=task_list.run_id)

            status = "done" if result["success"] else "failed"
            task_list.update_task(task.id, status=status, result=result.get("result"))
            yield {'type': 'progress', 'task_id': task.id, 'status': status}

            if not result["success"]:
                yield {'type': 'text', 'content': f"任务失败: {task.content}"}
                return

        yield {'type': 'text', 'content': task_list.render_progress()}
```

---

## 三、与 Hub 的集成

### _run_gen 双向通信

```
result = yield SubCommand()     → feedback = 子命令返回值
yield {'type': 'text', ...}     → StreamState 流式输出，feedback = None
[r1, r2] = yield [Cmd1, Cmd2]  → 并行执行，feedback = 结果列表
```

### 错误恢复分层

| 层级 | 机制 | 范围 |
|------|------|------|
| ReActStep | `max_steps=10` | 单任务 |
| Reflexion | `max_retries=3` + 反思注入 | 跨重试 |
| AgentCommand | 失败 → 停止后续 | 任务列表 |
| Hub | `gen.athrow(exc)` + pending | 框架级 |

---

## 四、多角色编排

### Pipeline（串行）

```python
risk_r = yield SpawnAgentCommand(role="risk_agent", input={"order": order})
if not risk_r["success"]: return
inv_r = yield SpawnAgentCommand(role="inventory_agent", input={**order, **risk_r["data"]})
```

### Fan-out（并行）

```python
[sales_r, inv_r, fin_r] = yield [
    SpawnAgentCommand(role="sales_agent", input=ctx),
    SpawnAgentCommand(role="inventory_agent", input=ctx),
    SpawnAgentCommand(role="finance_agent", input=ctx),
]
```

### Reactive（v2，事件驱动）

```python
await exchange.publish("inventory.low", {"sku": sku})
exchange.subscribe("inventory.low", self._on_inventory_low)
```

**v1 实现 Pipeline + Fan-out，v2 引入 Reactive。**

---

## 五、完整执行流程

```
用户输入 → Hub.dispatch(AgentCommand(goal=...))
│
├── Phase 1（可选）: yield PlanCommand → TaskList
│
└── Phase 2: for task in task_list:
    ├── ContextService.build_messages(role_def, task, history)
    │   → [system(静态)] + [system(skill reminder)] + [对话历史]
    │
    ├── yield ReActStepCommand(task)
    │   ├── LLMService.chat(messages, tools)
    │   ├── ToolService.execute(name, params)
    │   │   └── spawn_agent? → hub.execute(SpawnAgentCommand)
    │   └── observation → history
    │
    ├── 成功且 tool_calls ≥ 3?
    │   └── yield CrystallizeSkillCommand
    │
    └── yield {'type': 'progress', ...}
```

---

## 六、自演化

```
任务成功 → CrystallizeSkillCommand
  → ExtractPatternCommand → LLMService 提取模式
  → SkillService.save_skill(skill)
  → NotifyIndexUpdateCommand → L1.set()

任务失败 → 有价值信息? → L2GlobalFactsService.add_fact()
```

### Files

`agent/service.py`、`agent/command.py`、`agent/commands.py`（Chat/ClearSession）
