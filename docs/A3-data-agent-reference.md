# A3 — data-agent 项目参考分析

> 来源：`/Users/akulaku/workspace/data-agent`
> 定位：面向企业数据平台的 AI Agent（前后端完整产品），已在生产运行。
> 对比目的：识别 A2 可借鉴的实战特性、可简化的设计，以及需要新增支持的能力。

---

## 一、项目概况

data-agent 是一个前后端分离的企业级数据平台智能助手，核心架构：

```
frontend (Vue3)  ←SSE→  FastAPI backend
                           ├── agent/engine (自研 ReAct 引擎)
                           ├── agent/react (ReAct Loop ~120行核心)
                           ├── agent/context (上下文预算管理)
                           ├── agent/memory (向量+图谱记忆)
                           ├── agent/persona (Persona 四层提示词)
                           ├── agent/harness (技能工具运行时)
                           ├── skills/ (技能系统 + @skill_tool 装饰器)
                           ├── tools/ (内置工具: call_api, query_db, run_command)
                           ├── storage/ (JSONL 文件存储)
                           └── core/ (安全, 可观测性, 配置)
```

**技术栈**：FastAPI + Pydantic Settings + OpenAI SDK + AgentScope(部分) + structlog + OpenTelemetry + JSONL 文件存储

---

## 二、A2 应新增支持的特性

### 2.1 上下文预算管理（ContextBlock + ContextAssembler）

**data-agent 做法**：将所有上下文来源（system_prompt, memory, history, scratchpad, flow_context, vector_memory, graph_memory）抽象为带优先级的 `ContextBlock`，通过 `ContextAssembler` 在 token 预算内按优先级选择。生成 `ContextLedger`（账本）记录每个 block 是否被包含及原因。

```python
class ContextBlock:
    source: ContextSource  # 枚举：SUMMARY / MEMORY / HISTORY / SYSTEM_PROMPT / ...
    content: str
    token_count: int
    priority: int = 0       # 高优先级先保留
    protected: bool = False  # 受保护的 block 即使超预算也保留
```

**A2 差距**：A2 的 ContextService.build_messages 是硬编码拼接顺序，没有预算感知的 block 选择机制。

**建议**：在 ContextService 中引入 ContextBlock 抽象 + 优先级选择 + Ledger 审计追踪。

### 2.2 Artifact 溢出存储（ArtifactStore）

**data-agent 做法**：工具输出超过阈值（50K 字符）时，自动 spill 到文件系统，返回 LLM 摘要 + artifact_id。Agent 可通过 `read_artifact(artifact_id, offset, limit)` 分页读取完整内容。

- `tool_output_max_chars = 50000`：超出自动 spill
- `tool_output_preview_chars = 10000`：spill 后保留的预览
- `artifact_max_bytes = 2MB`：单个 artifact 上限
- 自动裁剪：每会话最多 200 个文件 / 50MB

**A2 差距**：A2 的工具输出只是简单截断到 30000 字符，没有溢出存储和分页读取。

**建议**：在 EnvService 或独立 ArtifactService 中实现 spill + read_artifact 工具。

### 2.3 Checkpoint 中断恢复（CheckpointManager）

**data-agent 做法**：Agent 每次工具调用后保存 checkpoint（messages_snapshot + tool_results + state），崩溃后可从最近 checkpoint 恢复。包含超时过期机制（300s）和内容匹配验证。

**A2 差距**：A2 的 PlannerService 有 tasks.db 持久化，但没有 turn 级别的中断恢复。

**建议**：在 AgentCommand 的 ReAct 循环中加入轻量 checkpoint 写入，利用现有 SQLiteProtocol。

### 2.4 Turn Trace（执行追踪）

**data-agent 做法**：每个 turn 结束后写入结构化 trace：

- tool_timeline（工具调用时间线，含 outcome 分类）
- guard_events（熔断事件）
- quality_signals（截断/循环/熔断标记）
- context manifest（上下文预算使用情况）
- intent_keywords（意图关键词）
- answer_verification（答案质量验证）

支持 compact 到 32KB 预算，保证 trace 不会无限膨胀。

**A2 差距**：A2 没有 turn 级执行追踪机制。

**建议**：在 ReActStepCommand 完成后写入 trace 到角色目录，便于回溯和调试。

### 2.5 Agent Insights / Lessons（经验蒸馏）

**data-agent 做法**：从 turn trace 中蒸馏出 verified lessons（工具调用模式、错误恢复策略），注入后续 turn 的 system prompt，形成**跨会话的经验积累**。

```python
async def build_runtime_lessons_context(principal, skill_scopes, query, max_items, max_chars):
    # 从 lessons.jsonl 中匹配相关经验
    # 注入 runtime_contexts["verified_lessons"]
```

