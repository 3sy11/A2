# 问题附录

> 收集所有已识别但尚未完全解决的设计问题、待研究事项和已解决的决策备忘。

---

## 一、已解决的设计问题

| # | 问题 | 方案 | 文档位置 |
|---|------|------|---------|
| P1 | PlannerService tasks 数据模型 | 开关模式：`persist_tasks=True`（默认）落 `{role.base_dir}/tasks.db`，`=False` 用 session 缓存 | [06-mod-planner.md](06-mod-planner.md) |
| P2 | TOML vs config.py 划分 | 每个模块独立 `config.py` 承载默认，`agent.toml` 纯模块配置覆盖 | [10-mod-config.md](10-mod-config.md) |
| P3 | Tool/Env 绑定方向 | **反转**：EnvService 拥有 ToolCommand，移除 ToolService | [03-mod-env.md](03-mod-env.md) |
| P4 | 多模型管理 | 单一 LLMService + litellm.Router 管多模型，RoleDef.model 选模型 | [02-mod-llm.md](02-mod-llm.md) |
| P5 | 多角色记忆隔离 | 角色级物理目录隔离，无 namespace 前缀 | [01-data-models.md](01-data-models.md) §九 |
| P6 | AgentRegistryService 独立性 | 合并进 AgentService，ContextService 为唯一 per-role 动态子类 | [09-mod-agent.md](09-mod-agent.md) |
| P7 | SkillService 存储方案 | 元数据存 SQLiteProtocol(skills 表)，正文/资源/脚本通过文件路径引用 | [01-data-models.md](01-data-models.md) §四、[05-mod-skills.md](05-mod-skills.md) |
| P8 | 数据模型表间关系 | ER 图 + 模块×表归属矩阵 | [01-data-models.md](01-data-models.md) §八 |

---

## 二、设计审查发现的问题

> 来源：对全部文档交叉审查 + 与 bollydog 框架约定的对比。

### C1. config.py 应按模块独立，非集中式

**现状**：`10-mod-config.md` 中 `config.py` 将所有服务默认配置集中在一个 `SERVICE_DEFAULT` 字典中。

**问题**：各模块（LLM、Env、Skills、Planner、MCP）的默认配置应该在各自模块的 `config.py` 中管理，`a2/config.py` 只负责聚合和 TOML 覆盖。

**建议方案**：
```
a2/llm/config.py       → LLM_DEFAULT
a2/env/config.py        → ENV_DEFAULT
a2/skills/config.py     → SKILLS_DEFAULT
a2/planner/config.py    → PLANNER_DEFAULT
a2/mcp/config.py        → MCP_DEFAULT
a2/context/config.py    → CONTEXT_DEFAULT + ROLE_DEFAULT
a2/agent/config.py      → AGENT_DEFAULT
a2/config.py            → build_config() 聚合各模块 + TOML 覆盖 + ${VAR} 替换
```

**状态**：待更新 [10-mod-config.md](10-mod-config.md)

### C2. SkillService save_skill 的文件写入逻辑

**现状**：Skill 改为 DB + 文件引用后，`save_skill` 需要同时写 DB 记录和创建文件目录。

**待明确**：
- 结晶时由 `CrystallizeSkillCommand` 先写文件（`body_path` 指向），再调 `save_skill` 写 DB？还是 `save_skill` 内部统一处理？
- 文件路径规范：`{skill_name}/SKILL.md` 是否允许嵌套子目录？
- `load_skill(name)` 从 DB 取 `body_path` 后读文件，文件不存在时降级策略？

### C3. SkillService 与 CacheLayer 的兼容性

**现状**：SkillService Protocol 改为 CacheLayer → SQLiteProtocol，但 Skill 模型包含 `resource_paths`、`script_paths` 等结构化字段。

**问题**：SQLiteProtocol 的 KV 模式将整个 Skill 序列化为 JSON 存入 `value` 列。这意味着无法对 `tags`、`trigger_keywords` 做 SQL 级别查询。

**待决策**：
- v1 接受 KV 模式（全量加载到内存，内存中做关键词匹配）— 简单可行
- v2 可迁移到 CRUDProtocol 做列级查询 — 但需要新的 Protocol 实现

### C4. on_started 中 depends 绑定方式

**现状**：`09-mod-agent.md` 中 AgentService.on_started 使用 `match dep.alias` 模式绑定依赖。

**问题**：bollydog `load_from_config` 解析 `depends` 后，`svc.depends` 从字符串列表变为实例列表。但 `dep.alias` 是服务的 alias 属性（如 `'LLMService'`、`'local'`），需要确认：
- `_load_commands` 后 `depends` 确实是实例列表而非字符串？
- `dep.alias` 在 `_derive()` 后是否保持原值？
- 多个 env（如 `local`、`docker`）同时存在时 match 是否正确？

**建议**：写单元测试验证 `on_started` 中 `self.depends` 的实际类型和内容。

### C5. ContextService per-role 动态子类的 _apps 注册

**现状**：`activate_role` 用 `type()` 动态创建 ContextService 子类，但这些动态实例是否自动注册到 `AppService._apps`？

