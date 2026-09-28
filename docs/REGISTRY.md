# A2 当前框架设计与实现现状

> 基线日期：2026-09-28
>
> A2 commit：P0 基线 `21cc06a`，P1-A（§4.4）为其后的工作区改动
>
> bollydog：本地源码 `/Users/mii/Workspace/bollydog`，version `0.1.5`，commit `63312c9`
>
> 本文是当前实现的唯一现状基线。`docs/issues/20260826-a2-redesign/`
> 描述设计目标和历史决策，不等于当前已交付能力。

## 1. 结论

A2 当前是一个可运行的 **Walking Skeleton**，而不是 29 个目标场景的
完整实现。已打通的主链路是：

```text
HTTP/SSE 或 CLI
  -> agent.assistant.Reply
  -> context.default.Assemble
  -> tool.toolkit.ListTools
  -> model.chat.Generate
  -> 可选 tool.toolkit.Invoke
  -> reply.finished
```

代码已经为计划、知识、记忆、技能、多智能体、MCP、凭证、调度和可观测建立了
Service/Command 骨架，但大多数还没有接入 `Reply` 主链路，也没有场景级测试。
“有类和命令”只表示接口骨架存在，不表示场景已交付。

当前默认配置仅适合开发和测试：模型是 `ScriptedChatProtocol`，业务状态基本都在
`MemoryProtocol`，命令执行是本机 shell，凭证使用开发级实现。

## 2. 框架目标

目标来自 [stories.md](issues/20260826-a2-redesign/stories.md)：在 bollydog 原语之上提供一个
能力对齐 AgentScope、能支撑 dataagent2 的事件驱动智能体框架。目标包含 29 个场景：

- 主链路：问答、工具、规划、并行、中断、人机确认、追问、压缩和流式输出。
- 知识与持续性：会话恢复、RAG、长期记忆、技能和大结果归档。
- 扩展与协作：MCP、子智能体、多智能体、沙箱、工具分组、凭证和调度。
- 运行保障：模型回退、可观测、失败保留、工具熔断、安全拒绝、预算上限和崩溃恢复。

## 3. bollydog 0.1.5 的实际能力边界

以 bollydog 源码为准，A2 可依赖的原语如下。

| 能力 | 源码事实 | 对 A2 的含义 |
|---|---|---|
| Command | `BaseCommand` 是 Pydantic 模型，普通 Command 持有 `Future`，异步生成器 Command 持有 `StreamState` | 工具、编排和入口都可统一为 Command |
| 子命令 | 生成器 `yield command` 只回传最终 state；`yield [commands]` 并行等待全部 state | 子命令的中间流不会自动冒泡，A2 仍需 `relay_gen` |
| StreamState | 保存所有 yield 值；单个值时 `result()` 返回该值，多个值时返回列表 | 并行执行流式工具时，A2 不能假定 result 总是单个 `tool.result` |
| 取消 | `HubService.cancel(iid)` 可取消排队或运行中 Command，运行中会调用 `on_cancel()` | 旧文档中“bollydog 无取消 API”已过时；A2 仍可保留回合级协作式中断 |
| Event | Event 类由 `Exchange` 按 topic 索引，`hub.emit` 实例化并 fire-and-forget dispatch | Event 订阅是进程内的，不是跨进程消息总线 |
| 钩子 | `before`/`after` 是 runner 全局列表，非实例级洋葱中间件 | 需要在钩子内快速判断 destination/作用域 |
| 注册 | Bootstrap 按 Service 的 commands **模块**扫描所有具体 Command/Event；Registry 支持运行时 `add_command()` | 同一模块被多个 Service 共用时，模块中的全部命令会被每个 Service 各注册一次 |
| HTTP | HTTP 在 `on_start` 时遍历 Registry 建立路由；SSE 直接迭代 `StreamState` | 运行时新增的 MCP Command 不会自动获得 HTTP 路由 |
| Session | `Session` 只是 KV 门面，是否持久化完全由配置的 Protocol 决定 | bollydog 默认为 `MemoryProtocol`，不会自动持久化 |
| Queue | 排队、运行中任务和最近历史全部在单进程内存 | 进程重启时无法仅依靠 bollydog Queue 恢复进行中命令 |
| 适配器 | 已有 Memory/Redis/SQLite KV、CacheLayer、SQLAlchemy、File 和 Graph 等实现 | A2 可通过 TOML 换成持久化后端，但当前配置并未这样做 |

