# A2 重设计 — 场景、领域、行为、服务职责（P0–P3）

> Issue: `20260826-a2-redesign`
> 目标：在 bollydog 之上重建 A2 智能体框架，能力对齐 agentscope 开源框架，并足以支撑 dataagent2 落地。
> 约束：**所有基础概念与范式必须是 bollydog 既有概念**（`BaseCommand` / `BaseEvent` / `BaseDomain` / `AppService` / `Protocol` / `HubService` / `Exchange` / `Session` / `Queue` / `RegistryService` / `routers` / `subscribe` / `depends` / `StreamState`）。无法映射的项记录在 `notes.md` 的「偏离与妥协」章节。

---

## Phase 0 场景叙述

规则遵守：不出现任何类名、不出现任何技术名词。每条一句话。

### 核心场景

| 编号 | 场景 |
|------|------|
| S01 | 用户问一句话，系统直接给出答案。 |
| S02 | 用户让系统看一个文件，系统打开文件读到内容后据此回答。 |
| S03 | 用户提出一个多步骤的复杂请求，系统先列出待办清单，逐项完成并在每项完成后更新清单，最后汇总汇报。 |
| S04 | 用户的问题需要同时查三个地方，系统三处同时查，全部返回后合并成一个答案。 |
| S05 | 系统正在回答时用户改主意喊停，系统立刻停下，已完成的部分保留，未完成的部分标记为中断。 |
| S06 | 系统准备做一件有破坏性的事（删数据、发消息），先把要做的事讲清楚请用户确认，用户点同意后才执行。 |
| S07 | 系统发现缺少关键信息无法继续，向用户提一个具体问题，用户回答后从中断处继续原任务。 |
| S08 | 对话进行了很久，早期内容已经超出模型能一次看完的量，系统把早期内容压缩成摘要，保留近期原文，然后继续对话。 |
| S09 | 用户关掉页面，第二天回来打开同一个对话，系统记得之前聊过什么并继续。 |
| S10 | 用户上传一批文档，之后提问时系统引用这些文档里的原文作答，并标明出处。 |
| S11 | 用户随口提过一次自己的偏好，几天后在一个全新对话里，系统仍然按这个偏好回答。 |
| S12 | 管理员接入了一个外部工具服务，系统随即获得该服务提供的全部工具并可直接使用。 |
| S13 | 用户为某类任务写了一份操作手册，之后遇到同类任务时系统先读手册再按手册的步骤执行。 |
| S14 | 主智能体判断某段工作应该交给专职智能体，把任务交出去，拿回结果后继续自己的工作。 |
| S15 | 几个智能体围绕同一个话题协作，每个人的发言其他人都能看到，最后收敛出一个结论。 |
| S16 | 系统写了一段代码，在一个隔离的环境里运行它，把运行结果和产出的文件带回来。 |
| S17 | 某个工具返回了几十万字的结果，系统只把摘要放进对话，完整内容单独存档，之后需要时能按关键词从存档里检索。 |
| S18 | 用户要求答案必须是固定字段的表单形式，系统按要求的字段逐项填好返回。 |
| S19 | 首选的模型服务返回失败，系统自动换用备用模型重试，用户无感知地拿到答案。 |
| S20 | 运维要复盘某一次回答，能逐步看到系统当时想了什么、调了哪些工具、每步花了多久、在哪一步出错。 |
| S21 | 用户设定每天早上八点自动生成一份报表，到点系统自动执行整个流程并把结果通知用户。 |
| S22 | 系统需要访问一个要登录的外部系统，自动取用为该用户单独保管的登录凭证，用户全程不用重新输入。 |
| S23 | 可用工具有几百个，全列出来会挤占大量篇幅，系统平时只展示一小组常用工具，遇到相关任务时再把对应的一组工具打开。 |
| S24 | 用户在网页上看到答案一个字一个字地出现，工具调用和执行结果也随过程实时显示。 |

### 异常场景

| 编号 | 场景 |
|------|------|
| S25 | 模型服务不可用，系统告诉用户当前无法回答，并把现场保留下来，稍后可以原地重试。 |
| S26 | 同一个工具连续失败三次，系统停止重试，把失败原因讲清楚并请用户介入。 |
| S27 | 用户的请求触碰了安全策略，系统拒绝执行并说明拒绝的原因，不做任何实际操作。 |
| S28 | 一次回答用掉的额度超过上限，系统在超限那一刻停止，把已产出的部分交付并说明原因。 |
| S29 | 系统重启，正在进行的对话丢失了运行态，用户重新打开对话时看到上次进行到哪一步，可以从那一步继续。 |

