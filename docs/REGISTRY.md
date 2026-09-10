# A2 Registry — 当前状态快照

> Issue: `20260826-a2-redesign`
> 本文是 A2 框架的唯一现状快照。历史变更通过 git commit trailer 追溯。

## 已知限制

| 编号 | 限制 | 规避方式 |
|------|------|---------|
| L1 | 并行工具批次无实时流 | 需要实时时改用串行批 |
| L2 | 中断粒度为安全点 | 工具自身实现 timeout |
| L3 | 动态注册命令无 HTTP 路由 | 经 tool.toolkit.Invoke 转发 |
| L4 | hub.before/after 全局生效 | 钩子首行做 destination 前缀判定 |
| L5 | 跨副本事件不可见 | 单进程 + 会话粘性路由 + Redis 共享状态 |
| L6 | 无通用中间件插件点 | Event 订阅 + 工具分组两个官方扩展位 |
| L7 | 停放态需定时清扫 | SchedulerService 定时清扫（P8） |
| L8 | 子命令流不会自动冒泡 | A2 `relay_gen` 显式中继 |

## Services

| domain | alias | 类 | depends | protocol |
|--------|-------|-----|---------|----------|
| agent | assistant | AgentService | context.default, tool.toolkit, session.store | — |
| model | chat | ChatModelService | — | ScriptedChatProtocol / LiteLLM |
| model | embed | EmbeddingService | — | HashEmbeddingProtocol |
| context | default | ContextService | — | bollydog Session |
| memory | longterm | MemoryService | model.embed | MemoryProtocol |
| tool | toolkit | ToolkitService | workspace.local, mcp.gateway, credential.vault | RulePermissionProtocol |
| mcp | gateway | McpService | — | MemoryProtocol (stub) |
| skill | hub | SkillService | — | MemoryProtocol |
| knowledge | base | KnowledgeService | model.embed | InMemoryVectorProtocol |
| plan | notebook | PlanService | — | MemoryProtocol |
| session | store | SessionService | — | MemoryProtocol |
| workspace | local | WorkspaceService | — | LocalSandboxProtocol |
| credential | vault | CredentialService | — | MemoryProtocol |
| team | room | TeamService | — | MemoryProtocol |
| observe | tracer | TraceService | — | MemoryProtocol |
| schedule | runner | SchedulerService | — | MemoryProtocol |
| entry | HttpEntryService | HttpEntryService | — | — |

## Commands（核心）

| destination | 类型 | 说明 |
|-------------|------|------|
| agent.assistant.Reply | gen | ReAct 主循环 |
| agent.assistant.Resume | gen | 停放恢复 |
| agent.assistant.Interrupt | cmd | 协作式中断 |
| agent.assistant.Spawn | gen | 子智能体委派 |
| model.chat.Generate | gen | 模型调用（流式） |
| model.embed.Embed | cmd | 向量嵌入 |
| context.default.Assemble | cmd | 上下文装配 |
| context.default.Compress | cmd | 上下文压缩 |
| tool.toolkit.ListTools | cmd | 工具 schema 列表 |
| tool.toolkit.Invoke | gen | 工具执行 |
| session.store.OpenSession | cmd | 打开会话 |
| session.store.Park / Unpark | cmd | 停放/恢复 |
| workspace.local.ReadPath | cmd | 读文件（工具） |
| workspace.local.RunCommand | gen | 沙箱执行 |

完整清单见 `docs/issues/20260826-a2-redesign/interfaces.md` §3。

## Events & Subscribers

| topic | 订阅 Event |
|-------|------------|
| agent.*.ReplyFinished | session.store.OnReplyFinished, memory.longterm.OnReplyFinished, observe.tracer.OnAny |
| agent.*.ReplyParked | session.store.OnReplyParked, observe.tracer.OnAny |
| tool.*.ToolInvoked | observe.tracer.OnAny |
| mcp.*.ServerConnected | tool.toolkit.OnServerConnected, observe.tracer.OnAny |
| team.*.MessageBroadcast | agent.*.OnMessageBroadcast |
| # | observe.tracer.OnAny |

订阅遵循 bollydog 当前模型：`subscribe` 将 Event 类绑定到 topic；Event 在
`__call__` 中处理 `self.data['events'][-1]` 携带的来源消息。

## Protocol ABCs

| ABC | 测试实现 | 生产实现 |
|-----|---------|---------|
| ChatModelProtocol | ScriptedChatProtocol | LiteLLM (P8) |
| EmbeddingProtocol | HashEmbeddingProtocol | OpenAI/Ollama (P8) |
| VectorStoreProtocol | InMemoryVectorProtocol | Qdrant/PgVector (P8) |
| SandboxProtocol | LocalSandboxProtocol | Docker/K8s (P8) |
| PermissionProtocol | RulePermissionProtocol | RulePermissionProtocol |
| McpProtocol | MemoryProtocol stub | Stdio/Http (P8) |
