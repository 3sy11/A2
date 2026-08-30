# 模块：AgentService（编排 + 角色管理）

> domain=`agent` | 顶层编排 | Protocol: CacheLayer → SQLiteProtocol(roles表)

---

## 一、AgentService

```python
class AgentService(AppService):
    depends = ['llm.LLMService', 'env.local',       # env 默认 local，TOML 可替换
               'skills.SkillService', 'planner.PlannerService', 'mcp.MCPService']

    _roles: dict[str, RoleDef] = {}
    _active_contexts: dict[str, ContextService] = {}
    _env: EnvService = None                          # 单实例

    async def on_started(self):
        for dep in self.depends:
            match dep.alias:
                case 'LLMService': self._llm = dep
                case 'local' | 'docker' | 'ssh': self._env = dep
                case 'SkillService': self._skill = dep
                case 'PlannerService': self._planner = dep
                case 'MCPService': self._mcp = dep
        raw = await self.protocol.get_all()
        for name, data in raw.items():
            self._roles[name] = RoleDef(**json.loads(data)) if isinstance(data, str) else RoleDef(**data)
        if 'default' in self._roles:
            await self.activate_role('default')
            log.info(f'预激活 default 角色')
```

### 1.1 activate_role — 动态创建 ContextService

```python
async def activate_role(self, role_name: str) -> ContextService:
    role_def = self._roles.get(role_name)
    if not role_def: raise ValueError(f"Role '{role_name}' not found")
    ns = role_def.effective_namespace
    if ns in self._active_contexts: return self._active_contexts[ns]

    alias = f'{ns}_ContextService'
    ctx_cls = type(alias, (ContextService,), {'alias': alias})
    ctx = ctx_cls()
    ctx.config = {
        'role_specs': role_def.service_specs or ROLE_DEFAULT,
        'base_dir': role_def.base_dir,
        'token_budget': self.config.get('context_token_budget', 128000),
        'compact_threshold': self.config.get('context_compact_threshold', 13000),
    }
    ctx._skill = self._skill
    ctx._llm = self._llm
    await ctx.maybe_start()
    self._active_contexts[ns] = ctx
    log.info(f'角色激活: {role_name} ns={ns}')
    return ctx

async def deactivate_role(self, role_name: str):
    ns = self._roles[role_name].effective_namespace
    if ns not in self._active_contexts: return
    ctx = self._active_contexts.pop(ns)
    await ctx.stop()

def get_available_description(self) -> str:
    lines = []
    for name, role in self._roles.items():
        if name == 'orchestrator': continue
        desc = f"- {name}: {role.system_prompt[:80]}... 工具={role.tools}, 模型={role.model or 'default'}"
        if role.env != 'env.local': desc += f", 环境={role.env}"
        lines.append(desc)
    return '\n'.join(lines)
```

### 1.2 工具聚合（环境无关工具注册在 AgentService 上）

```python
def get_agent_tool_schemas(self) -> list[dict]:
    """环境无关工具：ask_user, spawn_agent, create_role, present_files, web_search, web_fetch"""
    return [cmd.to_schema() for cmd in self._agent_tools]
```

---

## 二、AgentCommand

```python
class AgentCommand(BaseCommand):
    alias = 'AgentCommand'; qos: int = 0
    goal: str = ''; role_def: 'RoleDef | None' = None

    async def __call__(self):
        svc = app
        if not self.role_def:
            self.role_def = svc._roles.get('default') or RoleDef(name='default', system_prompt='')

        ns = self.role_def.effective_namespace
        role_ctx = svc._active_contexts.get(ns) or await svc.activate_role(self.role_def.name)

        if svc._planner.use_planning or self.role_def.use_planning:
            task_list = yield PlanCommand(goal=self.goal, run_id=message.trace_id)
        else:
            task_list = TaskList(run_id=message.trace_id, goal=self.goal, tasks=[Task(content=self.goal)])

        for task in task_list.pending():
            task_list.update_task(task.id, status="in_progress")
            yield {'type': 'progress', 'task_id': task.id, 'status': 'in_progress'}

            use_reflexion = svc._planner.use_reflexion or self.role_def.use_reflexion
            cmd_cls = ReflexionCommand if use_reflexion else ReActStepCommand
            result = yield cmd_cls(task=task, run_id=task_list.run_id, role_ctx=role_ctx)

            status = "done" if result["success"] else "failed"
            task_list.update_task(task.id, status=status, result=result.get("result"))
            yield {'type': 'progress', 'task_id': task.id, 'status': status}
            if not result["success"]:
                yield {'type': 'text', 'content': f"任务失败: {task.content}"}; return

        yield {'type': 'text', 'content': task_list.render_progress()}
```