**覆盖自检**：S01–S24 覆盖了「问答 / 工具 / 规划 / 并行 / 中断 / 人机确认 / 追问 / 压缩 / 续会话 / 检索增强 / 长期记忆 / 外部工具接入 / 技能 / 子智能体 / 多智能体 / 沙箱执行 / 大结果归档 / 结构化输出 / 容错 / 可观测 / 定时 / 凭证 / 工具分组 / 流式」；S25–S29 覆盖「外部失败 / 重试熔断 / 安全拒绝 / 预算超限 / 崩溃恢复」。

---

## Phase 1 领域边界

### 1.1 从场景动词到领域

对 S01–S29 中所有动词做归组：

```
回答/推理/循环/交出去/收回来/打断/续跑      → 智能体回合编排
调模型/换模型重试/算token/取向量/合成语音    → 模型调用
装配消息/裁剪/压缩/摘要/注入提示             → 上下文装配
记住偏好/跨会话召回/写笔记                   → 记忆
列工具/筛工具/鉴权/执行工具/流式返回          → 工具
接入外部工具服务/发现其工具                  → MCP 接入
读手册/按手册执行/披露手册正文                → 技能
切分文档/向量化/检索原文/标出处               → 知识库
列清单/更新清单/汇报进度                     → 计划
存对话/恢复对话/存回合快照/停放待确认状态      → 会话
运行代码/读写文件/隔离环境/存档大结果          → 工作区
取用户凭证/注入到外部请求                    → 凭证
多智能体同话题协作/广播发言                  → 团队
记录每一步/统计耗时/回放过程                 → 可观测
到点自动执行                                → 定时（入口层）
网页上逐字出现                              → HTTP/SSE（入口层，bollydog 已提供）
```

### 1.2 领域划分表

| domain | 主体（AppService） | 一句话职责 | 场景来源 |
|--------|-------------------|-----------|---------|
| `agent` | `AgentService` | 驱动一个具名智能体完成一个回合的「推理—行动—观察」循环并输出事件流 | S01–S07, S14, S18, S19, S23, S25–S29 |
| `model` | `ChatModelService` | 把统一格式的消息与工具描述送给某个大模型并返回统一格式的响应 | S01, S08, S19, S25 |
| `model` | `EmbeddingService` | 把文本转成向量 | S10, S11 |
| `context` | `ContextService` | 按 token 预算装配、裁剪并压缩送入模型的消息序列 | S08, S17, S23, S28 |
| `memory` | `MemoryService` | 读写跨会话的长期记忆条目 | S11 |
| `tool` | `ToolkitService` | 列出、筛选、鉴权并执行工具调用 | S02, S06, S16, S23, S26, S27 |
| `mcp` | `McpService` | 连接外部 MCP 服务并把其工具登记成本系统的可调用命令 | S12 |
| `skill` | `SkillService` | 发现技能手册并做三级渐进披露 | S13 |
| `knowledge` | `KnowledgeService` | 文档切分、向量化入库与相似检索 | S10 |
| `plan` | `PlanService` | 维护任务清单及其状态流转 | S03 |
| `session` | `SessionService` | 持久化会话记录、回合快照与待恢复的停放状态 | S05, S07, S09, S29 |
| `workspace` | `WorkspaceService` | 提供受控的文件读写与命令执行沙箱，并归档大产物 | S02, S16, S17 |
| `credential` | `CredentialService` | 按用户保管外部系统凭证并在调用时注入 | S22 |
| `team` | `TeamService` | 编排多个智能体的顺序、并行与广播协作 | S15 |
| `observe` | `TraceService` | 采集全链路事件、落库并支持回放 | S20, S24 |

### 1.3 入口层（沿用 bollydog，不属于业务领域）

| 组件 | 来源 | 用途 |
|------|------|------|
| `HttpService` | bollydog `entrypoint/http` | REST + SSE（`routers` 里标 `SSE` 即走 `SseHandler`） |
| `SocketService` | bollydog `entrypoint/websocket` | WebSocket |
| `UdsService` | bollydog `entrypoint/uds` | 本机进程间调用 |
| `CLI` | bollydog `entrypoint/cli` | `ls` / `execute` / `service` / `shell` |
| `SchedulerService` | A2 新增入口 | 定时把命令投进 Hub（S21）。放在 `a2/entrypoint/scheduler/`，与 HTTP/WS 同为「入口」而非业务服务，因此允许主动 dispatch |

