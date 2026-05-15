# 开发计划

> 按 DAG 拓扑排序，每层只依赖前层已构建的服务。

---

## 一、依赖 DAG

```
Phase 1 (零依赖)          Phase 2 (依赖 P1)    Phase 3 (无静态依赖)    Phase 4 (顶层)       Phase 5 (入口)
──────────────────       ────────────────     ──────────────────     ──────────────      ──────────
llm.LLMService           mcp.MCPService       context.ContextService  agent.AgentService   config.py
 └ LiteLLMProvider        └ env.local          └ depends:[Skill,LLM]  └ 全部子服务          cli.py
                                               └ owns: L0/L1/L4/Facts └ activate default   agent.toml
env.{local,docker}                              (per-role 动态子类)
 └ ToolCommand + 权限

skills.SkillService
 └ CacheLayer→File

planner.PlannerService
 └ depends: llm.LLMService

memory 基类（由 ContextService 动态实例化）
```

---

## 二、分阶段详细计划

### Phase 1: 叶子服务

| # | 模块 | 核心文件 | 验证 |
|---|------|---------|------|
| 1 | LLMService | `llm/service.py` `llm/provider.py` | Router 多模型 + RoleDef.model 选模型 |
| 2 | EnvService | `env/service.py` `env/tools.py` `env/protocol.py` `env/permission.py` | ToolCommand 注册、权限检查、env 注入执行、截断 |
| 3 | SkillService | `skills/` | 序列化往返 + 三级披露 + skill_refs 过滤 |
| 4 | PlannerService | `planner/` | TaskList CRUD + 持久化开关 |
| 5 | MemoryLayerService + GlobalFactsService | `memory/` | 基类验证（由 ContextService 动态实例化） |

### Phase 2: 单层依赖

| # | 模块 | 依赖 | 验证 |
|---|------|------|------|
| 6 | MCPService | env.local | 工具发现 + 聚合 |

### Phase 3: ContextService

| # | 模块 | 依赖 | 验证 |
|---|------|------|------|
| 7 | ContextService | Skill + LLM (depends) | per-role 动态子类 + owns memory + 三段式 + 压缩 |

### Phase 4: 顶层编排

| # | 模块 | 依赖 | 验证 |
|---|------|------|------|
| 8 | AgentService | 全部 | 角色管理 + activate_role + 完整 loop + 多角色 spawn + 工具聚合 |

### Phase 5: 入口

| # | 模块 | 验证 |
|---|------|------|
| 9 | config.py + cli.py + agent.toml | build_config 合并 + CLI/HTTP/SSE + 对抗测试 |

---

## 三、文件清单

| 模块 | 目录 | 文件数 | ~行数 | Phase |
|------|------|--------|------|-------|
| Config | `./` | 1 | 120 | P5 |
| LLM | `llm/` | 2 | 100 | P1 |
| Env | `env/` | 4 | 450 | P1 |
| Skills | `skills/` | 3 | 250 | P1 |
| Planner | `planner/` | 3 | 200 | P1 |
| Memory | `memory/` | 3 | 150 | P1 |
| MCP | `mcp/` | 2 | 150 | P2 |
| Context | `context/` | 1 | 200 | P3 |
| Agent | `agent/` | 3 | 400 | P4 |
| Entry | `./` | 2 | 80 | P5 |
| **合计** | | **~25** | **~2100** | |

---

## 四、核心设计决策摘要

| 决策 | 方案 | 详见 |
|------|------|------|
| 全局单例 | LLM / Env / Skill / Planner / MCP / AgentService 均为单例 | [00-architecture.md](00-architecture.md) §二.3 |
| per-role 动态 | **仅 ContextService** 是 per-role 动态子类，owns memory | [08-mod-context.md](08-mod-context.md) |
| 角色管理 | AgentService 内置（原 Registry 合并），protocol 存 roles.db | [09-mod-agent.md](09-mod-agent.md) |
| Domain 命名 | 按目录分域（llm/env/memory/...），域名无 `.` | [00-architecture.md](00-architecture.md) §四.3 |
| 服务访问 | Service: `self.depends`; Command: `app.xxx`; `_apps` 仅最后手段 | [00-architecture.md](00-architecture.md) §四.1 |
| 存储方案 | 角色物理隔离 + CacheLayer → SQLiteProtocol | [01-data-models.md](01-data-models.md) §八 |
| LLM | 单一 LLMService + Router 管多模型 | [02-mod-llm.md](02-mod-llm.md) |
| 工具 | **EnvService 拥有 ToolCommand**，无需 ToolService | [03-mod-env.md](03-mod-env.md) |
| 配置 | config.py `SERVICE_DEFAULT` + agent.toml 覆盖 | [10-mod-config.md](10-mod-config.md) |
| MCP | 官方 mcp SDK | [07-mod-mcp.md](07-mod-mcp.md) |
| CLI/HTTP | fire + Starlette（复用 bollydog） | [10-mod-config.md](10-mod-config.md) §三 |

---

## 五、延后到 v2

| # | 问题 | 说明 |
|---|------|------|
| V1 | Reactive Network（Exchange 事件驱动） | v1 只做 Pipeline + Fan-out |
| V2 | 跨进程多环境（RemoteAgentTool） | v1 同进程 SpawnAgent |
| V3 | LLM fallback_models | litellm.Router 原生支持，v1 不做 |
| V4 | DuckDB 跨角色聚合分析 | 角色 DB 物理隔离后，ATTACH 多库 |
| V5 | 角色热更新 | 运行时修改 RoleDef 无需 deactivate |
| V6 | Artifact 系统 | 参考 DeerFlow browser preview |
| V7 | IM 渠道集成 | Telegram / Slack / Feishu |
