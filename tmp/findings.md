# Findings

- bollydog 配置键现为 `domain.alias`，每项要求显式 `module`。
- Protocol 必须作为独立服务配置，AppService 通过字符串 `protocol = "adapters.alias"` 引用。
- 订阅字段为 `subscribe`，值是当前服务 commands 模块内的 Event 类名，不再是服务方法名。
- Event 自身执行订阅逻辑；来源位于 `event.data["events"][-1]`。
- `registry.all_commands()`、`add_command()`、`resolve()` 替代直接访问 `registry.commands`。
- `BaseCommand.describe()` 已提供工具 JSON Schema。
- `hub.cancel(iid)` 仅是单命令取消；A2 的 session 级协作中断仍需保留。
- 子命令深层流仍未原生冒泡；`relay_gen` 必须保留。
