# 附件一 — 外部特性参考（Feature Reference）

> 来源于对 DeerFlow、LangGraph、CrewAI、OpenHands 等开源项目的研究，供 A2 设计参考。
> 本文档不是执行计划，是技术选型和特性参考库。

---

## F1：Artifact 产物系统

### 概念

Artifact 是 agent **主动"展示"给用户的产出物**，区别于工作中间文件。Agent 生成文件后调用 `present_files` 工具声明"这些文件是给用户看的"，客户端据此渲染预览。

### 来源：DeerFlow 2.0

**项目**：[bytedance/deer-flow](https://github.com/bytedance/deer-flow)（MIT，67.7K stars）

**完整流程**：

```
Agent 生成文件         Agent 调 present_files     ThreadState 更新          客户端渲染
write_file →          → 路径安全检查              → artifacts 列表          → 按 MIME 预览
/outputs/report.html  → 归一化虚拟路径            → SSE 推送前端            → HTML=Blob URL
                      → Command(update={          → merge_artifacts         → Markdown=渲染
                         artifacts: [...]})          reducer 去重            → 图片=inline
                                                                           → 其他=下载
```

**关键源码**：

| 文件 | 职责 |
|------|------|
| `tools/builtins/present_file_tool.py` | 专用 tool：验证路径在 outputs/ 下 → 归一化 → 更新 graph state |
| `agents/thread_state.py` | `ThreadState.artifacts: list[str]` + `merge_artifacts` reducer（有序去重） |
| `app/gateway/routers/artifacts.py` | HTTP 服务：`GET /api/threads/{id}/artifacts/{path}`，MIME 检测，HTML/SVG 强制下载（防 XSS） |
| `frontend/src/core/artifacts/` | 前端：URL 构建、Blob 隔离渲染、workspace 面板 |

**安全设计**：
- 路径必须在 `/mnt/user-data/outputs/` 下，否则拒绝
- `text/html`、`application/xhtml+xml`、`image/svg+xml` 强制 `Content-Disposition: attachment`，防止活跃内容在应用域执行
- `.skill` 档案（ZIP）从 ZIP 内提取文件，短缓存（5 分钟）

### 独立 Artifact 开源方案

| 项目 | 链接 | 特点 | 适合场景 |
|------|------|------|---------|
| **E2B Fragments** | [e2b-dev/fragments](https://github.com/e2b-dev/fragments) | Next.js 模板，E2B sandbox 执行 AI 生成的应用（Python/Next/Vue/Streamlit），多 LLM 支持 | 需要 sandbox 执行 + 预览 |
| **Open Artifacts** | [13point5/open-artifacts](https://github.com/13point5/open-artifacts) | Claude Artifacts 克隆，Next.js + Supabase，支持裁剪对话式编辑 | 简单 artifact 渲染 |
| **OpenArtifacts** | [mayfer/open-artifacts](https://github.com/mayfer/open-artifacts) | 单 HTML 文件，浏览器内 esbuild-wasm 编译 JSX，iframe sandbox，零后端 | 极简/嵌入式场景 |
| **Artifactuse SDK** | [artifactuse/sdk](https://github.com/artifactuse/sdk) | 轻量 JS 库，将 AI 内容转为交互式 artifact（代码/视频/画布/表单），框架无关（Vue/React/Svelte） | 前端组件级集成 |
| **agent-render** | [baanish/agent-render](https://github.com/baanish/agent-render) | 静态 artifact viewer，fragment 分享（URL hash），Markdown/代码/diff/CSV/JSON 渲染，可选 SQLite 持久化 | 纯静态预览，无 sandbox |
| **mdserve** | [jfernandez/mdserve](https://github.com/jfernandez/mdserve) | Rust 写的 Markdown 预览服务，WebSocket 热更新，Mermaid 支持，Claude Code 插件 | Markdown 预览场景 |
| **LangGraph GenUI** | [langchain-ai/langgraphjs-gen-ui-examples](https://github.com/langchain-ai/langgraphjs-gen-ui-examples) | `push_ui_message()` + `useArtifact` hook，React 组件动态加载 | LangGraph 生态专用 |
| **CopilotKit OpenGenerativeUI** | [CopilotKit/OpenGenerativeUI](https://github.com/CopilotKit/OpenGenerativeUI) | 交互式 AI 生成 UI（算法可视化/3D/图表），MCP server 集成 | 需要 rich UI 渲染 |

### A2 适配建议

**推荐方案**：参考 DeerFlow 的 `present_files` + HTTP serving 模式，因为它：
1. 是 tool-driven（agent 主动声明），不依赖前端框架
2. HTTP 服务文件，任何客户端都能消费
3. 映射到 bollydog 的 `Command + router_mapping` 完全自然

**实现要点**：
- 内置 Tool `present_files`：验证路径 → 写入 `session.artifacts`
- `ArtifactCommand`：`router_mapping` 映射到 `GET /api/artifacts/{path}`
- 文件存放：`{role.base_dir}/outputs/`
- SSE 事件：`yield {'type': 'artifacts', 'items': [...]}`

**前端选型（v2）**：
- 如果构建独立 Web UI → E2B Fragments 或 Open Artifacts 作为起点
- 如果只需预览 → agent-render（静态）或 Artifactuse SDK（组件级）
- 如果 CLI 场景 → mdserve（Markdown 热更新预览）

---

## F2：Middleware Chain（Agent 执行中间件链）

### 概念

Agent 中间件不是 HTTP 中间件，是在 **agent 执行生命周期**中的交叉关注点处理器。在 model 调用前后和 tool 执行前后插入逻辑。

```
用户消息 → [before_model] → LLM → [after_model] → tool_call? → [before_tool] → Tool → [after_tool] → ...
```

### 来源与对比

#### DeerFlow 2.0 — 组合式中间件

**项目**：[bytedance/deer-flow](https://github.com/bytedance/deer-flow)

DeerFlow 用 **Python 类组合**实现中间件链，因为 LangChain Agent 是黑盒 callable，需要装饰器链包裹。

**完整中间件清单（有序）**：

| # | 中间件 | 类型 | 触发场景 | 做什么 |
|---|--------|------|---------|--------|
| 1 | ThreadDataMiddleware | 运行时 | 每次 run | 初始化 workspace/uploads/outputs 路径 |
| 2 | UploadsMiddleware | 运行时 | 有上传文件 | 将已上传文件列表注入 messages |
| 3 | SandboxMiddleware | 运行时 | 需代码执行 | 获取/释放 sandbox |
| 4 | DanglingToolCallMiddleware | 运行时 | LLM 被中断 | 清理未完成 tool_call，注入占位 result |
| 5 | LLMErrorHandlingMiddleware | 运行时 | LLM API 错误 | 重试、降级、格式化 |
| 6 | GuardrailMiddleware | 运行时 | 安全检查 | 内容安全护栏（可选） |
| 7 | SandboxAuditMiddleware | 运行时 | sandbox 操作后 | 审计日志 |
| 8 | ToolErrorHandlingMiddleware | 运行时 | Tool 异常 | try/except → 格式化为 tool result |
| 9 | DynamicContextMiddleware | Lead 专属 | 每轮对话 | 日期/memory 注入 HumanMessage（不改 system prompt，保持 prefix cache） |
| 10 | SummarizationMiddleware | Lead 专属 | token 超阈值 | 压缩旧消息；**压缩前 flush 到 Memory**（before_summarization hook） |
| 11 | TodoMiddleware | Lead 专属 | plan_mode | 压缩后恢复 todo；agent 想结束但 todo 未完成 → 注入提醒（max 2 次） |
| 12 | TokenUsageMiddleware | Lead 专属 | 每轮 | 统计 token，子 agent 归属父 |
| 13 | TitleMiddleware | Lead 专属 | 首轮 | LLM 生成对话标题 |
| 14 | MemoryMiddleware | Lead 专属 | 每轮后 | 防抖批量更新长期 memory（filter user+assistant only） |
| 15 | ViewImageMiddleware | Lead 专属 | 多模态 | upload 图片 → vision message |
| 16 | DeferredToolFilterMiddleware | Lead 专属 | tool 多时 | 按需加载 tool schema |
| 17 | SubagentLimitMiddleware | Lead 专属 | 多子 agent | 限并行子 agent 数 |
| 18 | LoopDetectionMiddleware | Lead 专属 | 死循环 | 检测重复 tool call → 软警告(8次)+硬中断(12次) |
| 19 | ClarificationMiddleware | Lead 专属 | 用户澄清 | 检测 agent 请求澄清 → 暂停 |

**Summarization 特殊设计（compact-middleware 增强版）**：

| 级别 | 名称 | 需要 LLM | 做什么 |
|------|------|:---:|--------|
| L1 | COLLAPSE | 否 | 合并连续 read/search 操作 |
| L2 | TRUNCATE | 否 | 截断旧消息中的超长 tool args |
| L3 | MICROCOMPACT | 否 | 清理过期 tool result（按时间间隔） |
| L4 | SUMMARIZE | 是 | LLM 压缩，9 段式结构化 prompt，保留文件路径和代码 |

来源：[emanueleielo/compact-middleware](https://github.com/emanueleielo/compact-middleware)

**LoopDetection 无状态设计**：

来源：[langchain-ai/deepagents PR#1327](https://github.com/langchain-ai/deepagents/pull/1327)

- 每次 hook 扫描 message history 而非维护 mutable state（线程安全）
- 使用 `Path.resolve()` 合并相对/绝对路径计数
- 软阈值（8 次编辑）→ 追加 nudge 到 tool message
- 硬阈值（12 次编辑）→ 注入 HumanMessage 强制停止 + `jump_to: "model"`

**DoubleBuffer Summarization（实验性）**：

来源：[langchain-ai/langchain PR#35434](https://github.com/langchain-ai/langchain/pull/35434)

70% 上下文时后台开始压缩（back buffer）；95% 时切换 buffer。Agent 不停等，压缩与执行并行。

#### CrewAI — Hook/Decorator + Event Listener

**项目**：[crewAIInc/crewAI](https://github.com/crewAIInc/crewAI)

CrewAI 用**两套系统**：

**1. Execution Hooks（同步拦截）**：

```python
@before_llm_call
def safety_check(context: LLMCallHookContext):
    if is_dangerous(context.messages): return False  # 阻断

@after_tool_call
def audit_log(context: ToolCallHookContext):
    log.info(f'{context.tool_name} → {context.tool_result}')
```

- `before_llm_call` / `after_llm_call`：拦截 LLM 调用
- `before_tool_call` / `after_tool_call`：拦截 Tool 执行
- 返回 `False` = 阻断执行
- 支持 crew-scoped（只对特定 crew 生效）和 global 两种作用域

**2. Event Listeners（异步订阅）**：

```python
@crewai_event_bus.on(AgentExecutionCompletedEvent)
def on_done(source, event):
    metrics.record(event.agent.name, event.duration)
```

- 生命周期事件：`CrewKickoffStarted/Completed`、`AgentExecutionStarted/Completed`、`ToolUsageStarted/Finished`
- 不阻断执行流，纯观测

#### LangChain/DeepAgents — Middleware 类

**项目**：[langchain-ai/deepagents](https://github.com/langchain-ai/deepagents)

```python
class SummarizationMiddleware(BaseMiddleware):
    async def before_model(self, state, config): ...
    async def after_model(self, state, config, response): ...
```

- 标准 `before_model` / `after_model` / `before_tool` / `after_tool` 接口
- 通过 `create_agent(middlewares=[...])` 注入
- `SummarizationToolMiddleware` 暴露 `compact_conversation` 工具，让 agent 自主决定何时压缩

### A2 适配建议

A2 基于 bollydog Command yield，**不需要独立中间件抽象层**，原因：

1. Command 是 async generator，`yield` 前 = before_model，`yield` 后 = after_model
2. `try/except` 天然是 error handling
3. `yield StreamState` 天然是遥测推送

**三层策略**：

| 层 | 适用场景 | 实现方式 | 示例 |
|----|---------|---------|------|
| Command 内置 | 与 ReAct 循环紧耦合 | 直接写在 Command yield 链中 | ToolError、LoopDetect、TodoCheck |
| Service Hook | 跨 Command 共享 | ContextService / AgentService 方法 | compact_with_flush、inject_dynamic_context |
| Exchange 事件 | 完全解耦、非阻塞 | bollydog pub/sub | TokenUsage、Memory 异步更新 |

---

## F3：Sub-agent 流式遥测

### 来源：DeerFlow 2.0

子 agent 执行时，父 agent 通过 `get_stream_writer()` **实时**接收子 agent 的中间 AI 消息，以 SSE 自定义事件推送给前端：

```
task_started → task_running(partial AI msg) → task_running(partial AI msg) → task_completed(result)
```

**关键设计**：
- 子 agent 运行在**独立事件循环线程**（`_isolated_subagent_loop`）避免嵌套 asyncio
- 协作式取消：`threading.Event` + 流式 chunk 间检查 `cancel_event.is_set()`
- Token 计量：`SubagentTokenCollector` 子 agent 用量归属父 agent 的 `tool_call_id`
- 并发限制：`SubagentLimitMiddleware` 控制最大并行数（默认 3）

### A2 适配建议

bollydog `_run_gen` + `qos=0` + `StreamState` 天然支持流式遥测：

```python
# SpawnAgentCommand 中
result = yield AgentCommand(role_def=..., goal=...)
# 但这是阻塞的 — 子 Command 完成才返回

# 流式化改造：子 AgentCommand 内部 yield StreamState 到自己的 state queue
# 父 Command 可通过 sub.state 的 async iteration 实时获取
```

---

## F4：Memory — 压缩前 Flush + 防抖更新

### 来源：DeerFlow 2.0

**压缩前 Flush**：`SummarizationMiddleware` 注册 `before_summarization` hook，把即将被压缩的对话推给 Memory 提取 insights，避免长对话中有价值信息丢失。

**防抖更新**：`MemoryMiddleware` 在每轮对话后 `queue.add()`，不立即写入。累积到阈值或超时后批量处理：
1. 过滤消息（只保留 user + final assistant）
2. LLM 判断哪些事实需要更新/删除（`shouldUpdate`, `newFacts`, `factsToRemove`）
3. 去重写入，跳过 upload-session 临时句子

**Memory 结构**：

```python
{
    "user": {
        "workContext":      {"summary": "", "updatedAt": ""},
        "personalContext":  {"summary": "", "updatedAt": ""},
        "topOfMind":        {"summary": "", "updatedAt": ""},
    },
    "history": {
        "recentMonths":      {"summary": "", "updatedAt": ""},
        "earlierContext":    {"summary": "", "updatedAt": ""},
        "longTermBackground":{"summary": "", "updatedAt": ""},
    },
    "facts": [...]
}
```

### A2 适配建议

A2 的 L1 (insights) 已有类似机制。增强点：
- ContextService `_compact_messages()` 前 → 将待压缩消息推给 L1 提取
- L2 (GlobalFacts) 增加防抖 batch 更新而非每条消息触发

---

## F5：Skill — 渐进加载 + 工具门控 + Summarization Rescue

### 来源：DeerFlow 2.0

**渐进加载**：Skill 不是全量注入 system prompt，而是按需加载。`DeferredToolFilterMiddleware` 按需展示 tool schema。

**工具门控**：`SKILL.md` frontmatter 声明 `allowed-tools`，当任一启用的 skill 有此声明时，ToolService 取所有启用 skill 的 union 作为白名单。

```yaml
---
name: research
description: Web research capability
allowed-tools:
  - web_search
  - web_fetch
  - read_file
---
```

**Summarization Rescue**：`SummarizationMiddleware` 检测到 `read_file` 读过 `/mnt/skills/` 下的文件时，在压缩中保留这些 skill 内容（token/count budget 限制），防止刚加载的 skill 指令被压缩掉。

### A2 适配建议

A2 已有三级披露机制（L1 元数据 → L3 关键词触发 → 全文注入）。可增强：
- Skill 模型增加 `allowed_tools: list[str]` 字段
- 压缩时检查最近的 skill 注入，保留 N 条

---

## F6：Plan Mode — TodoList + 完成检查

### 来源：DeerFlow 2.0

`TodoMiddleware` 在 plan_mode 下激活：
- 维护 `ThreadState.todos: list[dict]`
- **Summarization Recovery**：todo 被压缩掉 → 注入 `HumanMessage(name="todo_reminder")` 恢复快照
- **Completion Check**：agent 想结束但 todo 未完成 → 注入 `todo_completion_reminder`（最多 2 次，防死循环）
- 前端 workspace 面板实时展示 todo 列表

### A2 适配建议

A2 的 PlannerService + TaskList 已覆盖核心功能。可增强：
- AgentCommand 中：task_list 有 pending 但 agent 返回 → 注入提醒
- 压缩时保留 task_list snapshot

---

## F7：IM Channel 多渠道接入

### 来源：DeerFlow 2.0

| 渠道 | 传输方式 | 复杂度 |
|------|---------|--------|
| Telegram | Bot API (long-polling) | 简单 |
| Slack | Socket Mode | 中等 |
| 飞书/Lark | WebSocket | 中等 |
| 微信 | Tencent iLink (long-polling) | 中等 |
| 企业微信 | WebSocket | 中等 |
| 钉钉 | Stream Push (WebSocket) | 中等 |

所有渠道通过内部 LangGraph-compatible API 对接，无需公网 IP。

### A2 适配建议

**延后到 v2**。A2 通过 bollydog HTTP/SSE 提供 API，IM 适配层可作为独立模块添加。

---

## F8：Context Engineering 上下文工程

### 来源：DeerFlow 2.0 + LangChain/DeepAgents

**子 Agent 上下文隔离**：每个子 agent 独立上下文，看不到父 agent 或兄弟 agent 的对话。

**Prefix Cache 友好**：`DynamicContextMiddleware` 将动态内容（日期、memory）注入 **HumanMessage** 而非修改 **system prompt**，使 system prompt 保持稳定，对 LLM API 的 prefix cache 友好。

**Strict Tool-Call Recovery**：LLM 被 provider/middleware 中断时，清理 raw tool-call 元数据，注入占位 tool result，防止 OpenAI 等严格校验 `tool_call_id` 序列的模型报错。

### A2 适配建议

- ContextService `build_messages` 已有三段式设计（静态 system + reminder + history），天然 prefix cache 友好
- 增加 DanglingToolCall 处理：ReActStepCommand 捕获异常后注入占位 tool result

---

## F9：Sandbox 安全执行

### 来源：DeerFlow + E2B + OpenHands

| 方案 | 隔离级别 | 复杂度 | 适用 |
|------|---------|--------|------|
| DeerFlow LocalSandboxProvider | 无隔离 | 低 | 开发 |
| DeerFlow AioSandboxProvider | Docker 容器 | 中 | 生产 |
| E2B Sandbox | 云端 micro-VM | 高 | 企业级 |
| OpenHands | Docker + VNC | 高 | 完整桌面 |

A2 已有 `EnvService` + `DockerProtocol`，覆盖 Docker 隔离。

---

## 参考项目索引

| 项目 | 链接 | Stars | 关注特性 |
|------|------|-------|---------|
| DeerFlow | [bytedance/deer-flow](https://github.com/bytedance/deer-flow) | 67.7K | Artifact / Middleware / Memory / Sub-agent / Skill / IM |
| DeepAgents | [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) | — | Middleware / Summarization / LoopDetection |
| CrewAI | [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | — | Hook/Decorator / Event Listener |
| OpenHands | [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) | 73.5K | Sandbox / Browser / SDK |
| E2B | [e2b-dev/E2B](https://github.com/e2b-dev/E2B) | 11.6K | Sandbox infra |
| E2B Fragments | [e2b-dev/fragments](https://github.com/e2b-dev/fragments) | — | Artifact 预览模板 |
| Open Artifacts | [13point5/open-artifacts](https://github.com/13point5/open-artifacts) | — | Claude Artifacts 克隆 |
| OpenArtifacts | [mayfer/open-artifacts](https://github.com/mayfer/open-artifacts) | — | 单文件 Artifact 渲染 |
| Artifactuse SDK | [artifactuse/sdk](https://github.com/artifactuse/sdk) | — | 框架无关 Artifact 组件 |
| agent-render | [baanish/agent-render](https://github.com/baanish/agent-render) | — | 静态 Artifact viewer |
| mdserve | [jfernandez/mdserve](https://github.com/jfernandez/mdserve) | — | Markdown 预览服务 |
| compact-middleware | [emanueleielo/compact-middleware](https://github.com/emanueleielo/compact-middleware) | — | 四级压缩中间件 |
| LangGraph GenUI | [langchain-ai/langgraphjs-gen-ui-examples](https://github.com/langchain-ai/langgraphjs-gen-ui-examples) | — | React 组件动态加载 |
| CopilotKit GenUI | [CopilotKit/OpenGenerativeUI](https://github.com/CopilotKit/OpenGenerativeUI) | — | MCP + 交互式 AI UI |