### 1.4 边界检查

- 每个动词都有归属，无孤儿行为。
- 跨领域不允许直接调对方方法：`agent` 需要模型能力时派发 `model.*.Generate` 命令，而不是拿 `ChatModelService` 实例调方法。唯一例外是**已声明 `depends` 的只读查询**（如 `agent` 读 `skill.hub` 的技能目录元数据），此时走 `self.get_dependency('skill.hub')`，依赖显式、生命周期受管、可审计。
- `context` 与 `memory` 分开：前者管「本回合送进模型的那串消息」，落在 `Session`；后者管「跨会话的知识性记忆」，落在向量/KV 存储。两者读写模式与 Protocol 完全不同。
- `knowledge`（用户文档 RAG）与 `memory`（智能体自身记忆）分开：前者是外部语料的检索，后者是对话中提炼的事实。

---

## Phase 2 行为设计

### 2.1 Command 与 Event 的判定

| | Command | Event |
|---|---|---|
| 语气 | 祈使（Reply / Invoke / Compress） | 过去时（ReplyStarted / ToolInvoked） |
| 接收方 | 唯一（destination 定位） | 全部订阅者 |
| 失败 | 大声失败，调用方感知 | 各订阅者独立失败，不影响主链路 |
| bollydog | `BaseCommand` + `hub.dispatch` / `yield` | `BaseEvent` + `Exchange` 广播 |

**A2 的取舍原则**：主链路上「必须拿到结果才能往下走」的一律是 Command；「只是告诉别人发生了什么」的一律是 Event。可观测、长期记忆抽取、会话落盘、指标统计全部走 Event 订阅，从主链路上摘除，主链路失败不受它们影响，它们失败也不阻塞回答。

### 2.2 destination 命名

三段式 `domain.ServiceAlias.CommandAlias`，`ServiceAlias` 在 TOML 用 `alias` 指定短名：

| 服务 | key |
|------|-----|
| `AgentService`（可多实例） | `agent.assistant` / `agent.researcher` / … |
| `ChatModelService`（可多实例） | `model.chat` / `model.chat_small` |
| `EmbeddingService` | `model.embed` |
| `ContextService` | `context.default` |
| `MemoryService` | `memory.longterm` |
| `ToolkitService` | `tool.toolkit` |
| `McpService` | `mcp.gateway` |
| `SkillService` | `skill.hub` |
| `KnowledgeService` | `knowledge.base` |
| `PlanService` | `plan.notebook` |
| `SessionService` | `session.store` |
| `WorkspaceService`（可多实例） | `workspace.local` / `workspace.docker` |
| `CredentialService` | `credential.vault` |
| `TeamService` | `team.room` |
| `TraceService` | `observe.tracer` |

示例 destination：`agent.assistant.Reply`、`tool.toolkit.Invoke`、`model.chat.Generate`。

**服务引用一律配置化**：`AgentService` 的 `model_ref = 'model.chat'`、`context_ref = 'context.default'` 等都是 TOML 里的字符串。Command 内部用 `registry.resolve(f'{app.model_ref}.Generate')(**kw)` 构造子命令。这样换模型服务、换工作区、给不同智能体配不同工具集，全部只改 TOML。

### 2.3 各主体行为映射表

#### agent.{alias} — `AgentService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `Reply` | **异步生成器**。一个完整回合：装配上下文 → 推理 → 工具 → 观察 → 收敛。逐块 yield 事件字典 |
| Command | `Resume` | 从停放态（等待确认/等待回答/等待外部执行）继续同一回合 |
| Command | `Interrupt` | 写入中断标记，令正在跑的 `Reply` 在下一个安全点收敛 |
| Command | `Observe` | 把外部消息写进该智能体的上下文但不触发回复（多智能体协作用） |
| Command | `Spawn` | 委派一个子智能体执行子任务并取回结论 |
| Event | `ReplyStarted` | 回合开始 |
| Event | `IterationCompleted` | 一轮推理+行动完成 |
| Event | `ReplyFinished` | 回合结束（含 `finished_reason`） |
| Event | `ReplyInterrupted` | 回合被中断 |
| Event | `ReplyParked` | 回合停放，等待人类输入 |
| 订阅 | `team.room.MessageBroadcast` → `on_broadcast` | 团队广播时把别人的发言纳入自己的上下文 |