**A2 差距**：A2 的 L1 Insights 偏向静态规则，缺少从执行 trace 自动蒸馏经验的闭环。

**建议**：CrystallizeSkillCommand 可扩展为从 trace 中提取 lessons → L1。

### 2.6 SSE 事件体系（细粒度前端通知）

**data-agent 做法**：定义丰富的 SSE 事件类型：

| 事件 | 用途 |
|------|------|
| `turn_started` / `turn_completed` | Turn 生命周期 |
| `stream` / `thinking` | 流式输出 / 推理过程 |
| `tool_call` / `tool_result` / `tool_call_params` | 工具调用全过程 |
| `human_question` | 需用户选择或输入 |
| `plan_proposal` / `plan_step_update` | 计划确认 + 步骤进度 |
| `confirm_request` | 写操作确认 |
| `quality_signals` | 质量信号（截断/循环/熔断） |
| `context_update` | 上下文使用情况 |
| `suggestions` | 后续建议 |

**A2 差距**：A2 的 StreamState 只有基础的 text/progress 流式输出。

**建议**：定义标准 SSE 事件枚举，在 AgentCommand 的 yield 中使用。

### 2.7 写操作确认门（Confirm Gate）

**data-agent 做法**：middleware 自动检测写操作（create/update/delete 前缀或 POST/PUT/DELETE 方法），弹出前端确认对话框。支持表单编辑（JSON body 展开为字段），超时自动取消。

**A2 差距**：A2 的 PermissionProtocol 只有 allow/block/approve 三态，没有交互式确认流程。

**建议**：PermissionProtocol 的 `approve` 态接入 ask_user 工具实现交互式确认。

### 2.8 多模型分级（场景化模型选择）

**data-agent 做法**：按场景分配不同模型：

| 场景 | 配置项 | 用途 |
|------|--------|------|
| 主链路 | `llm_model` | 会话推理 |
| 轻量任务 | `llm_small_model` | 建议生成、压缩、分类 |
| 建议 | `suggestion_model` | 后续建议 |
| 压缩 | `context_compression_model` | 上下文压缩 |
| 分类 | `classification_model` | 意图分类 |
| 改写 | `rewrite_model` | 工具输出摘要 |
| 降级 | `llm_fallback_model` | 主模型失败后降级 |

**A2 差距**：A2 只有 `RoleDef.model` + `Tool.required_model`，缺少"内部任务用小模型"的机制。

**建议**：在 LLMService 中增加 `chat_internal(task_type, ...)` 方法，按任务类型自动选择模型。

---

## 三、可简化 A2 设计的启示

### 3.1 ReAct Loop 极简化

data-agent 的核心 ReAct Loop 只有 ~120 行（`react/loop.py`），职责清晰：

```
while turn < max_turns:
    system_prompt = anchor.build(turn, total_tool_calls, max_turns)
    response = llm.chat(tools)
    if response.tool_calls:
        results = dispatch(tool_calls)  # 支持并行
    else:
        guard = guard_no_tool(response)
        if guard.action == EXIT: break
        if guard.action == RETRY: append_nudge; continue
```

**对 A2 的启示**：A2 的 ReActStepCommand / PlanCommand / ReflexionCommand 三层可以合并为一个紧凑的循环，Plan 和 Reflexion 作为循环内的**可选分支**而非独立 Command。

### 3.2 Guard 机制（无需独立 Command）

data-agent 的 guard 逻辑内联在 ReAct Loop 中：

- **fake_tool 检测**：LLM 在文本中模拟工具调用格式 → 提醒重试
- **orphan_code_block 检测**：回复只有一个大代码块 → 提醒使用工具
- **空回复计数**：连续 3 次空回复 → 退出
- **thinking_loop 检测**：思考字符 >8K 且 >30s 无动作 → 退出
- **tool_circuit_breaker**：相同参数调用同一工具 >4 次 → 熔断

这些**不需要独立的 Command 或 Service**，直接作为 loop 内的函数。

### 3.3 Persona 替代独立 ContextService 提示词管理

data-agent 的 PersonaConfig 是一个 dataclass，包含四层 prompt 结构：

1. **Identity & Core Principles**（最高优先级）
2. **Behavioral Strategy**（决策梯度 + 错误策略）
3. **Tools & Skills**（工具规范 + 技能注入）
4. **Output Format**（回复格式 + 富媒体）

`build_system_prompt()` 一个函数搞定，不需要 Service。

**对 A2 的启示**：A2 的 system prompt 模板变量 + 三段式可以进一步精简。Persona 可以作为 RoleDef 的扩展字段，而非 ContextService 的复杂构建逻辑。

### 3.4 JSONL 文件存储（v1 可替代 SQLite）

data-agent 全量使用 JSONL 文件存储（conversations, skills, traces, checkpoints, plans），没有 SQLite/DuckDB。

