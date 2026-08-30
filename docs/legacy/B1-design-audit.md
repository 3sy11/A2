# B1 — 设计文档审计（对照五步法）

> 基于五步法（场景故事 → 顺序图 → 接口契约 → 数据模型 → Walking Skeleton）对现有设计文档做逐层审计。
> 目的：找出每层的完成度、缺口、和层间对齐问题，确定从哪里开始重新设计。

---

## 审计总览

| 步骤 | 文档 | 完成度 | 判定 |
|------|------|:------:|------|
| 步骤 1 — 场景故事 | B0-user-stories.md | 85% | 15 个故事已列出，需确认筛选 |
| 步骤 2 — 顺序图 | A0-t1/t2/t3 | 40% | 3 份追踪覆盖主路径，但箭头不够精确，6 个场景无图 |
| 步骤 3 — 接口契约 | 散落在 02~09 模块文档中 | 30% | **无独立文档**，签名不完整，内部/公开未分离 |
| 步骤 4 — 数据模型 | 01-data-models.md | 75% | 模型定义较完整，但缺少 3 个关键类型，未反向验证 |
| 步骤 5 — Walking Skeleton | a2/ 目录有 stub 文件 | 0% | 无骨架策略，无最薄切片定义 |

**当前位置**：有步骤 1 + 步骤 4（部分），缺步骤 2（精确顺序图）、步骤 3（接口契约）、步骤 5（骨架代码）。

---

## 步骤 1 — 场景故事

**现状**：B0-user-stories.md 列出 15 个场景（S01-S15），每个有 触发/过程/返回 三段结构。

**优点**：
- 覆盖了基础对话、规划编排、工具权限、记忆、结晶、管理、生命周期七个维度
- 主体明确（用户/管理员/运维/系统）

**缺口**：

| # | 问题 | 说明 |
|---|------|------|
| 1.1 | 部分故事过程描述偏实现 | S10（压缩）提到"三层""BM25"等技术细节，应只描述用户视角的效果 |
| 1.2 | 缺错误/边界场景 | 无 LLM 超时/失败、MCP server 断连、DB 损坏等异常场景 |
| 1.3 | 用户故事未分优先级 | 15 个故事无 P0/P1/P2 优先级标记，Walking Skeleton 选哪个不明确 |

**建议**：
- 给每个故事标 P0（骨架必须）/ P1（v1 必须）/ P2（v2 延后）
- S01+S02+S03 作为 P0 候选（最薄切片）

---

## 步骤 2 — 顺序图

**现状**：A0-t1/t2/t3 三份端到端追踪，每份有 Mermaid 序列图 + 文字走查。

**覆盖矩阵**：

| 场景 | 有顺序图？ | 箭头有方法签名？ |
|------|:---------:|:--------------:|
| S01 直答 | ✗ | — |
| S02 单工具 | ✗（t1 是 S03） | — |
| S03 多轮工具 | ✓ t1 | 部分 |
| S04 任务规划 | ✗（t2 含规划但与 S05 合并） | — |
| S05 多角色并行 | ✓ t2 | 部分 |
| S06 子角色失败 | ✗ | — |
| S07 危险操作审批 | ✓ t3 | 部分 |
| S08 MCP 工具 | ✓ t3 | 部分 |
| S09 权限拒绝 | ✗ | — |
| S10 长对话压缩 | ✓ t3 | 部分 |
| S11 跨会话回忆 | ✗ | — |
| S12 技能匹配 | ✓ t1（含在 build_messages 中） | 部分 |
| S13 手动结晶 | ✓ t3 | 部分 |
| S14 角色热更新 | ✗ | — |
| S15 冷启动 | ✗（t1 文字有，无独立图） | — |

**核心缺口**：

| # | 问题 | 影响 |
|---|------|------|
| 2.1 | 箭头不是一次调用 | 多个箭头合并了多步操作，如 "activate_role → ContextService 创建" 内部其实是 5 个调用 |
| 2.2 | 箭头缺方法签名 | 图中写 `chat(messages, tools)` 但缺入参类型和返回类型 |
| 2.3 | 6 个场景无图 | S01/S02/S06/S09/S11/S14 无对应顺序图 |
| 2.4 | 追踪文档混合伪代码和流程 | t1/t2/t3 大量 Python 代码块，不是纯顺序图，读者难以区分"设计"和"实现" |
| 2.5 | 无错误路径 | 所有图只画了 happy path，无 LLM 超时、工具失败、权限拒绝的分支 |