#### model.{alias} — `ChatModelService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `Generate` | **异步生成器**。一次模型调用，逐块 yield 增量（thinking / text / tool_call） |
| Command | `CountTokens` | 估算一段消息的 token 数 |
| Event | `ModelCalled` | 一次模型调用完成（含用量、时延、finish_reason） |
| Event | `ModelFailed` | 模型调用最终失败（已穷尽重试与备用模型） |

#### model.embed — `EmbeddingService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `Embed` | 批量文本转向量 |

#### context.default — `ContextService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `Assemble` | 按预算装配本轮送模型的消息序列（系统提示 + 摘要 + 技能提示 + 检索片段 + 历史 + 运行时提示） |
| Command | `Compress` | 触发压缩：滚动窗口裁剪 + 摘要生成 |
| Event | `ContextCompacted` | 压缩完成（压缩前后 token 数、被折叠的消息数） |

#### memory.longterm — `MemoryService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `Recall` | 按查询召回长期记忆条目 |
| Command | `Remember` | 写入一条长期记忆 |
| Command | `Forget` | 删除长期记忆条目 |
| 订阅 | `agent.*.ReplyFinished` → `on_reply_finished` | 回合结束后异步抽取值得长期保留的事实 |

#### tool.toolkit — `ToolkitService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `ListTools` | 返回当前激活分组的工具 JSON Schema 列表 |
| Command | `Invoke` | **异步生成器**。执行一次工具调用，逐块 yield 工具输出 |
| Command | `ActivateGroup` | 打开/关闭工具分组（对应 agentscope 的 meta tool） |
| Command | `CheckPermission` | 对一次工具调用做策略判定，返回 allow / deny / ask |
| Event | `ToolInvoked` | 工具执行成功 |
| Event | `ToolFailed` | 工具执行失败 |
| Event | `GroupActivated` | 工具分组状态变更 |

#### mcp.gateway — `McpService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `ConnectServer` | 连接一个 MCP 服务并把它的工具登记进注册表 |
| Command | `DisconnectServer` | 断开并注销其工具 |
| Command | `ListServers` | 列出已连接的 MCP 服务与健康状态 |
| Event | `ServerConnected` | MCP 服务已连接、工具已登记 |
| Event | `ServerLost` | MCP 服务断连 |

#### skill.hub — `SkillService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `ListSkills` | 一级披露：技能目录（名称 + 一句话说明） |
| Command | `MatchSkills` | 二级披露：按当前查询匹配相关技能 |
| Command | `LoadSkill` | 三级披露：读取技能手册正文与附带资源 |
| Command | `InstallSkill` | 从目录/压缩包/远端导入一个技能 |
| Event | `SkillActivated` | 某技能在本回合被激活 |
| Event | `SkillCatalogChanged` | 技能目录发生变更 |

#### knowledge.base — `KnowledgeService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `IngestDocument` | **异步生成器**。解析 → 切块 → 向量化 → 入库，逐块 yield 进度 |
| Command | `Search` | 相似检索，返回带出处的片段 |
| Command | `DeleteDocument` | 删除文档及其全部切块 |
| Event | `DocumentIngested` | 文档入库完成 |

#### plan.notebook — `PlanService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `CreatePlan` | 建立任务清单 |
| Command | `UpdateTask` | 更新某个任务的状态与备注 |
| Command | `ListTasks` | 读取当前清单 |
| Event | `PlanCreated` | 清单建立 |
| Event | `TaskUpdated` | 任务状态变更 |
| Event | `PlanCompleted` | 全部任务完成 |

#### session.store — `SessionService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `OpenSession` | 创建或打开一个会话，返回其状态快照 |
| Command | `SaveTurn` | 落盘一个回合（输入、输出、事件序列） |
| Command | `LoadSession` | 读取会话历史 |
| Command | `ListSessions` | 列出某用户的会话 |
| Command | `DeleteSession` | 删除会话 |
| Command | `Park` | 停放一个待人工介入的回合状态 |
| Command | `Unpark` | 取出并清除停放状态 |
| Command | `ReplayEvents` | **异步生成器**。从落盘的事件序列重放（断线续传） |
| 订阅 | `agent.*.ReplyFinished` → `on_reply_finished` | 自动落盘回合 |
| 订阅 | `agent.*.ReplyParked` → `on_reply_parked` | 自动停放 |

