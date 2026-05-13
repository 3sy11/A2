# A2 Agent Framework — 主执行计划

> A2 是基于 bollydog 微服务框架构建的多角色 AI Agent 框架。
> 架构设计见 [01-architecture.md](01-architecture.md)。

---

## 文档索引

| 编号 | 文档 | 内容 |
|------|------|------|
| 01 | [架构设计](01-architecture.md) | 系统架构、信息架构、bollydog 约定、Command 分离 |
| 02 | [基础服务](02-foundation-services.md) | LLM + Tool/Env + Skill + Planner + Registry |
| 03 | [知识管线](03-knowledge-pipeline.md) | Memory + MCP + Context |
| 04 | [Agent 编排](04-agent-orchestration.md) | AgentService + AgentCommand + 多角色 |
| 05 | [部署与验证](05-deployment.md) | 配置 + CLI/HTTP + 文件结构 + 测试 |

---

## 核心设计决策摘要

| 决策 | 方案 | 详见 |
|------|------|------|
| LLM | 单一 LLMService + LiteLLM Router（废弃 Fast/Capable/Code） | doc 02 §一 |
| 工具 | EnvService 基类 + TOML 配 3 实例（废弃独立 Service） | doc 02 §二 |
| 记忆 | MemoryLayerService 基类(L0/L1/L4) + L2 独立（废弃门面） | doc 03 §一二 |
| 技能 | SkillService + 三级披露 + Command 化结晶 | doc 02 §三 |
| 多角色 | AgentRegistryService + RoleDef（废弃 SubagentService） | doc 02 §五 |
| MCP | 官方 mcp SDK（废弃自实现） | doc 03 §三 |
| Prompt | 三段式：静态核心 + System Reminder + 对话历史 | doc 01 §三 |
| 压缩 | MicroCompact → SessionMemory → FullLLMCompact | doc 03 §四 |
| 权限 | allow_list + block_list + require_approval（绑定 RoleDef） | doc 02 §二 |
| CLI/HTTP | fire + Starlette（复用 bollydog） | doc 05 §二 |

---

## 分阶段执行计划

按 DAG 拓扑排序，每层只依赖前层已构建的服务。

```
Phase 1 (零依赖)        Phase 2 (依赖 P1)       Phase 3 (依赖 P1+P2)    Phase 4 (顶层)      Phase 5 (入口)
─────────────────       ──────────────────      ──────────────────      ──────────────      ──────────
LLMService              L0 (TOML实例)           ContextService          AgentService        config/cli/http
 └ LiteLLMProvider      L1 (TOML实例)           └ [L0,L1,L2,Skill,L4] └ 全部子服务         agent.toml
                        L2GlobalFactsService                                                RoleDef TOML
ToolService             L4 (TOML实例)
 └ EnvService×3         MCPService

SkillService
 └ CacheLayer→File

PlannerService
 └ depends: LLMService

AgentRegistryService
 └ KVProtocol
```

### Phase 1: 叶子服务

| # | 模块 | 核心文件 | 验证 |
|---|------|---------|------|
| 1 | LLMService | `llm/service.py` `llm/provider.py` | capability 映射 + Router fallback |
| 2 | ToolService + EnvService | `tools/` + `env/service.py` | 注册、权限、执行、截断 |
| 3 | SkillService | `skills/` | 序列化往返 + 三级披露 |
| 4 | PlannerService | `planner/` | TaskList CRUD + 持久化 |
| 5 | AgentRegistryService | `registry/` | RoleDef 注册/获取 |

### Phase 2: 单层依赖

| # | 模块 | 依赖 | 验证 |
|---|------|------|------|
| 6 | L0/L1/L4 MemoryLayer | — | L0 扫描规则 |
| 7 | L2GlobalFacts | — | BM25 检索 |
| 8 | MCPService | ToolService | 工具发现 + 注册 |

### Phase 3: 多层依赖

| # | 模块 | 依赖 | 验证 |
|---|------|------|------|
| 9 | ContextService | L0/L1/L2/Skill/L4/LLM | 三段式组装 + 压缩 |

### Phase 4: 顶层

| # | 模块 | 依赖 | 验证 |
|---|------|------|------|
| 10 | AgentService + AgentCommand | 全部 | 完整 loop + 流式 + 多角色 spawn |

### Phase 5: 入口

| # | 模块 | 验证 |
|---|------|------|
| 11 | config + cli + agent.toml | CLI/HTTP/SSE + 对抗测试 |

---

## 文件清单

| 模块 | 目录 | 文件数 | ~行数 | Phase |
|------|------|--------|------|-------|
| LLM | `llm/` | 3 | 120 | P1 |
| Tools | `tools/` | 6 | 500 | P1 |
| Env | `env/` | 2 | 60 | P1 |
| Skills | `skills/` | 4 | 250 | P1 |
| Planner | `planner/` | 4 | 250 | P1 |
| Registry | `registry/` | 4 | 200 | P1 |
| Memory | `memory/` | 5 | 200 | P2 |
| MCP | `mcp/` | 4 | 200 | P2 |
| Context | `context/` | 2 | 180 | P3 |
| Agent | `agent/` | 4 | 200 | P4 |
| Entry | `./` | 2 | 80 | P5 |
| **合计** | | **40** | **~2240** | |

---

## 待决问题

| # | 问题 | 推荐 |
|---|------|------|
| Q1 | Tool.capability 路由方式 | TOML capability→model 映射表 |
| Q2 | DEFAULT_SYSTEM_PROMPT 是否可覆盖 | 支持 `override_default_prompt` |
| Q3 | Orchestrator 决策机制 | LLM 动态决策（prompt 列出角色） |
| Q4 | 权限检查位置 | ToolService（统一入口） |
| Q5 | MicroCompact 距离阈值 | N=10，实测调整 |
| Q6 | Reactive Network 时间 | v2 引入 |
| Q7 | prompt 模板变量 | 支持 `{current_date}` 插值 |

---

## 审计修复记录

### CRITICAL（已修复）

| # | 问题 | 修复 |
|---|------|------|
| C1 | SubagentTool 引用不存在属性 | `hub.execute(SpawnAgentCommand)` |
| C2 | SkillService 调用 LLM 但 depends=[] | 结晶 Command 化 |
| C3 | `app.resolve()` 虚构 API | `AppService._apps['key']` |
| C4 | `_run_gen` 伪代码不准确 | `gen.asend(feedback)` |
| C5 | depends 键名不匹配 | 统一 domain 命名 |

### HIGH（已修复）

| # | 问题 | 修复 |
|---|------|------|
| H1 | AgentCommand 构造函数注入 | globals.app + _apps |
| H2 | Command 穿透子服务 | _apps 访问 |
| H3 | L3→L1 跨层通知缺失 | NotifyIndexUpdateCommand |
| H4 | SubagentTool 直调服务 | SpawnAgentCommand |