**建议**：
- 每个 P0/P1 故事各画一张**纯顺序图**（无伪代码），箭头格式：`方法名(关键参数) → 返回类型`
- 补 error path 分支（至少 S09 权限拒绝、S06 子角色失败）

---

## 步骤 3 — 接口契约

**现状**：无独立文档。方法签名散落在 8 个模块文档（02-09）和 3 份追踪（t1-t3）中。

**已有签名清单**（从模块文档提取）：

| Service | 方法 | 签名完整度 | 来源 |
|---------|------|:---------:|------|
| LLMService | `chat(messages, model, tools, **kw)` | ⚠️ 返回类型 `LLMResponse` 未定义 | 02 |
| LLMService | `count_tokens(messages, model)→int` | ✅ | 02 |
| EnvService | `get_tool_schemas(filter, model)→list[dict]` | ✅ | 03 |
| EnvService | `execute_tool(name, params)→str` | ✅ | 03 |
| TerminalProtocol | `check_permission(tool_name)→str` | ✅ | 03 |
| TerminalProtocol | `execute(command, timeout)→str` | ✅ | 03 |
| TerminalProtocol | `read_file(path, offset, limit)→str` | ✅ | 03 |
| SkillService | `get_metadata_prompt(skill_refs)→str` | ✅ | 05 |
| SkillService | `get_matching(query, skill_refs)→list[Skill]` | ✅ | 05 |
| SkillService | `load_skill(name)→str` | ✅ | 05 |
| SkillService | `save_skill(skill)→None` | ✅ | 05 |
| PlannerService | `save_task_list(task_list, base_dir)` | ⚠️ 无返回类型 | 06 |
| PlannerService | `load_task_list(run_id, base_dir)→TaskList?` | ✅ | 06 |
| ContextService | `build_messages(role_def, query, history)→list[dict]` | ✅ | 08 |
| ContextService | `compact_if_needed(messages, model)→list[dict]` | ✅ | 08 |
| MCPService | `get_tool_schemas()→list[dict]` | ✅ | 07 |
| MCPService | `call_tool(full_name, arguments)→str` | ✅ | 07 |
| AgentService | `activate_role(role_name)→ContextService` | ✅ | 09 |
| AgentService | `deactivate_role(role_name)` | ⚠️ 无返回类型 | 09 |
| AgentService | `get_agent_tool_schemas()→list[dict]` | ✅ | 09 |
| AgentService | `get_available_description()→str` | ✅ | 09 |

**缺失签名**（追踪中出现但模块文档中未定义）：

| 方法 | 出现位置 | 问题 |
|------|---------|------|
| `ContextService._memory_flush(messages)` | t3 | 08-mod-context 未提及 |
| `ContextService._micro_compact(messages)` | t3 | 08 只有表格描述，无签名 |
| `ContextService._full_compact(messages, model)` | t3 | 同上 |
| `ContextService._render_prompt(template, role_def)→str` | 08 | 有签名但属内部方法，未标注 |
| `MemoryLayerService.get_recent(token_budget)` | t1 | 04-mod-memory 无此方法，只有 get/set/get_all |
| `GlobalFactsService.get_relevant(query, budget)→str` | 08 | 04 中签名是 `search(query, top_k)→list` |
| `ToolCommand.execute(**kw)→str` | 03 | 与 `__call__` 关系不明 |

**缺失类型定义**：

| 类型 | 使用位置 | 问题 |
|------|---------|------|
| `LLMResponse` | LLMService.chat 返回值 | 模型未定义（有 .text .tool_calls 属性） |
| `TaskResult` | ReActStepCommand 返回值 | t1 中使用但 01-data-models 无定义 |
| `Message` | Hub.dispatch 入参 | bollydog 类型，A2 未明确 payload 格式 |
| yield dict 格式 | AgentCommand 流式输出 | `{type, content, task_id, status}` 未标准化 |

**核心缺口**：

| # | 问题 | 影响 |
|---|------|------|
| 3.1 | 无独立接口契约文档 | 签名散落，无法一眼看全所有类的公开 API |
| 3.2 | 内部/公开方法未分离 | `_micro_compact` 是内部方法但追踪中当公开方法讨论 |
| 3.3 | 3 个关键返回类型未定义 | `LLMResponse`、`TaskResult`、yield dict 格式 |
| 3.4 | 方法名不一致 | 04 说 `search()`，08 说 `get_relevant()`；t1 说 `save_session()`，04 只有 `set()` |
| 3.5 | Command 签名零散 | Command 是核心执行单元，但入参/返回值只在追踪中出现，模块文档重复定义 |
| 3.6 | 接口非从顺序图推导 | 现在是"先想好类再补方法"，应该是"顺序图箭头逼出方法" |