#### workspace.{alias} — `WorkspaceService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `RunCommand` | **异步生成器**。在沙箱里执行一条命令，逐块 yield 输出 |
| Command | `ReadPath` / `WritePath` / `ListPath` | 沙箱内文件读写与列举 |
| Command | `StoreArtifact` | 归档一个大产物，返回引用 |
| Command | `GrepArtifact` | 在归档产物里按关键词检索 |
| Event | `ArtifactStored` | 产物归档完成 |

#### credential.vault — `CredentialService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `PutCredential` | 保存某用户对某外部系统的凭证 |
| Command | `GetCredential` | 取出凭证（返回脱敏视图，注入由服务方法完成） |
| Command | `ListCredentials` | 列出某用户已配置的凭证 |
| Event | `CredentialMissing` | 调用外部系统时发现缺凭证 |

#### team.room — `TeamService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `Sequential` | **异步生成器**。按顺序让一串智能体依次发言，前者输出作为后者输入 |
| Command | `Fanout` | 让一组智能体并行处理同一输入，合并结果 |
| Command | `Broadcast` | 把一条消息广播给同一话题内的全部智能体 |
| Event | `MessageBroadcast` | 广播已发出（被各 `AgentService` 订阅） |
| Event | `RoundCompleted` | 一轮团队协作完成 |

#### observe.tracer — `TraceService`

| 类别 | 名称 | 说明 |
|------|------|------|
| Command | `QueryTrace` | 按 trace_id 查询整条调用链 |
| Command | `ExportTrace` | 导出为可回放/可评测的用例 |
| 订阅 | `#` | 订阅全部主题，落盘 span |

### 2.4 事件拓扑总览

```
                     ┌──────────────────────────────┐
 agent.*.ReplyStarted│                              │
 agent.*.ReplyFinished ─┬──→ observe.tracer   (落 span)
 agent.*.ReplyParked │  ├──→ session.store    (落盘 / 停放)
 agent.*.ReplyInterrupted│├──→ memory.longterm (抽取事实)
 model.*.ModelCalled │  │
 model.*.ModelFailed ├──┤
 tool.*.ToolInvoked  │  │
 tool.*.ToolFailed   │  │
 context.*.ContextCompacted│
 plan.*.TaskUpdated  │  │
 skill.*.SkillActivated  │
 knowledge.*.DocumentIngested
 mcp.*.ServerConnected┘  └──→ tool.toolkit    (刷新工具目录)
 team.room.MessageBroadcast ──→ agent.*       (纳入上下文)
```

订阅声明写在 TOML：

```toml
["observe.tracer"]
module = "a2.observe.service.TraceService"
commands = ["commands"]
subscribe = { "#" = "OnAny" }

["session.store"]
module = "a2.session.service.SessionService"
commands = ["commands"]
subscribe = { "agent.*.ReplyFinished" = "OnReplyFinished", "agent.*.ReplyParked" = "OnReplyParked" }

["memory.longterm"]
module = "a2.memory.service.MemoryService"
commands = ["commands"]
subscribe = { "agent.*.ReplyFinished" = "OnReplyFinished" }

["tool.toolkit"]
module = "a2.tool.service.ToolkitService"
commands = ["commands"]
subscribe = { "mcp.*.ServerConnected" = "OnServerConnected", "mcp.*.ServerLost" = "OnServerLost" }
```

### 2.5 检查项

- 场景中的每一次交互都能在上表找到对应 Command 或 Event。
- 每个 Command 有唯一接收方（destination 三段定位）。
- 每个 Event 的发出时机明确：**在对应 Command 成功返回之后**。
- 跨领域交互只经 `hub.dispatch` / `yield` / Exchange，无直接方法调用（`depends` 只读查询除外，已在 1.4 声明）。

---

## Phase 3 服务职责

### 3.1 通用规则

- **AppService = 资源持有者**：持有 Protocol、持有配置、暴露业务方法给 Command 调用，自身不主动 dispatch（入口层服务除外）。
- **Command = 薄编排层**：调 `app.xxx()` + `protocol.get/set` + `yield 子命令`，自身不持有状态。
- **Protocol = 环境抽象**：生产用真实实现，测试换成内存实现，代码零改动。

### 3.2 各服务声明

#### `AgentService`（domain=`agent`）

