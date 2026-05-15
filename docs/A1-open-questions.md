# 问题附录

> 收集所有已识别但尚未完全解决的设计问题、待研究事项和已解决的决策备忘。

---

## 一、已解决的设计问题

| # | 问题 | 方案 | 文档位置 |
|---|------|------|---------|
| P1 | PlannerService tasks 数据模型 | 开关模式：`persist_tasks=True`（默认）落 `{role.base_dir}/tasks.db`，`=False` 用 session 缓存 | [06-mod-planner.md](06-mod-planner.md) |
| P2 | TOML vs config.py 划分 | `config.py` SERVICE_DEFAULT 承载全量默认，`agent.toml` 纯模块配置覆盖 | [10-mod-config.md](10-mod-config.md) |
| P3 | Tool/Env 绑定方向 | **反转**：EnvService 拥有 ToolCommand，移除 ToolService | [03-mod-env.md](03-mod-env.md) |
| P4 | 多模型管理 | 单一 LLMService + litellm.Router 管多模型，RoleDef.model 选模型 | [02-mod-llm.md](02-mod-llm.md) |
| P5 | 多角色记忆隔离 | 角色级物理目录隔离，无 namespace 前缀 | [01-data-models.md](01-data-models.md) §八 |
| P6 | AgentRegistryService 独立性 | 合并进 AgentService，ContextService 为唯一 per-role 动态子类 | [09-mod-agent.md](09-mod-agent.md) |

---

## 二、待研究问题

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

## 三、设计决策索引

| # | 主题 | 位置 | 摘要 |
|---|------|------|------|
| D1 | Tool.required_model 工具降级 | [03-mod-env.md](03-mod-env.md) | v1 策略：移除不兼容 Tool，不切换模型 |
| D2 | System Prompt 管理 | [08-mod-context.md](08-mod-context.md) | `RoleDef.system_prompt` 是完整提示词，由角色定义管理 |
| D3 | Orchestrator 决策 | [06-mod-planner.md](06-mod-planner.md) | LLM 决策 + `get_available_description()` 注入 |
| D4 | 技能隔离 | [05-mod-skills.md](05-mod-skills.md) | 全局 Hub + `RoleDef.skill_refs` 引用过滤 |
| D5 | Prompt 模板变量 | [08-mod-context.md](08-mod-context.md) | `_build_vars()` + `_render_prompt()` + `defaultdict` |
| D6 | Session 角色绑定 | [04-mod-memory.md](04-mod-memory.md) §三 | namespace = `{role}/{trace_id}`；activate 与 session 解耦 |