**问题**：
- `AppService.__init__` 会将 `{domain}.{alias}` 写入 `_apps`。动态子类 alias 如 `'default_ContextService'` 会注册为 `context.default_ContextService`。
- 但 AgentService 通过 `_active_contexts[ns]` 管理这些实例，不需要 `_apps` 查找。
- 多角色时 `_apps` 中会堆积大量 ContextService 条目，deactivate 后是否需要从 `_apps` 中移除？

**建议**：`deactivate_role` 中增加 `AppService._apps.pop(key)` 清理。

---

## 三、待研究问题

### Q1. MCP 工具与 EnvService 工具的统一权限

**背景**：EnvService 的 `PermissionConfig` 管理 env 工具权限，MCP 工具目前缺少统一的权限检查入口。

**待研究**：
- 是否需要在 ReActStepCommand 级别做统一权限拦截？
- MCP 工具的 `require_approval` 策略如何配置？
- 是否复用 `PermissionConfig` 还是 MCP 独立权限？

### Q2. ContextService 热更新

**背景**：`RoleDef` 修改后（如调整 system_prompt、tools），当前需要 `deactivate_role` + `activate_role` 才能生效。

**待研究**：
- 是否支持运行中 RoleDef 字段的部分热更新？
- 哪些字段可以热更新（system_prompt、tools、model）vs 哪些需要重建（service_specs、memory_namespace）？
- 更新是否需要广播通知正在运行的 AgentCommand？

### Q3. Memory Flush 触发策略

**背景**：执行 FullLLMCompact 压缩前，需要将即将被丢弃的对话内容 flush 到 L1 (insights) 和 L2 (facts)，防止信息丢失。参考 DeerFlow MemoryMiddleware。

**待研究**：
- flush 由 ContextService.compact_if_needed 直接触发，还是通过 Exchange 事件通知？
- flush 需要 LLM 参与吗（提取 insights）？如果是，token 开销估计？
- 是否需要 debounce 机制（DeerFlow 使用 500ms debounce）？

### Q4. Artifact 系统集成

**背景**：DeerFlow 的 Artifact 系统支持 agent 产出物的浏览器预览（HTML/图表/Markdown）。参考 [A2-feature-reference.md](A2-feature-reference.md) §一。

**待研究**：
- A2 中 `present_files` 工具的 serve 机制（静态文件 vs Starlette 路由）？
- Artifact metadata 存储（session 级 vs 角色级 vs 全局）？
- 安全：如何隔离 agent 生成的 HTML（iframe sandbox / CSP）？

### Q5. 环境无关工具的归属

**背景**：`web_search`、`web_fetch`、`ask_user`、`present_files` 不依赖 env protocol，当前设计放在 AgentService 上。

**待研究**：
- 是否需要抽出独立的 `WebService` 来承载 HTTP 类工具？
- 这些工具的权限是否应该受 `RoleDef.tools` 控制？
- `ask_user` 在非交互模式（HTTP/Webhook）下如何降级？

### Q6. 多 Agent 跨 Env 通信

**背景**：v1 使用 `SpawnAgentCommand` 同进程并行，v2 需要跨进程。当前设计通过 bollydog HTTP router 绑定 Command 实现。

**待研究**：
- bollydog 的 HTTP router 是否原生支持跨进程 Command dispatch？
- 是否需要独立的 RemoteAgentProtocol？
- 子 agent 的状态回传机制（SSE streaming / polling / callback）？

### Q7. Skill 结晶的 LLM 成本控制

**背景**：`CrystallizeSkillCommand` 在每次 `tool_calls ≥ 3` 的成功任务后触发 LLM 提取模式。

**待研究**：
- 是否需要频率限制（如 cooldown 期）？
- 是否可以先用规则匹配判断"是否值得结晶"再调 LLM？
- 结晶 LLM 调用是否应该用更便宜的模型（而非 role_def.model）？

---

## 四、设计决策索引

| # | 主题 | 位置 | 摘要 |
|---|------|------|------|
| D1 | Tool.required_model 工具降级 | [03-mod-env.md](03-mod-env.md) | v1 策略：移除不兼容 Tool，不切换模型 |
| D2 | System Prompt 管理 | [08-mod-context.md](08-mod-context.md) | `RoleDef.system_prompt` 是完整提示词，由角色定义管理 |
| D3 | Orchestrator 决策 | [06-mod-planner.md](06-mod-planner.md) | LLM 决策 + `get_available_description()` 注入 |
| D4 | 技能隔离 | [05-mod-skills.md](05-mod-skills.md) | 全局 Hub + `RoleDef.skill_refs` 引用过滤 |
| D5 | Prompt 模板变量 | [08-mod-context.md](08-mod-context.md) | `_build_vars()` + `_render_prompt()` + `defaultdict` |
| D6 | Session 角色绑定 | [04-mod-memory.md](04-mod-memory.md) §三 | namespace = `{role}/{trace_id}`；activate 与 session 解耦 |
| D7 | Skill 存储双层方案 | [01-data-models.md](01-data-models.md) §四 | DB 索引元数据 + 文件系统存正文/资源/脚本 |
| D8 | 配置模块化 | A1 §C1 | 各模块独立 config.py 管默认，a2/config.py 聚合 + TOML 覆盖 |