| 项 | 内容 |
|----|------|
| 持有状态 | 智能体名、系统提示、模型引用、工具分组白名单、ReAct 上限、注入策略 |
| Protocol | `CacheLayer` → `SQLiteProtocol`（存该智能体的画像与跨回合小状态）。测试换 `MemoryProtocol` |
| depends | `context.default`、`tool.toolkit`、`session.store`（只读查询用） |
| 业务方法 | `system_prompt()`、`tool_groups()`、`max_iters`、`next_action(state)`、`should_compress(usage)`、`build_hint()` |
| 生命周期 | `on_start` 载入画像；`on_started` 校验 `model_ref` / `context_ref` 指向的服务存在 |
| 多实例 | 是。每个智能体一个 TOML 段，`alias` 即智能体名 |

#### `ChatModelService`（domain=`model`）

| 项 | 内容 |
|----|------|
| 持有状态 | 主模型、备用模型、重试次数、上下文窗口、采样参数、formatter 选择 |
| Protocol | `ChatModelProtocol` 抽象；实现：`OpenAIChatProtocol` / `AnthropicChatProtocol` / `DashScopeChatProtocol` / `GeminiChatProtocol` / `OllamaChatProtocol` / `LiteLLMChatProtocol`；测试用 `ScriptedChatProtocol`（按脚本吐固定响应） |
| depends | `credential.vault` |
| 业务方法 | `format(messages, mode)`（纯计算，选 chat / multi-agent formatter）、`context_size`、`fallbacks()`、`count_tokens(messages)` |
| 生命周期 | `on_start` 建 HTTP 客户端；`on_stop` 关闭连接 |
| 多实例 | 是。大模型 / 小模型 / 视觉模型各一段 |

#### `EmbeddingService`（domain=`model`）

| 项 | 内容 |
|----|------|
| Protocol | `EmbeddingProtocol` 实现 + 外层 `CacheLayer`（向量缓存，命中率高）→ `SQLiteProtocol` |
| 业务方法 | `batch_size`、`dimension` |

#### `ContextService`（domain=`context`）

| 项 | 内容 |
|----|------|
| 持有状态 | token 预算、触发压缩比例、保留比例、工具结果截断长度、图片数上限、压缩提示词 |
| Protocol | 无自有存储；上下文本体存在 bollydog 全局 `Session`（其 Protocol 在 TOML 配成 `CacheLayer`→`SQLiteProtocol`） |
| depends | `skill.hub`、`plan.notebook`（读目录/清单做提示注入） |
| 业务方法 | `assemble(parts) -> list[dict]`（纯计算）、`need_compress(used, limit) -> bool`、`select_window(msgs, budget)`、`render_hint(state)` |
| 说明 | 该服务是**纯计算 + Session 读写**，是全框架最容易单测的部分 |

#### `MemoryService`（domain=`memory`）

| 项 | 内容 |
|----|------|
| Protocol | `VectorStoreProtocol`（`QdrantProtocol` / `MilvusProtocol` / `InMemoryVectorProtocol`）；轻量部署可退化成 `CacheLayer`→`SQLiteProtocol` + BM25 |
| depends | `model.embed` |
| 业务方法 | `extract_facts(turn) -> list[dict]`、`score(entry, query)` |
| 订阅 | `agent.*.ReplyFinished` |

#### `ToolkitService`（domain=`tool`）

| 项 | 内容 |
|----|------|
| 持有状态 | 分组定义（`{group: [destination…]}`）、常驻分组、权限规则、结果截断阈值 |
| Protocol | `PermissionProtocol`（规则匹配 + 决策）外层包 `CacheLayer` 存每会话已激活分组 |
| depends | `workspace.local`、`mcp.gateway`、`credential.vault` |
| 业务方法 | `schemas(groups) -> list[dict]`（从 `registry.all_commands()` 过滤 + `BaseCommand.describe()` 推导）、`resolve_tool(name) -> str`（工具名 → destination）、`decide(name, args, ctx) -> str`、`is_concurrency_safe(name) -> bool`、`truncate(result) -> tuple[dict, bool]` |
| 生命周期 | `on_started` 建立「工具名 ↔ destination」索引；订阅 MCP 事件后增量刷新 |
| 关键点 | **工具就是 Command**。工具的入参 = Command 的字段，工具描述 = Command 的 docstring，工具 JSON Schema 由 Pydantic 自动导出。不存在独立的 Tool 抽象 |

#### `McpService`（domain=`mcp`）

