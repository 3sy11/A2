# 12 — Bollydog 修改计划

## 结论：零修改

bollydog 核心代码**零修改**。TerminalProtocol 及其 backend 实现放在 A2 的 `tools/protocol.py` 中，先做模式验证。验证通过后再考虑提升到 bollydog 的 `adapters/` 层。

## 为什么不直接放在 bollydog

1. **模式验证优先** — TerminalProtocol 是新概念（Protocol 不只做存储，还做执行），先在 A2 中验证可行性
2. **接口可能迭代** — `execute`/`read_file`/`write_file`/`upload`/`download` 这 5 个方法是否合理，需要实际使用后才能确定
3. **bollydog 保持稳定** — 不因为 A2 的设计探索而频繁修改基础框架

## A2 中的实现位置

```
A2/tools/
├── protocol.py    ← TerminalProtocol + LocalProtocol + DockerProtocol + SSHProtocol
├── service.py     ← ToolService(AppService) 门面
├── tool.py        ← Tool(BaseCommand) 基类
└── builtin.py     ← 11 个内置工具
```

TerminalProtocol 继承 `bollydog.models.protocol.Protocol`，复用：
- `on_start()` / `on_stop()` — 生命周期（子类实现）
- `__aenter__` / `__aexit__` — 上下文管理（基类实现）
- `self.adapter` — 底层资源引用（子类赋值）

## 未来提升路径

模式验证通过后，将 `tools/protocol.py` 移动到 `bollydog/adapters/terminal.py`：
- TerminalProtocol 成为与 KVProtocol/FileProtocol 同级的适配器
- A2 的 `tools/protocol.py` 改为从 bollydog 导入
- 其他项目也可以复用 TerminalProtocol

## bollydog 完全可复用的机制（不变）

| 机制 | A2 复用方式 |
|------|------------|
| `BaseCommand.__init_subclass__` | Tool 继承 BaseCommand，自动注册到 MessageRegistry |
| `BaseCommand.__call__` | Tool 重定义 `__call__` 为直接可调用 |
| `BaseCommand.is_async_gen` | 自动检测 `__call__` 是否为 async generator |
| Hub `before`/`after` 钩子 | Agent 生命周期钩子的底层支撑 |
| Hub `_execute` | before → runner → after 流程 |
| Hub `_run_gen` `else` 分支 | Agent yield dict 已能正确处理 |
| Exchange topic 匹配 | Agent 事件发布（如 SubagentEvent） |
| Session KV + CompositeProtocol | L4 SessionArchive（内存缓存 + 文件持久化） |
| FileProtocol | L3 TaskSkills（技能以 .md 文件存储） |
| Queue qos=1 | 长任务的可靠队列 |
| Protocol `_build_protocol` 嵌套 | LayeredMemory L0-L4 链式组装 + TerminalProtocol 多环境 |
| `AppService._load_commands` | 动态导入 agent 命令模块 |
| `AppService.resolve_app` | 基于 destination 的路由 |
| `Protocol` 基类 | TerminalProtocol 继承，复用生命周期和 adapter 模式 |

## A2 侧依赖

```toml
# A2/pyproject.toml
[project]
dependencies = [
    "bollydog>=0.1.0",
]

[project.optional-dependencies]
llm = [
    "openai>=1.0.0",
    "anthropic>=0.20.0",
]
mcp = [
    "mcp>=1.0.0",
]
all = [
    "openai>=1.0.0",
    "anthropic>=0.20.0",
    "mcp>=1.0.0",
    "tiktoken>=0.5.0",
]
```
