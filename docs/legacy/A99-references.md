# 附件 99 — 参考项目引用

> 与 A2 设计相关的外部开源项目和技术参考，持续更新。

---

## Multica — 开源托管式 Agent 平台

| 字段 | 信息 |
|------|------|
| **项目** | [multica-ai/multica](https://github.com/multica-ai/multica) |
| **定位** | 开源 managed agents platform，把 coding agent 变成真正的队友 |
| **名称来源** | 致敬 1960s Multics 操作系统的分时复用哲学——共享系统的"用户"既是人也是 AI agent |
| **文档** | [zread.ai/multica-ai/multica](https://zread.ai/multica-ai/multica) |

### 解决的问题

传统 AI coding agent 工作流的四个痛点：

| 痛点 | Multica 方案 |
|------|-------------|
| 重复 prompt 工程（每次手动复制上下文） | Agent 作为 first-class actor 在任务看板上接收 issue |
| 需要看护执行（盯着终端等完成） | 自主执行 + 状态上报 + 阻塞时自动反馈 |
| 零跨任务记忆（每次从零开始） | Compound Skills 积累团队知识，agent 自动引用 |
| 无统一可见性（多 agent 分散运行） | 统一看板展示所有 agent 的工作状态 |

### 核心概念

| 概念 | 说明 | 数据库表 |
|------|------|----------|
| **Workspace** | 多租户容器，资源按 workspace 隔离 | `workspace` |
| **Issue** | 工作单元（任务/bug/feature），可分配给人或 agent | `issue` |
| **Agent** | AI 工作者，有 profile、配置、运行时绑定、挂载技能 | `agent` |
| **Runtime** | agent 执行的计算环境（本地机器或云实例） | `agent_runtime` |
| **Daemon** | 本地后台进程，发现 CLI、注册运行时、轮询任务、流式传输结果 | （进程，非表） |
| **Skill** | 可复用的 Markdown 文档，执行前注入 agent 工作目录 | `skill` |
| **Squad** | agent/人组成的团队，leader agent 路由工作到对的成员 | `squad` |
| **Autopilot** | 定时/webhook 触发的自动化，创建 issue 并分配给 agent | `autopilot` |
| **Task** | 一次执行运行——排队、认领、启动、完成/失败的会话 | `agent_task_queue` |

### 关键架构决策

#### 1. 多态 Actor 模型（Polymorphic Actor Model）

所有"谁做了这件事"字段使用 `actor_type`（`member`/`agent`）+ `actor_id`。Agent 和人类在数据模型层面无区分——agent 可以创建 issue、发评论、出现在分配人选择器中。

#### 2. 分布式执行（Distributed Execution）

Server 永远不运行 agent 代码。Daemon 在本地机器轮询任务、在隔离目录启动 CLI 子进程、流式回传结果。代码和 API key 始终留在本地。

```
四层架构：
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────────┐
│ Clients  │ ──→ │  Server  │ ──→ │  Daemon  │ ──→ │  Agent CLIs  │
│ UI/状态   │     │ 持久化    │     │ CLI发现   │     │ LLM+工具执行  │
│ Zustand   │     │ 权限/编排  │     │ 工作目录   │     │ 零 Multica   │
│ TanStack  │     │ 实时广播   │     │ 结果流式   │     │ 感知         │
└─────────┘     └─────────┘     └─────────┘     └─────────────┘
```

#### 3. 复合技能（Compound Skills）

每个解决方案可以变成可复用的 Skill（Markdown 文档），执行前注入 agent 工作目录。Skills 是 provider-native：
- Claude Code 读取 `.claude/skills/`
- Codex 读取 `CODEX_HOME/skills/`
- 其他 CLI 各有约定

团队积累的知识，agent 自动引用。

### 技术栈

| 层 | 技术 | 用途 |
|----|------|------|
| 前端 | Next.js 16 (App Router), React 19, Tailwind CSS 4 | Web UI + Server Components |
| 桌面 | Electron + electron-vite | 原生桌面应用 |
| 状态管理 | Zustand + TanStack Query | 本地状态 + 服务端缓存 |
| 后端 | Go, Chi router, gorilla/websocket, sqlc | HTTP API + WebSocket |
| 数据库 | PostgreSQL 17 | 主存储（28 表，sqlc 生成） |
| 缓存 | Redis（可选） | 多节点事件中继 + 限流 |
| 构建 | pnpm workspaces + Turborepo | Monorepo 编排 |
| Agent 运行时 | 11 种 CLI | Claude Code, Codex, Copilot CLI, OpenClaw, OpenCode, Hermes, Gemini, Pi, Cursor Agent, Kimi, Kiro CLI |

### 部署模式

| 模式 | 说明 | Agent 执行位置 |
|------|------|---------------|
| Multica Cloud | 官方托管 multica.ai | 本地 daemon（代码不离开你的机器） |
| Self-Hosted | Docker Compose 自部署 | 同上 |

### 项目结构

```
multica/
├── apps/
│   ├── web/              # Next.js Web 应用
│   ├── desktop/          # Electron 桌面应用
│   └── docs/             # 文档站（Fumadocs）
├── packages/
│   ├── core/             # 共享业务逻辑、API hooks、Zustand stores
│   ├── views/            # 共享 React 视图组件
│   ├── ui/               # 基础 UI 组件（shadcn/ui）
│   ├── tsconfig/         # 共享 TS 配置
│   └── eslint-config/    # 共享 ESLint 配置
├── server/
│   ├── cmd/server/       # Go HTTP 服务入口
│   ├── cmd/multica/      # CLI 二进制（daemon, login, issue, agent…）
│   ├── cmd/migrate/      # 数据库迁移工具
│   ├── internal/         # 私有 Go 包（handler/daemon/service/events/realtime/middleware）
│   ├── migrations/       # SQL 迁移文件
│   └── pkg/db/           # sqlc 生成的数据库代码
├── e2e/                  # Playwright E2E 测试
├── scripts/              # 开发脚本
└── docker/               # Docker Compose 自托管
```

### 与 A2 的关联性

| Multica 特性 | A2 对应/启发 |
|-------------|-------------|
| Polymorphic Actor Model | A2 的 `RoleDef` 可扩展为支持 human/agent 统一分配 |
| Daemon + CLI 子进程模式 | 对应 A9 的"自托管 CLI 入口点"原语，支持进程级隔离 |
| Compound Skills（Markdown 注入） | A2 `SkillService` 已有类似机制（三级披露） |
| Squad（leader 路由） | A2 的 Orchestrator + `spawn_agent` 模式 |
| Autopilot（定时/webhook 触发） | 对应 A9 的"反射模式"，当前 A2 缺失 |
| Task Queue（排队/认领/执行） | A2 当前进程内同步，可参考引入异步任务队列 |
| 文件协议（工作目录隔离） | 对应 A9 的文件协议原语 |
| 11 种 Agent CLI 支持 | A2 的 `LLMService` 多模型支持 + 未来可扩展为多 CLI backend |

### 值得借鉴的设计

1. **Agent 不感知平台**：Agent CLI 完全不知道 Multica 的存在，只管执行 LLM + 工具。平台层面的编排、分配、监控全在 Server + Daemon 层完成。
2. **Skill 是 provider-native**：不自造 skill 格式，而是适配各 CLI 的原生约定。
3. **Server 永不执行 agent 代码**：安全边界清晰（对应 A4 "安全边界清楚了"）。
4. **所有概念都有持久化**：concept = table，没有"只存在于内存"的抽象。