---

## 三、Command 暴露

### SpawnAgentCommand

```python
class SpawnAgentCommand(BaseCommand):
    alias = 'SpawnAgent'; role: str; input: dict = {}

    async def __call__(self):
        svc = app
        role_def = svc._roles.get(self.role)
        if not role_def: return {"success": False, "error": f"Role '{self.role}' not found"}
        if role_def.effective_namespace not in svc._active_contexts:
            await svc.activate_role(self.role)

        depth = (await session.get('agent_depth')) or 0
        if depth >= 2: return {"success": False, "error": "Max depth"}
        if not role_def.can_spawn and depth > 0:
            return {"success": False, "error": f"'{self.role}' cannot spawn"}

        await session.set('agent_depth', depth + 1)
        result = yield AgentCommand(role_def=role_def, goal=str(self.input.get('task', '')),
                                     session_namespace=f"{role_def.effective_namespace}/{message.trace_id}")
        await session.set('agent_depth', depth)
        return result
```

### CreateRoleCommand

```python
class CreateRoleCommand(BaseCommand):
    alias = 'CreateRole'
    name: str; system_prompt: str; tools: list[str] = ['*']
    model: str | None = None; env: str = 'env.local'; can_spawn: bool = False

    async def __call__(self):
        svc = app
        if self.name in svc._roles: return {"success": False, "error": f"Role '{self.name}' exists"}
        role_def = RoleDef(name=self.name, system_prompt=self.system_prompt,
                          tools=self.tools, model=self.model, env=self.env, can_spawn=self.can_spawn)
        await svc.protocol.set(self.name, role_def.model_dump_json())
        svc._roles[self.name] = role_def
        log.info(f'角色创建: {self.name}')
        return {"success": True, "role": self.name}
```

### 防递归

| 机制 | 说明 |
|------|------|
| `can_spawn=False`（默认） | 只有 Orchestrator 允许 spawn |
| `agent_depth` | Session 传递，硬限制 ≤ 2 |
| 工具过滤 | 非 can_spawn 角色不含 spawn_agent |

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

v1 实现 Pipeline + Fan-out，v2 引入 Exchange 驱动的 Reactive 模式。

---

## 五、自演化

```
任务成功 → yield CrystallizeSkillCommand
  → yield ExtractPatternCommand → app._llm 提取模式
  → app._skill.save_skill(skill)
  → yield NotifyIndexUpdateCommand → role_ctx._l1.set()

任务失败 → 有价值信息? → yield AddFactCommand → role_ctx._facts.add_fact()
```

---

## 六、与 Hub 的集成

### 错误恢复分层

| 层级 | 机制 | 范围 |
|------|------|------|
| ReActStep | `max_steps=10` | 单任务 |
| Reflexion | `max_retries=3` + 反思注入 | 跨重试 |
| AgentCommand | 失败 → 停止后续 | 任务列表 |
| Hub | `gen.athrow(exc)` + pending | 框架级 |

## Files

`agent/service.py`（AgentService + 角色管理）、`agent/commands.py`（AgentCommand + SpawnAgent + CreateRole + Chat + ClearSession + 环境无关工具）、`agent/role_def.py`（RoleDef）
