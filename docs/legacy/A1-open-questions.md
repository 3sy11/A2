# 问题附录

> 设计审查问题、待研究事项。已解决的设计决策已各自归入对应模块文档，不再重复索引。

---

## 一、设计审查问题

### C2. SkillService Protocol 方案 — 保留 CacheLayer + Service 层文件 I/O

**问题**：Skill 改为 DB + 文件引用后，Protocol 需要同时操作数据库和文件系统。

**分析**：三个方案对比：

| 方案 | 描述 | 优劣 |
|------|------|------|
| A. 自定义 SkillProtocol 复合协议 | 外层管文件，内层管 DB | 复杂，bollydog 协议嵌套是 Protocol-holds-Protocol，不适合异构（KV+文件） |
| B. **CacheLayer(DB) + Service 层文件 I/O** | 元数据走 CacheLayer → SQLiteProtocol，文件读写在 SkillService 方法中直接处理 | 简单，复用现有基础设施，职责清晰 |
| C. 放弃 CacheLayer，直接 SQLiteProtocol | 元数据不缓存 | 失去内存热缓存，每次匹配都查 DB |

**决策：方案 B** — CacheLayer 负责元数据缓存（全量加载到内存，支持关键词匹配等高频操作），文件 I/O 在 SkillService 业务层处理。

```python
class SkillService(AppService):
    # protocol: CacheLayer → SQLiteProtocol(skills.db/skills) — 元数据
    skills_dir: str = '.agent/skills'

    async def on_started(self):
        for name, raw in self.protocol._cache.items():
            self._skills[name] = Skill.model_validate_json(raw) if isinstance(raw, str) else Skill(**raw)

    async def save_skill(self, skill: Skill):
        """DB 写元数据 + 文件写正文。save_skill 内部统一处理。"""
        await self.protocol.set(skill.name, skill.model_dump_json())
        self._skills[skill.name] = skill
        if skill.body_path:
            path = Path(self.skills_dir) / skill.body_path
            path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(path, 'w') as f: await f.write(skill.body_text)
        log.info(f'技能保存: {skill.name} body={skill.body_path}')

    async def load_skill(self, name: str) -> str:
        skill = self._skills.get(name)
        if not skill or not skill.body_path: return ''
        path = Path(self.skills_dir) / skill.body_path
        if not path.exists():
            log.warning(f'技能正文文件缺失: {path}，返回空')
            return ''
        async with aiofiles.open(path) as f: return await f.read()

    def get_matching(self, query: str, skill_refs=None) -> list[Skill]:
        """内存匹配，不查 DB。CacheLayer 已全量加载。"""
        candidates = [s for s in self._skills.values()
                      if not skill_refs or '*' in skill_refs or s.name in skill_refs]
        return [s for s in candidates if any(kw in query.lower() for kw in s.trigger_keywords)]
```

**v2 演进**：技能 1000+ 时迁移 CRUDProtocol 列级索引。v1 全量内存缓存足够。

---

### C4. depends 绑定 + 角色热更新（合并原 Q2）

**问题 A — depends 绑定方式**：`on_started` 中 `match dep.alias` 模式不安全，需要确认 `svc.depends` 解析后的实际类型。

**备注**：计划修改 bollydog 基础框架的 depends 设计。两个方向：

**方向 A — depends 改为字典声明**：
```python
class AgentService(AppService):
    depends = {'_llm': 'llm.LLMService', '_env': 'env.local', '_skill': 'skills.SkillService', ...}
    # load_a2_config 解析后直接 setattr(svc, attr_name, dep_instance)
```

**方向 B — 增加 dep() 属性方法**：
```python
class AppService(BaseService):
    def dep(self, key: str) -> 'AppService':
        for d in self.depends:
            if isinstance(d, AppService) and (d.alias == key or f'{d.domain}.{d.alias}' == key): return d
        return None
```

**问题 B — 角色热更新**：`RoleDef` 修改后需 deactivate+activate 才能生效。方案为暴露 `UpdateRoleCommand`：

```python
class UpdateRoleCommand(BaseCommand):
    alias = 'UpdateRole'; role: str; updates: dict

    async def __call__(self):
        svc = app
        role_def = svc._roles.get(self.role)
        if not role_def: return {"success": False, "error": f"Role '{self.role}' not found"}
        hot_fields = {'system_prompt', 'tools', 'model', 'max_turns', 'skill_refs', 'use_planning', 'use_reflexion', 'can_spawn'}
        cold_fields = {'service_specs', 'memory_namespace', 'env'}
        cold_updates = {k for k in self.updates if k in cold_fields}
        if cold_updates: return {"success": False, "error": f"字段 {cold_updates} 需要 deactivate+activate"}
        for k, v in self.updates.items():
            if k in hot_fields: setattr(role_def, k, v)
        await svc.protocol.set(self.role, role_def.model_dump_json())
        svc._roles[self.role] = role_def
        log.info(f'角色热更新: {self.role} fields={list(self.updates.keys())}')
        return {"success": True, "updated": list(self.updates.keys())}
```

| 类型 | 字段 | 原因 |
|------|------|------|
| 热更新 | system_prompt, tools, model, max_turns, skill_refs, use_planning, use_reflexion, can_spawn | 不影响 ContextService 实例结构 |
| 冷更新 | service_specs, memory_namespace, env | 需要重建 ContextService 或切换 owned memory DB |