| 项 | 内容 |
|----|------|
| Protocol | `McpProtocol` 抽象；实现 `StdioMcpProtocol` / `HttpMcpProtocol`；测试用 `FakeMcpProtocol` |
| 业务方法 | `register_tools(server, tools)` —— 为每个远端工具用 `type()` 动态生成一个 `BaseCommand` 子类并调用 `registry.add_command()`；`unregister_tools(server)` 通过 `registry.all_commands()` 移除对应 destination |
| 生命周期 | `on_started` 连接 TOML 里声明的服务；`on_stop` 全部断开 |

#### `SkillService`（domain=`skill`）

| 项 | 内容 |
|----|------|
| Protocol | `CacheLayer`（技能元数据）→ `SQLiteProtocol`；正文走 `LocalFileProtocol` |
| 业务方法 | `catalog() -> list[dict]`、`match(query, top_k) -> list[str]`、`body(name) -> str`、`validate(manifest) -> dict` |
| 说明 | 元数据入库、正文留在文件系统。三级披露分别对应 `ListSkills` / `MatchSkills` / `LoadSkill` |

#### `KnowledgeService`（domain=`knowledge`）

| 项 | 内容 |
|----|------|
| Protocol | `VectorStoreProtocol`；文档原件走 `LocalFileProtocol` |
| depends | `model.embed` |
| 业务方法 | `parse(path) -> list[dict]`（按扩展名选解析器）、`chunk(sections, size, overlap) -> list[dict]`、`rerank(hits, query)` |

#### `PlanService`（domain=`plan`）

| 项 | 内容 |
|----|------|
| Protocol | `CacheLayer` → `SQLiteProtocol`（按 `session_id` 分键） |
| 业务方法 | `render(tasks) -> str`（生成注入模型的清单文本）、`next_pending(tasks) -> dict \| None`、`progress(tasks) -> dict` |

#### `SessionService`（domain=`session`）

| 项 | 内容 |
|----|------|
| Protocol | `CacheLayer` → `SQLiteProtocol`（单机）/ `RedisProtocol`（多副本）。会话记录、回合快照、停放状态、事件序列四类键前缀隔离 |
| 业务方法 | `snapshot(session_id) -> dict`、`turn_key(session_id, turn)`、`park_key(session_id)`、`event_key(session_id, turn)` |
| 订阅 | `agent.*.ReplyFinished`、`agent.*.ReplyParked` |
| 说明 | 与 bollydog 全局 `Session` 的分工：全局 `Session` 是**回合内**的运行态便签（键为 `session_id`，回合结束即可丢弃）；`SessionService` 是**跨回合**的持久档案。前者快、后者全 |

#### `WorkspaceService`（domain=`workspace`）

| 项 | 内容 |
|----|------|
| Protocol | `SandboxProtocol` 抽象；实现 `LocalSandboxProtocol` / `DockerSandboxProtocol` / `K8sSandboxProtocol` / `SshSandboxProtocol`；测试用 `FakeSandboxProtocol`。产物归档另挂 `LocalFileProtocol` |
| 业务方法 | `resolve(path) -> str`（越界检查）、`policy(cmd) -> bool`（命令白名单）、`artifact_ref(key) -> str` |
| 多实例 | 是。本地开发用 `workspace.local`，生产用 `workspace.docker` |

#### `CredentialService`（domain=`credential`）

| 项 | 内容 |
|----|------|
| Protocol | `CacheLayer` → `SQLiteProtocol`，值经对称加密后存储 |
| 业务方法 | `inject(headers, cred) -> dict`、`mask(cred) -> dict`、`schema(system) -> dict` |

#### `TeamService`（domain=`team`）

| 项 | 内容 |
|----|------|
| Protocol | `MemoryProtocol`（话题成员表，进程内即可）；需要跨进程时换 `RedisProtocol` |
| 业务方法 | `members(topic) -> list[str]`、`join(topic, agent)`、`leave(topic, agent)` |

#### `TraceService`（domain=`observe`）

| 项 | 内容 |
|----|------|
| Protocol | `SqlAlchemyProtocol`（生产，便于聚合查询）/ `SQLiteProtocol`（单机）/ `MemoryProtocol`（测试） |
| 订阅 | `#` |
| 业务方法 | `to_span(message) -> dict`（从 `trace_id` / `span_id` / `parent_span_id` / `created_time` 组装）、`tree(trace_id) -> list[dict]` |
| 说明 | bollydog 的 `BaseCommand` 天生带 OpenTelemetry 三元组，`TraceService` 只做落盘与聚合，无需额外埋点 |

