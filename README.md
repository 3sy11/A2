# A2 — AI Agent Framework

基于 [bollydog](https://github.com/3sy11/bollydog) 的事件驱动智能体框架。
能力对齐 AgentScope、支撑 dataagent2 是设计目标；当前已完成 P0 对话保存、恢复和事件重放基础，仍是逐场景交付中的框架。

## 设计原则

- **所有基础概念来自 bollydog**：Command、Event、AppService、Protocol、Hub、Exchange、Session
- **工具就是 Command**（D-A）：无独立 Tool 抽象，使用 bollydog Registry 的 Command 索引
- **主链路 Command，旁路 Event**（D-C）：可观测/落盘/记忆抽取走订阅
- **偏离记录**：见 [`docs/issues/20260826-a2-redesign/notes.md`](docs/issues/20260826-a2-redesign/notes.md) §3

## 快速开始

```bash
uv sync
uv run pytest tests/ -v
ENTRYPOINT_HTTP_ENABLED=1 uv run bollydog service --config config/agent.toml
```

## 文档

| 文档 | 内容 |
|------|------|
| [stories.md](docs/issues/20260826-a2-redesign/stories.md) | 29 场景、14 领域、Command/Event |
| [interfaces.md](docs/issues/20260826-a2-redesign/interfaces.md) | 接口契约、领域模型 |
| [notes.md](docs/issues/20260826-a2-redesign/notes.md) | 偏离与妥协（审计入口） |
| [REGISTRY.md](docs/REGISTRY.md) | 当前框架设计、bollydog 能力边界、实现状态与目标差距 |
| [skeleton.md](docs/issues/20260826-a2-redesign/skeleton.md) | Walking skeleton 说明 |

## 架构

```
用户 → HTTP/SSE/CLI → agent.Reply (ReAct)
                         ├→ context.Assemble
                         ├→ model.Generate
                         ├→ tool.Invoke → workspace.* / agent.*
                         └→ Event → observe / session / memory
```