**暴露方式**：v1 HTTP `router_mapping`（`POST /api/roles/{role}/update`），v2 Exchange 事件触发。

**状态**：待实施，depends 改造需修改 bollydog 框架。

---

### C5. ContextService per-role 动态子类的 _apps 生命周期管理

**现状**：`activate_role` 用 `type()` 创建动态子类，`AppService.__init__` 自动注册 `_apps`，`deactivate_role` 的 `stop()` 不清理 `_apps`。

**方案 A（推荐） — AppService.on_stop 中自动注销**：

```python
class AppService(BaseService, abstract=True):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        key = f'{self.domain}.{self.alias}'
        AppService._apps[key] = self

    async def on_stop(self) -> None:
        key = f'{self.domain}.{self.alias}'
        AppService._apps.pop(key, None)  # 对称注销
        await super().on_stop()
```

**方案 B — deactivate_role 中显式清理**：

```python
async def deactivate_role(self, role_name: str):
    ctx = self._active_contexts.pop(ns, None)
    if ctx:
        await self.remove_dependency(ctx)
        AppService._apps.pop(f'{ctx.domain}.{ctx.alias}', None)
        for owned in ctx._owned_services:
            AppService._apps.pop(f'{owned.domain}.{owned.alias}', None)
```

推荐方案 A，修改 bollydog 框架。`__init__` 注册 ↔ `on_stop` 注销，符合 mode.Service 生命周期模型。

---

### C6. build_messages 中 history 的范围（来源：A0-t1 Q2）

**问题**：传入 `build_messages` 的 `history` 是本次 ReActStepCommand 的工具调用历史（短期），还是整个 AgentCommand 会话的历史（长期）？两者 token 成本差异巨大。

**分析**：
- **短期 history**（ReActStep 内）：只包含当前任务的 tool_call + observation 循环。每次 ReActStep 结束后 history 清空。L4 负责跨任务记忆。
- **长期 history**（AgentCommand 内）：包含多轮对话，AgentCommand 维护一个会话级 history 列表，传给每次 ReActStep。

**建议**：AgentCommand 维护会话级 `conversation_history`，每次 ReActStep 的 tool_call/observation 追加到 `conversation_history`。`build_messages` 接收的是会话级历史 + L4 归档。ReActStep 内不再独立维护 `history`。

```python
# AgentCommand.__call__
conversation_history = []
for task in task_list.pending():
    result = yield ReActStepCommand(task=task, history=conversation_history, ...)
    # ReActStepCommand 内部将 tool_call/observation 追加到 conversation_history
```

**状态**：待在 `09-mod-agent.md` 和 t1 追踪中明确。

---

### C7. _active_contexts 的生命周期策略（来源：A0-t1 Q1）

**问题**：何时从 `_active_contexts` 移除一个 role 的 ContextService？

**方案**：
- v1：**不主动销毁**（进程生命周期内保持），`deactivate_role()` 仅在显式调用时执行。理由：ContextService 持有 DB 连接和内存缓存，重建成本高。
- v2：LRU 策略，基于最近访问时间淘汰冷角色。

与 C5 关联：无论哪种策略，销毁时需要正确清理 `_apps`（依赖 C5 方案）。

---

## 二、待研究问题

### Q1. Tool 执行的统一权限与 Approval 机制

**思考方向 — Approval 中断模型**：

```
ReActStepCommand 解析 tool_call
  → 权限检查 → action='approve'
  → yield ApprovalRequestCommand(tool_name, params)  ← 中断 agent 循环
  → Hub dispatch:
    ├── CLI: input() 等待确认
    ├── HTTP: SSE {type:'approval_required'} → POST /api/approval/{trace_id} 回调
    └── Webhook: 推送审批请求 → 轮询回调
  → feedback → 继续执行或跳过
```

**要点**：权限检查在 ReActStepCommand 中统一拦截三类工具；MCP 工具复用 `PermissionConfig`（`mcp__*` 通配符）；approval 超时 60s 等同 rejected。

**待研究**：state 管理方式、批量 approval、"记住选择"机制。

### Q3. Memory Flush 触发策略

**待研究**：flush 由 ContextService 直接触发 vs Exchange 事件；flush 是否需要 LLM；debounce 机制。

### Q4. Artifact 系统集成

**待研究**：`present_files` serve 机制；metadata 存储位置；HTML 安全隔离。

### Q5. 环境无关工具的归属

**待研究**：是否抽 WebService；`RoleDef.tools` 是否控制权限；非交互模式降级。

### Q6. 多 Agent 跨 Env 通信

**待研究**：bollydog HTTP router 跨进程 dispatch；RemoteAgentProtocol；状态回传。

### Q7. Skill 结晶触发机制

**思考方向**：不自动触发，改为三种方式：

1. **显式调用**：`CrystallizeSkillCommand` 暴露 HTTP 接口（v1）
2. **周期审计**：`Service.timer(3600)` 扫描候选会话（v2）
3. **事件触发**：Exchange 订阅 `task.completed`（v2）

v1 只暴露 Command 接口，AgentCommand 中不自动触发。