**对 A2 的启示**：A2 v1 的 SQLiteProtocol 方案是合理的（bollydog 内置），但如果觉得 SQLite 增加复杂度，JSONL 是一个更轻量的替代方案。不过 A2 已有 bollydog Protocol 抽象，SQLite 方案更正式。

---

## 四、对比总结表

| 维度 | data-agent | A2 | 建议 |
|------|-----------|-----|------|
| **ReAct 引擎** | 自研 ~120 行 loop | ReActStep + Plan + Reflexion 三层 Command | A2 可精简为单循环 + 可选分支 |
| **上下文管理** | ContextBlock + Assembler + Ledger | 硬编码拼接 | ✅ A2 需新增 block 抽象 |
| **Artifact 溢出** | ArtifactStore (spill + read_artifact) | 无（截断 30K） | ✅ A2 需新增 |
| **中断恢复** | CheckpointManager (per-turn) | PlannerService tasks.db | ✅ A2 需扩展到 turn 级 |
| **执行追踪** | TurnTrace + tool_timeline + quality_signals | 无 | ✅ A2 需新增 |
| **经验蒸馏** | Insights → lessons.jsonl → 注入 prompt | CrystallizeSkillCommand → L1 | 思路相似，A2 可借鉴 trace→lesson 闭环 |
| **SSE 事件** | 15+ 事件类型 | StreamState 基础流式 | ✅ A2 需丰富事件体系 |
| **写操作确认** | Confirm Gate + 表单编辑 | PermissionProtocol approve 态 | ✅ A2 需补充交互式确认 |
| **多模型分级** | 7 个场景化模型配置 | RoleDef.model + Tool.required_model | ✅ A2 需增加内部任务模型 |
| **Persona / 提示词** | PersonaConfig dataclass + build_system_prompt() | ContextService + 模板变量 | 可简化 |
| **存储** | JSONL 文件 | CacheLayer → SQLiteProtocol | A2 方案更正式 |
| **技能系统** | @skill_tool 装饰器 + Harness 运行时 | SkillService + Markdown + 三级披露 | 各有优势 |
| **安全** | Auth + RateLimiter + AuditLog + Guard | PermissionProtocol | data-agent 更完善 |
| **可观测性** | OpenTelemetry + Prometheus metrics | 无 | ✅ A2 需考虑 |
| **Tool Harness** | selection_hook + default_params + inject_from_session | 无 | ✅ 工具运行时增强 |
| **Flow 引擎** | 表单收集 + 步骤编排 + chat/confirm 模式 | 无 | 可作为 v2 特性 |

---

## 五、优先级建议（A2 v1 应考虑）

| 优先级 | 特性 | 理由 |
|--------|------|------|
| P0 | 上下文预算管理 (ContextBlock + Assembler) | 直接影响 agent 质量，A2 目前是硬编码拼接 |
| P0 | SSE 事件体系 | 前端必需，A2 的 yield dict 需要标准化 |
| P1 | Artifact 溢出存储 | 防止长输出截断丢失信息 |
| P1 | Guard 机制（熔断/fake_tool/空回复） | 生产必备的防护栏 |
| P1 | 多模型分级 | 内部任务用小模型降低成本 |
| P2 | Turn Trace 执行追踪 | 调试和经验积累的基础 |
| P2 | Checkpoint 中断恢复 | 长任务可靠性保障 |
| P2 | 写操作确认门 | 数据安全 |
| P3 | 可观测性 (OpenTelemetry) | 生产监控 |
| P3 | Agent Insights/Lessons | 跨会话学习 |
| v2 | Flow 引擎 | 表单收集 + 步骤编排 |
| v2 | Tool Harness 运行时增强 | selection_hook + default_params |

---

## 六、关键代码参考路径

| 模块 | 路径 |
|------|------|
| ReAct Loop | `backend/app/agent/react/loop.py` |
| 上下文预算 | `backend/app/agent/context/blocks.py` + `assembler.py` |
| Artifact 存储 | `backend/app/agent/artifact_store.py` |
| Checkpoint | `backend/app/agent/checkpoint.py` |
| Turn Trace | `backend/app/agent/trace/turn_trace.py` |
| Agent Insights | `backend/app/agent/insights.py` |
| Persona 提示词 | `backend/app/agent/persona/persona.py` |
| SSE Bridge | `backend/app/agent/sse/bridge.py` + `manager.py` |
| Middleware | `backend/app/agent/middleware.py` |
| Hooks (确认/计划) | `backend/app/agent/hooks.py` |
| 配置 | `backend/app/core/config.py` |
| 引擎入口 | `backend/app/agent/engine/engine.py` |
| 技能系统 | `backend/app/skills/service.py` + `matcher.py` |