源码入口：
[bootstrap.py](../../bollydog/bollydog/bootstrap.py)、
[runner.py](../../bollydog/bollydog/service/runner.py)、
[app.py](../../bollydog/bollydog/service/app.py)、
[state.py](../../bollydog/bollydog/models/state.py)、
[exchange.py](../../bollydog/bollydog/service/exchange.py)、
[memory.py](../../bollydog/bollydog/adapters/memory.py)。

## 4. 当前 A2 运行时基线

### 4.1 启动快照

使用 `config/agent.toml` 构造 Bootstrap，当前得到：

- 23 个 Service，含 7 个 bollydog 框架 Service 和 16 个 A2 Service。
- 71 个已注册 Command destination。
- 37 个 Event topic，共 37 个 Event 绑定。
- A2 声明了 `agent.assistant.Reply` 和 `entry.http.Chat` 两条 SSE 路由；只有设置
  `ENTRYPOINT_HTTP_ENABLED=1` 时，bollydog 的 `HttpService` 才会加载并实际暴露它们。

Chat 和 Embedding 已分别扫描 `a2.model.chat_commands` 与
`a2.model.embedding_commands`。因此 `model.chat` 只注册 Generate/CountTokens，
`model.embed` 只注册 Embed，不再产生调用到错误 Protocol 的命令。

### 4.2 Service 现状

| Service | 当前后端 | 当前状态 |
|---|---|---|
| `agent.assistant` | 无自有 Protocol | ReAct 骨架可运行；已接入 plan/skill/knowledge/memory（见 §4.4）；未接入 credential/team |
| `model.chat` | `ScriptedChatProtocol` | 只是确定性测试模型，无生产 LLM |
| `model.embed` | `HashEmbeddingProtocol` | 伪向量，适合测试 |
| `context.default` | bollydog Session -> `SQLiteProtocol` | 用户消息、助手消息和工具结果可跨重启恢复；`Assemble` 已支持 RAG/记忆/技能/计划四类注入槽位 |
| `tool.toolkit` | `RulePermissionProtocol` | 列表、分组、鉴权、执行可用；默认规则为空，大结果只截断不归档；分组可由模型经 `ActivateToolGroup` 运行时打开 |
| `session.store` | `SQLiteProtocol` | 保存会话、已结束回合、等待用户处理的回合和 SSE 事件；支持重启后读取 |
| `workspace.local` | `LocalSandboxProtocol` | 文件与本地 shell 可用；不是容器或进程隔离沙箱 |
| `plan.notebook` | `SQLiteProtocol` | CRUD 数据可跨重启保存；已接入 Agent（`plan` 工具组 + 每轮清单注入） |
| `skill.hub` | 直接文件系统 | 可扫描/匹配/读取；一级目录注入主链路，`LoadSkill` 发 `SkillActivated`；InstallSkill 仍是 stub |
| `memory.longterm` | `SQLiteProtocol` | 关键词提取与词集打分骨架；按 `user_id` 跨会话召回并注入，已跨重启持久化；非向量召回 |
| `knowledge.base` | `InMemoryVectorProtocol` | 文本分块/伪向量检索；已接入 `Assemble` 注入带出处片段；向量库本身不持久化 |
| `mcp.gateway` | 错配为 `MemoryProtocol` | `ConnectServer` 会因缺少 `list_tools()` 直接失败，尚不是可用 stub |
| `credential.vault` | `SQLiteProtocol` | CRUD 数据可跨重启保存；仍未注入 model/tool，当前 XOR 仅适合开发环境 |
| `team.room` | `MemoryProtocol` | 顺序/并行/广播 Command 存在；只有一个默认 agent，无完整协作配置 |
| `observe.tracer` | `SQLiteProtocol` | Event 转 span 可跨重启保存；无聚合和完整回放保证 |
| `schedule.runner` | `MemoryProtocol` | 间隔轮询与 dispatch 骨架；无 cron/通知/失败隔离/停放态清理 |
| `entry.http` | 路由声明服务（不是 HTTP server） | `Chat` 会 OpenSession 后中继 Reply，并透传 `user_id`；实际 HTTP server 由 bollydog 环境开关控制；不会加载持久化历史 |

