# 10 — Bollydog 集成约定

## Context

本文档建立 A2 项目对 bollydog 框架的集成约定，确保所有设计文档中的伪代码引用的 API 真实存在。本文档是审计 A2 设计文档后的修正产物。

## 1. 服务访问模式

### 1.1 Command 内部访问服务

bollydog 的 `_with_context(message)` 会从 `message.destination` 解析出所属 `AppService`，推入 `globals.app`。**Command 应通过 globals.app 访问其所属服务的方法。**

```python
from bollydog.globals import app, protocol, session, message

class MyCommand(BaseCommand):
    async def __call__(self):
        result = app.do_something()           # ✅ 当前 app 的业务方法
        data = await protocol.get('key')      # ✅ 当前 app 的 protocol
        await session.set('key', data)        # ✅ 全局 session
```

### 1.2 Command 访问其他服务（跨域）

bollydog 的 `AppService` 没有 `resolve(key)` 实例方法。正确方式是通过 `AppService._apps` 类属性查找：

```python
from bollydog.models.service import AppService

class CrossDomainCommand(BaseCommand):
    async def __call__(self):
        llm = AppService._apps['a2.LLMService']           # ✅ 正确
        result = await llm.chat(messages=..., capability='fast')

        # ❌ 错误 — 以下 API 不存在：
        # llm = app.resolve('a2.LLMService')
```

### 1.3 Service 访问 Service（通过 depends）

Service 之间通过 `depends` 注入的引用相互访问。`load_from_config` 将 depends 字符串解析为实例，通过 `add_dependency` 注入。注入后通过 `self._children` 或显式属性引用。

```python
class ContextService(AppService):
    depends = ['a2.MemoryService', 'a2.LLMService']

    async def on_started(self):
        # depends 注入后，通过 _children 列表或在 on_started 中手动赋值
        for dep in self._children:
            if hasattr(dep, 'get_context'):
                self._memory = dep
            elif hasattr(dep, 'chat'):
                self._llm = dep
```

## 2. Domain 与 _apps 键命名约定

### 2.1 _apps 键的计算规则

```python
# bollydog/models/service.py → AppService.__init__
key = f'{self.domain}.{self.alias}'
AppService._apps[key] = self
```

### 2.2 A2 的命名约定

**规则：`domain` 必须设为 TOML 节名去掉最后一段（alias）后的前缀。**

| TOML 节名 | domain | alias | _apps 键 |
|-----------|--------|-------|----------|
| `"a2.LLMService"` | `a2` | `LLMService` | `a2.LLMService` |
| `"a2.llm.fast.FastLLMService"` | `a2.llm.fast` | `FastLLMService` | `a2.llm.fast.FastLLMService` |
| `"a2.env.local.LocalToolService"` | `a2.env.local` | `LocalToolService` | `a2.env.local.LocalToolService` |
| `"a2.memory.l0.L0MetaRulesService"` | `a2.memory.l0` | `L0MetaRulesService` | `a2.memory.l0.L0MetaRulesService` |

### 2.3 depends 引用

`depends` 列表中的字符串**必须与 `_apps` 键完全匹配**。

```python
class ToolService(AppService):
    domain = 'a2'
    alias = 'ToolService'
    depends = [
        'a2.env.local.LocalToolService',     # ✅ 精确匹配 _apps 键
        'a2.env.docker.DockerToolService',
        'a2.env.ssh.SSHToolService',
    ]

class LocalToolService(AppService):
    domain = 'a2.env.local'                  # ← 必须与 TOML 键前缀一致
    alias = 'LocalToolService'
```

## 3. Tool 不经过 Hub dispatch

Tool 继承 BaseCommand 但设置 `abstract=True`，其 `__call__` 不在具体子类 `__dict__` 中，因此不会被 `_load_commands` 注册到 `BaseService.registry`。

```python
class Tool(BaseCommand, abstract=True):
    # abstract=True → __init_subclass__ 跳过 destination 设置
    async def __call__(self, **kwargs) -> str:
        return await self.execute(**kwargs)

class ReadTool(Tool):
    # 只定义 execute，不重写 __call__
    # '__call__' not in ReadTool.__dict__ → 不会被 _load_commands 注册
    async def execute(self, path: str, **kw) -> str: ...
```

Tool 由 `ToolService.execute()` 直接调用，不经过 Hub dispatch。

## 4. AgentCommand 的 globals 模式

AgentCommand 是 async generator，通过 Hub 的 `_run_gen` 执行。**不应通过构造函数注入 service**，而是使用 globals。

```python
from bollydog.globals import app
from bollydog.models.service import AppService

class AgentCommand(BaseCommand):
    async def __call__(self):
        # 通过 globals.app 访问所属的 AgentService
        system_prompt = app.build_system_prompt()

        # 通过 AppService._apps 访问其他服务
        planner = AppService._apps['a2.PlannerService']
        if planner.use_planning:
            task_list = yield PlanCommand(goal=self.goal, run_id=message.trace_id)
        ...
```

## 5. _run_gen 的精确语义

Hub._run_gen 使用 `gen.asend(feedback)` 实现双向通信：

```
gen = message()
feedback = None
while True:
    value = gen.asend(feedback)       # 发送上次结果，获取下一个 yield 值
    if isinstance(value, Message):    # yield SubCommand
        sub = dispatch(value)
        feedback = await sub.state    # 子命令结果作为下次 feedback
    elif isinstance(value, (list, tuple)):  # yield [cmd1, cmd2]
        feedback = gather(...)        # 并行结果列表
    else:                             # yield dict/value
        message.state.put(value)      # 流式输出
        feedback = None               # 普通 yield 无 feedback
```

这意味着：
- `task_list = yield PlanCommand(...)` → task_list 接收 PlanCommand 的返回值 ✅
- `yield {'type': 'text', ...}` → 流式输出，无返回值 ✅
- `[r1, r2] = yield [Cmd1(), Cmd2()]` → 并行执行，接收结果列表 ✅

## 6. QoS 注意事项

bollydog 默认 `DEFAULT_QOS = 1`，意味着 Command 默认走队列。对于流式 AgentCommand，应显式设置 `qos = 0` 避免队列开销：

```python
class AgentCommand(BaseCommand):
    qos: int = 0  # 流式命令不走队列
```
