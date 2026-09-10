# Progress

## 2026-09-10
- 完成 bollydog 新提交与 A2 旧调用点对照。
- 确认用户工作区只有未跟踪的 `docs/A3-data-agent-reference.md`，本任务不触碰。
- 配置已迁移为 `domain.alias`、显式 `module`、独立 Protocol 引用和 `subscribe`。
- 已将 5 组订阅方法迁移为 commands 模块中的 `BaseEvent.__call__`。
- 已将现有事件发布改为 `hub.emit(topic=..., source=...)`。
- Registry 已改用 `all_commands()` / `add_command()`；工具 schema 改用
  `BaseCommand.describe()`。
- 删除 A2 重复的 TOML 注入、emits 自检、`_source` 和
  `safe_subscriber` 补丁；保留 relay、chunk、回合级 should_stop。
- 更新 README、REGISTRY 和 redesign 活跃文档。
- Bootstrap 验证：23 services / 73 commands / 40 event bindings。
- 最终测试：8 passed；compileall 与 `git diff --check` 均通过。
- 去掉 `a2/session/service.py` 文件末尾多余空行。