### 4.3 当前 Reply 主链路做了什么

1. 打开会话（带上调用方的 `user_id`）；若服务刚重启且内存上下文为空，从已保存回合恢复消息。
2. 将本次用户消息写入上下文，再采集可选上下文来源并装配模型输入。
3. 每条发送给前端的事件先写入 `session.store`，再输出到 SSE。
4. 每次开始调用模型或工具时，记录当前回合执行位置。
5. 普通结束、中断和模型失败时保存已完成回合；危险工具确认和追问时保存等待用户处理的信息。
6. 用户后续批准、拒绝或回答后，`Resume` 使用同一 turn_id 继续；服务在工具执行中重启时不会自动重复执行该工具。

上下文压缩仍是字符串截取，尚未调用模型生成摘要。

### 4.4 上下文注入（P1-A）

`Reply` 每轮迭代前调用 `AgentService.collect_sources()` 采集四类来源，
以纯数据传给 `context.default.Assemble`，由 `ContextService` 渲染成 system 消息。
采集与渲染分离，是为了让 `ContextService` 不依赖 knowledge/memory/skill 服务。

| 来源 | 开关（默认 `off`） | 采集命令 | 渲染位置 |
|---|---|---|---|
| 技能目录 | `skill_mode = 'catalog'` | `skill.hub.ListSkills`（一级披露，只取 name + description） | summary 之后 |
| 长期记忆 | `memory_mode = 'recall_write'` | `memory.longterm.Recall`（有 `user_id` 用 user 作用域，否则回落到 session） | 技能之后 |
| 任务清单 | `plan_mode = 'tool'` | `plan.notebook.ListTasks` 的 `rendered` 字段 | 记忆之后 |
| 检索片段 | `rag_mode = 'on'` | `knowledge.base.Search`，命中项归一化出 `source` 以便标注出处 | 清单之后 |

采集有硬上限（`max_skills` / `memory_top_k` / `rag_top_k`，每条片段按 500 字符截断），
避免注入本身把 token 推过压缩阈值。任一来源失败只降级为空，不会让整个回合失败。

技能正文（三级披露）与计划清单的创建/更新都由模型主动调用工具完成，分别落在
`skill` 与 `plan` 工具组；`Reply` 按 session 键 `tool_groups:{session_id}` 决定暴露哪些分组，
模型可用 `agent.assistant.ActivateToolGroup` 打开其他分组（S23）。

## 5. 29 个目标场景的实现状态

状态定义：

- **已验收**：有场景级测试且通过。
- **部分**：关键组件存在，但语义不完整、未接入主链路或未验收。
- **未实现**：关键能力缺失，或当前命令会直接失败。