---

## 步骤 4 — 数据模型

**现状**：01-data-models.md 定义了 6 类模型 + ER 图 + 存储布局。

**已有模型**：

| 模型 | 字段完整度 | 存储位置 |
|------|:---------:|---------|
| RoleDef | ✅ 12 字段 | roles.db |
| ServiceSpec | ✅ 6 字段 | config.py |
| Task/TaskList | ✅ 8+4 字段 | tasks.db |
| Skill | ✅ 10 字段 | skills.db + 文件系统 |
| PermissionRule/Config | ✅ 2+1 字段 | config 注入 |
| ToolCommand 元数据 | ✅ 5 ClassVar | 代码中 |

**缺失模型**：

| 模型 | 需要定义的字段 | 使用位置 |
|------|-------------|---------|
| `LLMResponse` | text: str, tool_calls: list, usage: dict | LLMService.chat 返回 |
| `TaskResult` | success: bool, output: str | ReActStepCommand 返回 |
| `ToolCall` | id: str, name: str, arguments: dict | LLM 返回的 tool_calls 元素 |
| 流式 chunk 类型 | type: str, content/task_id/status 等 | AgentCommand yield |
| `ApprovalRequest` | tool_name, arguments, timeout | Q1 审批机制（待研究） |

**ER 图缺口**：

| # | 问题 |
|---|------|
| 4.1 | ER 图只画了 KV 表，未体现 `RoleDef → ContextService → Memory` 的运行时关系 |
| 4.2 | Skill.body_path → 文件系统的关系用了 SKILL_FILES 虚拟表，实际非表 |
| 4.3 | 模型字段与接口契约未交叉验证（Step 3→4 的推导关系不存在） |

---

## 步骤 5 — Walking Skeleton

**现状**：`a2/` 目录下有 ~30 个 stub 文件（git status 全部 untracked），但无明确的骨架策略。

**缺口**：

| # | 问题 | 说明 |
|---|------|------|
| 5.1 | 无最薄切片定义 | 未指定哪个场景是第一条通路 |
| 5.2 | 无 stub/mock 策略 | 哪些服务真实实现、哪些 mock |
| 5.3 | 无验证标准 | "跑通"的定义是什么（CLI 输入→LLM 回复？有 tool 调用？） |
| 5.4 | 无测试入口 | 没有 test 文件或 pytest 配置 |

**建议最薄切片**（S01 直答）：
```
CLI input → Hub.dispatch → AgentCommand → build_messages(仅 system_prompt) → LLMService.chat → 返回文字
```
- 真实实现：config.py + LLMService + AgentService + ContextService(最简)
- Mock/Stub：EnvService(空)、SkillService(空)、PlannerService(空)、MCPService(空)、Memory(空)

---

## 层间对齐问题

**最大的结构性问题：各层之间缺少推导关系。**

| 断层 | 表现 |
|------|------|
| 故事 → 顺序图 | 15 个故事只有 5~6 个有对应顺序图 |
| 顺序图 → 接口 | 追踪中的箭头没有系统地提取为接口签名 |
| 接口 → 模型 | 模型是"先设计"的，不是从接口参数/返回值倒推的 |
| 模型 → 骨架 | 无映射关系，stub 文件与模型/接口无关联 |

```
步骤 1 ─?─→ 步骤 2 ─?─→ 步骤 3 ─?─→ 步骤 4 ─?─→ 步骤 5
  S01-S15      t1-t3      (散落)    01-data     a2/ stubs
              部分覆盖    无独立文档   较完整       无策略
```

---

## 建议行动顺序

| 序号 | 行动 | 产出 |
|------|------|------|
| 0 | **确认 B0 场景列表 + 标优先级** | B0 定稿（P0/P1/P2） |
| 1 | **为每个 P0 场景画精确顺序图** | B2-sequence-diagrams.md — 纯图，每箭头一次调用 |
| 2 | **从顺序图提取接口契约** | B3-interface-contracts.md — 每类的公开方法签名表 |
| 3 | **用接口契约反向验证数据模型** | 修正 01-data-models.md — 补缺失类型，删无用字段 |
| 4 | **定义 Walking Skeleton 切片** | B4-skeleton-plan.md — 切片场景 + stub 策略 + 验证标准 |
| 5 | **实现骨架代码** | 主干路径跑通一个真实请求 |

> 原有的 00~11 模块文档作为**参考**保留不动，新的 B 系列文档是从故事推导出来的权威源。
> 当 B 系列与原文档冲突时，以 B 系列为准，回头修正原文档。
