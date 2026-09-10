# A2 适配 bollydog 新契约

目标：仅使用 bollydog 已有概念，将 A2 迁移到当前配置、事件、取消和 Registry API，并保持现有领域行为。

## 阶段
- [x] 1. 配置迁移：`domain.alias`、显式 `module`、独立 Protocol、`subscribe`
- [x] 2. 事件迁移：订阅行为改为 `BaseEvent.__call__`，发布改用 `hub.emit`
- [x] 3. Registry/schema 迁移：公开 API 与 `BaseCommand.describe()`
- [x] 4. Kernel 精简：删除已由 bollydog 提供的补丁
- [x] 5. 测试和静态检查
- [x] 6. 同步活跃设计文档

## 成功标准
- `Bootstrap(config="config/agent.toml")` 成功构建全部服务。
- A2 全量测试通过。
- 源码不再使用 `subscribers`、`registry.commands`、`message._source`。
- A2 不复制 bollydog 已提供的配置注入和 Command schema 逻辑。

## 约束
- 不修改 bollydog。
- 不引入新框架抽象；保留 A2 的 ReAct、chunk、relay、回合级中断等领域能力。
- 不修改用户已有未跟踪文件 `docs/A3-data-agent-reference.md`。

## 错误记录
- `session-catchup.py` 不存在于 skill 目录，无法执行恢复脚本；已通过 `git status` 确认当前仅有用户未跟踪文档。
- 新增订阅 Event 测试首次失败：Event 内 `hub.dispatch(SaveTurn)` 在 ExecuteService
  测试模式没有 Queue consumer。改用 bollydog 原生 handoff（Event 返回
  `SaveTurn` Command）后通过。