| 场景 | 状态 | 当前实现 | 与目标的主要差距 |
|---|---|---|---|
| S01 直接回答 | 已验收 | Reply -> Assemble -> Scripted Generate -> finished | 需生产模型和真实 provider 验收 |
| S02 读文件回答 | 部分 | ReadPath 单命令测试通过，Reply 有 Invoke 骨架 | 没有验证“模型决策 -> 工具 -> 根据结果回答”的 E2E |
| S03 规划驱动 | 部分 | `plan` 工具组（CreatePlan/UpdateTask/ListTasks）+ 每轮注入渲染后的清单 | 由模型自行决定建清单，无多步请求自动识别；清单变化经 `tool.result` 呈现，无独立 `plan.updated` 客户端事件 |
| S04 并行工具 | 未验收 | 工具服务保留并行分批规则 | P0 为了让每个工具调用都在独立保存点完成，Reply 当前按顺序执行；恢复并行执行与对应测试属于后续工作 |
| S05 中断 | 部分 | 中断请求会保留 turn_id、已完成回合和事件 | 仍只在模型/工具边界生效；未使用硬取消 |
| S06 危险操作确认 | 已验收 | 保存待执行工具，输出确认请求；批准后只执行该工具一次 | 未覆盖复杂并行工具批次 |
| S07 追问后续跑 | 已验收 | AskHuman 保存问题、选项和上下文，Resume 写入用户回答后继续 | 未覆盖多轮追问 |
| S08 上下文压缩 | 部分 | 阈值判断、滚动窗口、字符串摘要 | 未调用模型生成摘要；用户历史没有完整写入 context |
| S09 跨日续会话 | 已验收 | SQLite 保存回合；新进程可 LoadSession 并恢复上下文 | 仅验证单机 SQLite；多副本尚未实现 |
| S10 文档 RAG 与引用 | 部分 | `Assemble` 注入 `knowledge.base.Search` 的命中片段并标出 `[source]` 出处 | 向量是 `HashEmbeddingProtocol` 伪向量且库不持久化，只验证了接线，检索质量未验收 |
| S11 跨会话长期记忆 | 部分 | 按 `user_id` 召回并注入；写入同样按 user 作用域，落 SQLite 可跨重启 | 打分为关键词词集交集，非向量召回；只有 `prefer`/`remember` 这类显式表述会被抽取 |
| S12 动态 MCP | 未实现 | 动态 Command 造类逻辑存在 | Protocol 错配 MemoryProtocol，ConnectServer 实测报缺少 `list_tools` |
| S13 技能渐进披露 | 部分 | 一级目录注入 system 消息；`LoadSkill` 作为工具按需拉正文并发 `SkillActivated` | 无 `skill.activated` 客户端 chunk（只走旁路 Event）；`InstallSkill` 仍是 stub |
| S14 子智能体 | 未实现 | Spawn 可中继另一 Agent Reply | 只配置一个 agent；Spawn 未暴露给默认工具组；未实现 inherit_context/depth |
| S15 多智能体协作 | 部分 | Sequential/Fanout/Broadcast/Topic 命令存在 | 只有一个 Agent 实例，无收敛策略和场景验收 |
| S16 隔离运行代码 | 部分 | 本地 cwd 限制 + subprocess shell | 不是真实隔离沙箱，传入完整宿主环境，命令策略仅字符串黑名单 |
| S17 大结果归档 | 未实现 | truncate/Store/Read/GrepArtifact 命令存在 | truncate 丢弃截断后全文，从不调用 StoreArtifact；artifact 路径未复用 workspace 越界校验 |
| S18 结构化输出 | 未实现 | Reply 有 `structured_schema` 字段 | 字段未使用，文档承诺的 GenerateStructuredOutput 不存在 |
| S19 模型重试/回退 | 部分 | 按 model list 尝试并退避 | `max_retries` 实际限制模型列表长度，不会对同一模型重试；默认无 fallback |
| S20 追踪与回放 | 部分 | `# -> OnAny` 写入 SQLite | span 字段和聚合有限；无场景测试 |
| S21 定时报表 | 部分 | task + interval polling + dispatch | 不支持每日 08:00/cron，无通知、失败隔离和多实例租约 |
| S22 自动凭证 | 未实现 | Credential CRUD 写入 SQLite | model/tool 从不取用凭证；默认密钥 + XOR 不符合安全保管目标 |
| S23 工具分组 | 部分 | basic/edit/exec/mcp/skill/plan/knowledge 配置 + `ActivateToolGroup` 元工具；`Reply` 按 session 键取分组 | 没有任务匹配后自动激活；`always_on_groups` 是 toolkit 全局的，无 per-agent 白名单 |
| S24 SSE 实时流 | 部分 | 每条前端事件持久化，可用 `ReplayEvents(last_seq)` 读取断线后事件 | 并行工具无实时流 |
| S25 模型失败保留现场 | 部分 | 模型失败保存 retry 状态，用户可 Resume | 无完整场景测试和生产模型验证 |
| S26 工具三次失败熔断 | 未实现 | 单次异常转 `tool.result:error` | 无失败计数、重试、熔断或请求用户介入 |
| S27 安全拒绝 | 部分 | Permission deny 路径存在 | 默认 rules 为空即全放行；shell 只做弱黑名单；未验收无副作用 |
| S28 预算超限停止 | 未实现 | 统计模型 usage | 无预算字段、before 守卫或超限终止与部分交付 |
| S29 崩溃恢复 | 部分 | 保存模型/工具执行位置和事件；模型调用可重试，工具结果不确定时不自动重放 | 无完整场景测试；多副本未实现 |