### 3.3 服务依赖图

```
                      ┌─────────────────┐
                      │  agent.assistant│
                      └────┬───────┬────┘
             depends       │       │     dispatch
        ┌──────────────────┘       └────────────────────┐
        ▼                                               ▼
 ┌─────────────┐  ┌────────────┐  ┌───────────┐  ┌────────────┐
 │context.defau│  │tool.toolkit│  │model.chat │  │plan.notebook│
 └──┬───────┬──┘  └──┬──┬───┬──┘  └─────┬─────┘  └────────────┘
    │       │        │  │   │           │
    ▼       ▼        ▼  │   ▼           ▼
┌────────┐┌──────┐┌──────┐│┌────────┐┌──────────────┐
│skill.hub││plan  ││worksp││ │mcp.gw  ││credential.va │
└────────┘└──────┘└──────┘│└────────┘└──────────────┘
                          └──────────────┘
 事件侧（无编译期依赖，只有主题约定）：
 observe.tracer ← #
 session.store  ← agent.*.Reply{Finished,Parked}
 memory.longterm← agent.*.ReplyFinished
 tool.toolkit   ← mcp.*.Server{Connected,Lost}
 agent.*        ← team.room.MessageBroadcast
```

依赖边只有 12 条且无环；事件边不构成启动期依赖，因此 `depends` 图保持极简，服务可以任意子集部署（`bollydog service --config` 只加载需要的段）。

### 3.4 Protocol 组合决策

| 服务 | 读写特征 | 选型 | 可测性 |
|------|---------|------|--------|
| `agent` | 少量画像，读多写少 | `CacheLayer` → `SQLiteProtocol` | 换 `MemoryProtocol` |
| `model` | 无持久化，只有连接 | `ChatModelProtocol`（连接型） | 换 `ScriptedChatProtocol` |
| `context` | 无自有存储 | 用全局 `Session` | `Session` 的 Protocol 换 `MemoryProtocol` |
| `memory` | 向量近邻检索 | `VectorStoreProtocol` | 换 `InMemoryVectorProtocol` |
| `tool` | 规则匹配 + 每会话小状态 | `CacheLayer` → `MemoryProtocol`（内含 `PermissionProtocol` 规则表） | 天生内存 |
| `mcp` | 长连接 | `McpProtocol` | 换 `FakeMcpProtocol` |
| `skill` | 元数据读多写少 + 正文大文本 | `CacheLayer`→`SQLiteProtocol` + `LocalFileProtocol` | 换 `MemoryProtocol` + 临时目录 |
| `knowledge` | 批量写入 + 近邻检索 | `VectorStoreProtocol` | 换 `InMemoryVectorProtocol` |
| `plan` | 高频小更新 | `CacheLayer` → `SQLiteProtocol` | 换 `MemoryProtocol` |
| `session` | 高频读写 + 需持久 | `CacheLayer` → `SQLiteProtocol` / `RedisProtocol` | 换 `MemoryProtocol` |
| `workspace` | 进程/容器执行 + 大文件 | `SandboxProtocol` + `LocalFileProtocol` | 换 `FakeSandboxProtocol` |
| `credential` | 少量加密值 | `CacheLayer` → `SQLiteProtocol` | 换 `MemoryProtocol` |
| `team` | 进程内成员表 | `MemoryProtocol` / `RedisProtocol` | 天生内存 |
| `observe` | 大量追加写 + 聚合查询 | `SqlAlchemyProtocol` | 换 `MemoryProtocol` |

全部满足「生产用真实实现、测试换内存实现、代码零改动」。

### 3.5 生命周期钩子

| 钩子 | A2 用法 |
|------|---------|
| `on_init_dependencies` | 不用（依赖由 `Bootstrap` 从 TOML `depends` 解析后 `add_dependency`） |
| `on_first_start` | `ToolkitService` 注册 `hub.before` 权限守卫与 `hub.after` 结果截断钩子（全局只注册一次） |
| `on_start` | Protocol 建连；`AgentService` 载入画像；`SkillService` 扫描技能目录 |
| `on_started` | `ToolkitService` 建工具索引；`McpService` 连接远端并动态注册工具命令；`AgentService` 校验引用完整性 |
| `on_stop` | Protocol 关闭；`McpService` 断开全部远端；`CacheLayer` 自动 flush |