## 6. 文档决策的当前裁定

| 决策 | 当前裁定 | 说明 |
|---|---|---|
| D-A 工具就是 Command | 保留 | 与 bollydog 实现一致；Toolkit 只负责发现、分组、鉴权和统一输出 |
| D-B 服务引用配置化 | 保留 | TOML `domain.alias` + `registry.resolve` 是 bollydog 原生路径 |
| D-C 主链路 Command，旁路 Event | 保留但需持久化兜底 | Event 是 fire-and-forget，重要会话/审计数据不能只依赖未持久的旁路 |
| D-D 两级会话状态 | 已部分落地 | bollydog Session 与 session.store 均使用直接 SQLite；为保证每次写入可恢复，P0 不使用延迟刷盘 CacheLayer |
| D-F 三种子命令姿势 | 保留 | `yield cmd`、`yield [cmds]`、`relay_gen` 均对应 bollydog 实际语义 |
| D-G 停放-恢复 | 已落地 | 停放时结束当前请求，保存问题或待确认工具；Resume 在后续请求继续同一回合 |
| D-H 单进程默认 | 保留 | Exchange/Queue/Hook 均为进程内机制；横向扩展需外置状态和会话粘性 |
| D-01 显式流中继 | 保留 | bollydog 子命令确实只回传最终 state |
| D-02 无法取消 | 废弃历史前提 | bollydog 0.1.5 已支持 `hub.cancel(iid)` 与 `on_cancel()` |
| D-03 跨请求 HITL | 保留问题和方案 | 不应持有长期 Future；当前 Park/Resume 实现不完整 |
| D-04 无实例级洋葱中间件 | 作为架构取舍保留 | Event 可观测/可重放，但旧文档的“Event 可跨进程”表述错误 |
| D-07 动态工具注册 | 部分保留 | `add_command()` 存在；Registry 没有 remove API，A2 目前通过可变 `all_commands()` dict 删除 |
| D-08 定时触发 | 方向保留，文档需以实现为准 | 实现是 `mode.Service.task` + sleep 轮询，不是旧文档记载的 `timer` |
| D-13 跨副本 Event | 明确未解决 | 当前只能通过单进程、粘性路由和外置状态规避 |

## 7. 与目标的优先级差距

### P0：会话回合生命周期（已完成）

1. bollydog Session、`session.store`、plan、credential、observe 已切换为 SQLite。
2. 用户消息、助手回答和工具结果写入上下文；服务重启时可从回合记录恢复。
3. Park/Resume 保存 turn_id、迭代次数、消息和待确认工具；停放时结束 SSE。
4. `AppendEvent` 已接入 Reply，SSE 事件可按序号重放。
5. Chat/Embedding 命令模块已拆分，交叉注册已消除。

### P1：把已有领域骨架接入 Agent

1. **已完成（Spawn 除外）**：Plan、Skill、Knowledge、Memory 与工具分组激活已接入
   `Reply/Assemble`，见 §4.4。`Spawn` 仍可中继子 agent，但未暴露为工具，也没有
   `inherit_context`/`depth` 语义。
2. 实现大结果 `truncate -> StoreArtifact -> summary + ref`。
3. 实现 `structured_schema` 的动态终止工具和校验。
4. 分离”同模型重试”与”跨模型 fallback”，并实现工具失败熔断和预算上限。
5. 设计并恢复安全工具的并行执行：每个并行工具都必须记录独立执行状态，并补齐断线和重启场景测试。
6. 为 S02–S29 增加场景级端到端测试，不再以 Command 存在代替场景验收。
   （P1-A 已为 S03/S10/S11/S13/S23 补上，其余场景仍缺。）

### P2：生产可用性

1. 实现 LiteLLM/目标 provider、生产 Embedding/VectorStore 和真实 MCP transport。
2. 将 LocalSandbox 换成可验证隔离的 Docker/K8s backend，建立结构化权限策略。
3. 将凭证改为可轮换密钥的标准 authenticated encryption/外部 secrets backend，并接入 model/tool。
4. 补齐持久化 trace、指标、调度租约、通知与运行维护。
5. 如需多副本，另行设计跨副本 Event/Trace 通道；bollydog Exchange 本身不提供该能力。

## 8. 验证基线

2026-09-28 实际执行：

```text
.venv/bin/pytest tests/ -q
27 passed

/Users/mii/Workspace/bollydog/.venv/bin/pytest \
  -c /Users/mii/Workspace/bollydog/pyproject.toml \
  -o addopts= -o cache_dir=/tmp/bollydog-pytest-cache \
  /Users/mii/Workspace/bollydog/tests -q
154 passed in 1.61s
```

A2 的 18 个 P0 测试覆盖：消息/块模型、S01、会话重启恢复、确认后执行、确认参数错误时保留
待处理操作、追问后继续、中断保存、事件重放、工具执行中重启的保守恢复、计划/凭证/追踪
持久化和模型命令注册隔离。

P1-A 新增 9 个测试（`tests/test_p1_domains.py`）覆盖：`user_id` 贯通到会话与
`ReplyFinished`、`Assemble` 四类注入槽位的渲染与空注入时行为不变、技能目录注入 +
`LoadSkill` 按需拉正文并发出 `SkillActivated`、文档入库后检索片段带出处注入、
长期记忆跨会话召回与跨重启持久化、计划创建与推进、工具分组按需打开。

它们仍不覆盖其余目标场景。

额外的运行时探测结果：

- `test_session_survives_rebuild` 已验证：同一 `session_id` 在重建 Bootstrap 后仍可读取已保存
  回合；SQLite 文件保存在进程工作目录下的 `.a2/state/`。
- `mcp.gateway.ConnectServer(server='probe')` 返回
  `AttributeError: 'MemoryProtocol' object has no attribute 'list_tools'`。

## 9. 文档与源码索引

- 目标场景与领域设计：[stories.md](issues/20260826-a2-redesign/stories.md)
- 设计时序：[sequence.md](issues/20260826-a2-redesign/sequence.md)
- 目标接口契约：[interfaces.md](issues/20260826-a2-redesign/interfaces.md)
- 历史决策与偏离：[notes.md](issues/20260826-a2-redesign/notes.md)
- Walking Skeleton 范围：[skeleton.md](issues/20260826-a2-redesign/skeleton.md)
- A2 默认运行配置：[agent.toml](../config/agent.toml)
- A2 主循环：[agent/commands.py](../a2/agent/commands.py)
- bollydog 源码：[bollydog](../../bollydog/bollydog)
