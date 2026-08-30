# A2 重设计 — 交集分析、设计决策、偏离记录、Registry Delta

> Issue: `20260826-a2-redesign`
> 本文是审计入口。**第 3 节「偏离与妥协」是本次设计中所有「bollydog 概念无法直接表达」的完整清单**，每条都写清：为什么不能、我用了什么方案、代价是什么。

---

## 1 交集分析（Issue Protocol Step 3）

`docs/REGISTRY.md` 此前不存在——本 issue 是 A2 的第一个 issue，也是初始构建。

| 问题 | 答案 | 动作 |
|------|------|------|
| 新 domain？ | 是，14 个全新 | 全量写入 REGISTRY 的 Services 表 |
| 既有 domain 里的新 Service？ | 不适用 | — |
| 既有 Service 上的新 Command？ | 不适用 | — |
| 修改既有 Command 签名？ | 不适用 | — |
| 新 Event / Subscriber？ | 是，24 个事件、7 个订阅绑定 | 写入 Events 表并核对拓扑无环 |
| 新 Protocol 或 Protocol 变更？ | 是，7 个新 ABC + 21 个实现 | 逐个验证「有假实现且可零改代码替换」 |

### 与 v1 的关系

v1 代码与文档已归档到 `legacy/a2-v1/` 与 `docs/legacy/`。v1 的失败原因（引自其自身的 `B1-design-audit.md` 与本次核查）：

| v1 问题 | 本次如何避免 |
|---------|-------------|
| `a2/env/` 整模块缺失，`config.py` import 即失败 | 先落 P5 接口契约再写代码；skeleton 阶段用 `_stub_` 占位而不是留空模块 |
| 文档层间无推导关系（有故事、有模块文档，但顺序图不完整、无接口契约） | 严格走 P0→P1→P2→P3→P4→P5，每层的产物都能追溯到上一层 |
| 结晶触发、Memory Flush、L4 持久化等文档与代码不一致 | 契约先行；REGISTRY.md 作为唯一现状快照，代码与之对齐 |
| 直接用 `AppService._apps` 跨服务捞实例（且 key 拼错导致静默失效） | 一律 `registry.resolve(destination)` 派发命令，或 `get_dependency('domain.alias')` 取已声明依赖 |
| per-role 动态子类化 `ContextService`，`_apps` 泄漏 | 不做动态服务子类。多智能体 = TOML 里多个 `AgentService` 段，生命周期由 `Bootstrap` 统一管 |
| 无测试 | P6 四层测试与 skeleton 同批交付 |

### v1 中值得保留的资产

- 「Env 拥有 Tool、没有独立 ToolService」→ 本次演进为**工具就是 Command**，比 v1 更彻底：连 ToolCommand 注册表都不需要，`registry.commands` 就是。
- 三级渐进披露（技能）→ 保留，并推广到工具分组（S23）。
- 角色物理隔离存储 → 保留为「每个 `AgentService` 实例有自己的 Protocol 链」。
- B 系列的推导方法论（故事 → 顺序图 → 接口契约）→ 本次即按此方法论执行，并与 bollydog SOP 的 P0–P8 完全对齐。

---

## 2 核心设计决策

### D-A 工具就是 Command

**决策**：A2 不定义 `Tool` 抽象。一个工具 = 一个 `BaseCommand` 子类。

- 工具名 ← `destination` 末段或注册别名
- 工具描述 ← 类的 `__doc__`
- 工具参数 JSON Schema ← `cls.model_json_schema()` 减去 `_ModelMixin` + `BaseCommand` 的基类字段
- 工具执行 ← `hub.dispatch` / `yield`
- 工具流式 ← Command 写成异步生成器
- 工具并行 ← `yield [cmd, cmd, …]`
- 工具鉴权 ← `hub.before` 钩子
- 工具可发现 ← `bollydog ls` 直接列出

这是本次设计里收益最大的一条：agentscope 的 `ToolBase` / `RegisteredToolFunction` / `FunctionTool` / `ToolResponse` / `ToolChunk` 五个抽象，在 A2 里全部由 `BaseCommand` + `StreamState` 承担。

### D-B 服务引用配置化，命令一律经 registry 构造

`AgentService.model_ref = 'model.chat'` 这样的字符串字段 + `registry.resolve(f'{app.model_ref}.Generate')(...)`。

收益：换模型、换沙箱、给不同智能体配不同工具集与知识库，全部只改 TOML；不需要任何工厂类或依赖注入容器。同时保证 `type(cmd).destination` 非空，`registry.resolve_app` 能正确绑定 `app` / `protocol` 全局（见偏离 D-05）。

### D-C 主链路用 Command，旁路用 Event

「必须拿到结果才能往下走」→ Command；「只是通知」→ Event。

落地结果：可观测、会话落盘、长期记忆抽取、指标统计全部从主链路摘除。主链路只剩 `Assemble → ListTools → Generate → CheckPermission → Invoke → AppendContext`，六步，全部有返回值语义。旁路失败不影响回答，回答失败也不丢旁路记录。

### D-D 两级会话状态

| | bollydog 全局 `Session` | `session.store` 的 Protocol |
|---|---|---|
| 语义 | 回合内运行态便签 | 跨回合持久档案 |
| 键 | `context:{sid}` / `turn:{sid}` / `summary:{sid}` | `session:{id}` / `turn:{sid}:{tid}` / `park:{sid}` / `evt:{sid}:{seq}` |
| 存储 | `CacheLayer → SQLiteProtocol` | `CacheLayer → SQLiteProtocol` 或 `RedisProtocol` |
| 谁写 | `Reply` / `Assemble` / `Compress` 主链路 | `Park` 命令 + `ReplyFinished` 订阅回调 |

键空间不重叠，职责不重叠。

### D-E `A2Service` 基类

```python
class A2Service(AppService, abstract=True):
    """A2 全域服务基类。只做三件事，不引入新范式。"""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(type(self), k): setattr(self, k, v)   # 解决偏离 D-06
        super().__init__(**kwargs)

    def event(self, name: str, **fields):
        """构造本服务的绑定事件类实例。解决偏离 D-05。"""
        return registry.resolve(f'{self.domain}.{self.alias}.{name}')(**fields)

    @staticmethod
    def source_of(message) -> dict:
        """订阅回调里取原始事件的字段。解决偏离 D-11。"""
        src = getattr(message, '_source', None)
        return src.model_dump() if src is not None else {}
```

这是普通的 `AppService` 子类，三个方法都只是既有 API（`registry.resolve` / `setattr` / `model_dump`）的封装，不是新概念。

### D-F 三种子命令调用姿势，按需选择

| 姿势 | 写法 | 用途 | 是否保留流式 |
|------|------|------|-------------|
| 编排 | `r = yield cmd` | 拿结果继续走 | 否 |
| 扇出 | `rs = yield [c1, c2]` | 并行 | 否 |
| 中继 | `await hub.dispatch(c)` + `async for x in c.state: yield x` | 需要把子命令的流冒泡出去 | 是 |

三者都是 bollydog 既有语义。中继是解决偏离 D-01 的手段。

### D-G 停放-恢复取代阻塞式人机交互

见偏离 D-03。

### D-H 单进程默认，横向扩展靠状态外置

见偏离 D-13。

---

## 3 偏离与妥协（**审计重点**）

以下 13 条是「agentscope 有、但 bollydog 没有对应原语」的全部条目。格式：**为什么不能 → 我怎么解决 → 代价**。

---

### D-01 深层事件流冒泡

**agentscope 的做法**：`Agent.reply_stream()` 是一个 async generator，模型增量、工具输出、子智能体输出都在同一个 Python 调用栈里 `yield` 上来，天然冒泡。

**bollydog 为什么不能直接做**：源码 `service/runner.py:80-107` 的 `_run_gen`——
- `yield value`（非 Command）→ `await message.state.put(value)`，只推入**当前**消息的 `StreamState`；
- `yield cmd`（Command）→ `self._submit(cmd)`，`HubService._submit` 是 `dispatch` + `await sub.state`，**只拿最终结果**。

子命令的流式内容在 `_submit` 处被吞掉，不会自动进入父命令的 `StreamState`。这是 bollydog 的既定语义（子命令是黑盒），不是 bug。

**解决方案（纯 bollydog API）**：命令中继模式。

```python
sub = registry.resolve('model.chat.Generate')(messages=..., tools=...)
await hub.dispatch(sub)                 # 只投递，不 await 结果
async for chunk in sub.state:           # StreamState.__aiter__（models/state.py:61）
    yield chunk                         # 冒泡到父命令的 StreamState
final = sub.state.result()
```

用到的三个 API——`hub.dispatch`、`StreamState.__aiter__`、`StreamState.result`——全部是 bollydog 公开语义，无一自造。

**代价**：
1. 同一个子命令不能既 `yield cmd` 又中继，编码时必须二选一。已在 `sequence.md` 每处显式标注。
2. 并行分支 `yield [c1, c2, c3]` 内部走 `asyncio.gather(*(self._submit(c) …))`，无法逐个中继。因此**并行工具调用不做实时流**，只在全部完成后逐条补发 `tool.result` 块。这与 agentscope 的行为存在可观察差异，已在图 3 记录为刻意取舍。
3. 中继期间父命令的 `expire_time` 仍在计时，长任务需要在 TOML 里调大 `COMMAND_EXPIRE_TIME` 或命令字段 `expire_time`。

---

### D-02 中断正在执行的回合

**agentscope 的做法**：`asyncio.CancelledError` 硬取消 + `_close_unfinished_tool_calls()` 收尾，或投递 `UserInterruptEvent`。

**bollydog 为什么不能直接做**：`HubService.run`（`service/app.py:76-83`）用 `self.add_future(self._process_and_complete(message))` 创建任务，**任务句柄不对外暴露**；`Queue` 也没有按 `iid` 取消的 API。唯一的超时机制是 `asyncio.wait_for(message(), timeout=message.expire_time)`，那是超时不是取消。

**解决方案（纯 bollydog API）**：协作式中断，两条互补路径。

1. **状态标记**：`Interrupt` 命令往全局 `session` 写 `turn:{session_id}.interrupted = True`。运行中的 `Reply` 在三个安全点（推理前、每个工具批之间、迭代末尾）读取该标记，命中则 `yield {'type':'reply.interrupted'}` 后 `return`，生成器正常收敛。
2. **入队拦截**：`ToolkitService` 在 `on_first_start` 用 `hub.before` 注册守卫。`CommandRunnerMixin._execute`（`service/runner.py:34-44`）对 before 钩子的非 `None` 返回值会**短路执行并直接 set_result**。因此已经排在 Queue 里、尚未开始跑的工具命令会被就地作废。

**代价**：
1. 中断不是瞬时的，粒度是「一个安全点」。正在跑的单个工具（比如一次 30 秒的 SQL）不会被打断，要等它自己结束。
2. 需要每个长循环命令自觉检查标记。这是约定而非强制，靠 `A2Service` 提供 `should_stop(session_id)` 辅助方法 + 代码评审保证。

**为什么这样反而更好**：硬取消会让工具留下半成品副作用（写了一半的文件、发了一半的请求）。协作式中断保证每个工具要么没开始、要么完整结束，与 agentscope 需要 `_close_unfinished_tool_calls` 补救相比，语义更干净。

---

### D-03 跨请求的人机交互挂起

**agentscope 的做法**：`RequireUserConfirmEvent` + agent 内部 park，`AgentState.has_awaiting_tool_calls()` 记录，下次 `reply()` 带着用户输入续跑。

**bollydog 为什么不能直接做**：命令的生命周期是「dispatch → 执行 → state 完成」。在 `__call__` 里 `await` 一个等待用户回答的 Future 在技术上可行，但会：
- 长期占住 `Queue` 的 in-flight 槽位（`QUEUE_MAX_SIZE` 默认 1000）；
- 要求 SSE 连接一直挂着，用户刷新页面即丢失；
- 进程重启后现场彻底消失，无法满足 S29。

**解决方案（纯 bollydog API）**：停放-恢复。

- 需要人类输入时：`yield R('session.store.Park')(session_id, turn_id, kind, pending)` 把全部现场（当前迭代数、上下文消息、待执行的工具调用）写进 `session.store` 的 Protocol，然后 `yield {'type':'require_user_confirm', …}` 并 `return`。生成器收敛，`StreamState` 收到 `None`，SSE 正常关闭。
- 用户答复时：新的 `Resume` 命令 `yield R('session.store.Unpark')(…)` 取回现场，然后中继（D-01 的模式）进一个带 `resume_state` 的新 `Reply`。

**代价**：
1. `Reply` 必须支持 `resume_state` 入参，内部有两条初始化路径（新回合 / 续跑）。已在 interfaces.md 中定为契约的一部分。
2. 前端要处理「SSE 正常结束但回合未完成」这一状态，靠块类型 `require_user_confirm` / `require_user_answer` 区分。
3. 停放态需要过期清理（`ParkedState.created_at` + 定时清扫），列入 skeleton 的 `_stub_` 清单。

**收益**：这个方案顺带解决了 S29（崩溃恢复）——停放态本来就在持久层，进程重启后 `OpenSession` 能报告 `resumable: true`。

---

### D-04 实例级洋葱中间件

**agentscope 的做法**：`Agent(middlewares=[RAGMiddleware(), Mem0Middleware(), TracingMiddleware()])`，7 个切点（`on_reply` / `on_reasoning` / `on_check_permission` / `on_acting` / `on_model_call` / `on_compress_context` / `on_system_prompt`），洋葱式嵌套，每个 agent 实例一条独立的链。

**bollydog 为什么不能直接做**：`CommandRunnerMixin` 只有 `_before` / `_after` 两个列表，注册在 `HubService` 上，是**全局单例**级别、**两个切点**、**平铺不嵌套**。没有按服务实例组装拦截链的机制。

**解决方案（纯 bollydog 概念，三层分治）**：

| agentscope 中间件类型 | A2 的落法 | 用的 bollydog 概念 |
|---------------------|----------|------------------|
| `on_check_permission` / 预算控制 / 中断守卫 | `hub.before` 钩子，回调内按 `message.destination` 与 `message.data['agent']` 判定作用域 | before 钩子 + 短路返回 |
| `TracingMiddleware` / 长期记忆写入 / 会话落盘 / 指标 | `BaseEvent` + Exchange 订阅，主题里带实例 alias（`agent.researcher.ReplyFinished`），天然实例级 | Event + subscribers |
| 大结果截断 / 归档 | `hub.after` 钩子 | after 钩子 |
| `RAGMiddleware`（static 模式）/ 系统提示改写 / 上下文压缩 | `Reply` 里显式 `yield` 的子命令，由 `AgentService` 的配置字段（`rag_mode` / `inject_runtime_state`）开关 | 子命令编排 |
| `RAGMiddleware`（agentic 模式）/ `Mem0Middleware`（agentic） | 注册成工具分组，交给模型自己决定何时调 | 工具即 Command |

**代价**：
1. 没有通用的「写一个类插到任意 agent 上」的插件点。新增横切能力要么改 `hub` 钩子（全局，需判定作用域），要么加订阅（异步旁路），要么改 `Reply` 的编排（同步链路）。**插件化程度低于 agentscope**。
2. `hub.before` / `hub.after` 是全局的，所有回调对每条消息都会跑一次。回调必须自己快速判定「与我无关」并返回 `None`，否则有性能损耗。约定：守卫回调第一行就做 destination 前缀判定。

**为什么刻意不做通用洋葱**：在 bollydog 里实现洋葱要包装 Command 的 `__call__`，会破坏「Command 是自描述的可执行单元」这一核心语义——`bollydog ls` 列出来的、HTTP 路由绑定的、`registry.resolve` 拿到的，都必须是那个原始类。用 Event 换来的是可观测、可重放、可跨进程，这与 bollydog 的设计取向一致。**这是本次设计中与 agentscope 差异最大的一处，明确记录为架构取向差异而非能力缺失。**

---

### D-05 事件必须携带 destination 才能被路由（陷阱）

**事实**：
- `Exchange.bind_subscriber_callbacks`（`service/exchange.py:57-62`）：`topic = type(message).destination; if not topic: return`。
- `RegistryService._register_commands`（`service/registry.py:38,41`）：对 `BaseEvent` 子类，若类体里**已有** `destination` 则 `continue`（跳过登记）；若没有，则生成一个带 `destination` 的**绑定子类**放进 `registry.commands`，**原始类的 `destination` 仍然是 `None`**。

**后果**：`from a2.agent.commands import ReplyFinished; await hub.emit(ReplyFinished(...))` 不会触发任何订阅者，而且**不报错**。这是最危险的静默失败。

**解决方案**：约定 + 自检。
- 约定：所有命令与事件实例一律经 `registry.resolve(destination)` 构造，封装在 `A2Service.event(name, **fields)` 和模块级 `a2.kernel.ref(dest)` 里。
- 自检：`A2Service.on_started` 遍历本类声明的 `emits: ClassVar[list[str]]`，逐个 `registry.resolve`，缺失即抛异常，启动失败。把静默失败提前成启动失败。

**代价**：多一层间接。收益是把一个隐蔽的运行期陷阱变成显式的启动期校验。

---

### D-06 TOML 自定义参数不会自动变成实例属性

**事实**：`AppService.create_from`（`models/service.py:47-60`）抽走五个框架键后，把剩余 `**conf` 传给 `cls(...)`；而 `BaseService.__init__(self, **kwargs)`（`models/base.py:117-118`）直接 `super().__init__()`，**丢弃 kwargs**。`service.config = conf` 保留了原始 dict，但不设属性。

**解决方案**：`A2Service.__init__` 遍历 kwargs，凡是类上已声明的同名属性就 `setattr`。这正是 bollydog spec 里「服务级自定义参数定义在 `__init__` 带默认值，TOML 只覆盖非默认值」的实现方式。

**代价**：无。属于按 spec 正确使用。

---

### D-07 运行时动态注册工具（MCP）

**agentscope 的做法**：`Toolkit.add_tool()` / `remove_tool()` 随时增删。

**bollydog 的现状**：`RegistryService.register()` 在 `Bootstrap.__init__` 里跑一次，扫描各服务的 `commands` 模块列表。

**解决方案**：`registry.commands` 是普通 `dict`，运行时写入合法。为每个远端 MCP 工具用 `type()` 动态生成 `BaseCommand` 子类并显式指定 `destination`：

```python
cls = type(f'mcp__{server}__{name}', (BaseCommand,), {
    'destination': f'mcp.gateway.mcp__{server}__{name}',
    'alias': f'mcp__{server}__{name}',
    '__doc__': tool['description'],
    '__annotations__': annotations,
    '__call__': _make_caller(server, name),
    **defaults,
})
registry.commands[cls.destination] = cls
```

**这与 bollydog 自己的做法完全一致**——`RegistryService._register_subscribers`（`service/registry.py:53-58`）就是用 `type()` 为每个订阅方法动态造 handler Command。所以这不是绕过框架，是复用框架自身的手法。

**代价（已知限制）**：动态注册的命令不会自动获得 HTTP 路由，因为 `HttpService.on_start` 已经跑完。MCP 工具只需被模型调用，不需要 HTTP 端点，因此当前无影响。若将来要给动态命令开 HTTP 路由，需要重启或给 `HttpService` 增加路由热加载——**记为框架级待办，不在本 issue 范围**。

---

### D-08 定时触发

**bollydog 的现状**：没有调度器。

**解决方案**：用 `mode.Service.timer`。bollydog 的 `BaseService` 就是 `mode.Service`（`models/base.py:102`），`HubService.run` 已经在用 `@mode.Service.task`（`service/app.py:76`），所以 `@mode.Service.timer(interval=30.0)` 是框架自带能力，不是外挂。

`SchedulerService` 放在 `a2/entrypoint/scheduler/`，与 `HttpService` / `SocketService` / `UdsService` 同列为**入口层**。

**关于「AppService 不主动 dispatch」这条硬约束**：该约束针对的是业务域服务。bollydog 自身的入口层服务全都主动 dispatch（`HttpHandler.__call__` → `hub.dispatch`；`Exchange._on_subscriber_done` → `hub.dispatch`；`HubService.run` → 消费 Queue）。把定时器归为入口，语义上与「HTTP 请求进来」等价——只是触发源从网络变成时钟。**记录为对约束的合理解释，而非违反。**

**代价**：调度器与业务在同一进程，进程挂了定时任务就停。生产环境需要外部 supervisor 保活，或把调度改为外部 cron 打 HTTP 入口。

---

### D-09 结构化领域对象 vs Command 只能用原语

**约束**：bollydog 硬性要求 Command 的字段与返回值只能是原语。但对话消息 `Msg`、内容块 `Block`、任务 `Task` 都是结构化对象。

**解决方案**：`BaseDomain`（bollydog 自带的 DDD 基类，`models/base.py:38`）定义领域模型，在 Command 边界一律 `model_dump()` / `model_validate()`。这是 bollydog spec 明写的做法（"If a domain model is needed downstream, convert it to dict"）。

**代价**：
1. 每轮上下文装配都有 dump/validate 开销。数百条消息量级下可忽略；若成瓶颈，`ContextService` 可缓存已装配好的 provider 格式（缓存键 = 消息列表哈希）。
2. 类型提示在 Command 签名上丢失（都是 `list` / `dict`）。靠 `interfaces.md` 的契约文档和领域模型的 Pydantic 校验兜底。

**收益**：Command 天然可序列化 → 可跨 HTTP/WS/UDS 三种入口、可跨进程投递、可被 `bollydog ls` 自描述、可直接落盘重放。这正是 dataagent2 需要的 trace/replay/eval 能力的基础。

---

### D-10 新增 Protocol ABC

**现状**：bollydog 在 `adapters/_base.py` 提供 KV / CRUD / Graph / File 四个 ABC。A2 需要 ChatModel / Embedding / TTS / VectorStore / Sandbox / Mcp / Permission 七个。

**判定**：`Protocol(BaseService)`（`models/protocol.py:6`）是通用适配器基类，只有 `adapter: Any` + `protocol`（嵌套）+ 生命周期。那四个 ABC 是 `adapters` 包里的便利封装，不是封闭集合——从 `Protocol` 直接派生新 ABC 是框架预期的扩展方式。

**自检标准**：每个新 ABC 都必须满足「生产用真实实现、测试换假实现、代码零改动」。interfaces.md 第 5 节的实现矩阵逐行满足（`ScriptedChatProtocol` / `HashEmbeddingProtocol` / `InMemoryVectorProtocol` / `FakeSandboxProtocol` / `FakeMcpProtocol`）。

**代价**：无。属于框架预期扩展。**记录在此是为了让审计者能明确判断这是「扩展」而非「造概念」。**

---

### D-11 订阅回调拿不到强类型事件

**事实**：`RegistryService._register_subscribers` 生成的 handler Command 把触发事件挂在 `self._source` 上（私有属性），回调签名统一是 `async def m(self, message) -> …`，`message` 是 handler 实例，`message._source` 才是原始事件。

**解决方案**：`A2Service.source_of(message) -> dict` 统一转 dict，回调内按字典键取值。

**代价**：回调内无 IDE 类型提示。类型正确性由派发侧的事件类 Pydantic 定义保证，加上订阅回调的单元测试覆盖。

---

### D-12 订阅者失败静默

**事实**：`Exchange._on_subscriber_done`（`service/exchange.py:46-55`）整体包在 `try/except` 里，异常只打日志。

**解决方案**：
1. 约定订阅回调必须自己捕获异常并返回 dict（包含 `{'ok': False, 'error': …}`），不向外抛。
2. `TraceService` 订阅 `#`，订阅回调本身产生的命令也会进 span，失败可从追踪树看出来。
3. skeleton 里给订阅回调加统一的 `@safe_subscriber` 装饰器（普通 Python 装饰器，不是框架概念）。

**代价**：仍然不会阻断主链路——但这正是 Event 的语义（"Fail independently"），符合设计意图。

---

### D-13 多副本部署下的事件广播

**事实**：`Exchange.match` 查的是 `registry.subscribers`（`service/registry.py:18`），这是**本进程**的注册表。跨进程的事件广播 bollydog 没有提供。

**影响**：多副本部署时，副本 A 上发生的 `ReplyFinished` 不会触发副本 B 上的订阅者。

**解决方案（分层降级）**：
1. **默认形态**：单进程多协程。智能体负载是 IO 密集型（等模型、等工具、等数据库），asyncio 单进程足以支撑。这是 A2 的推荐部署形态。
2. **需要横向扩展时**：把 `Session`、`session.store`、`tool.toolkit`、`plan.notebook` 的 Protocol 换成 `RedisProtocol`，实现状态共享；入口层做**会话粘性路由**（同一 `session_id` 恒定路由到同一副本），保证一个会话的事件闭环在单副本内完成。
3. **跨副本事件**：超出 bollydog 当前能力。**明确记为框架级已知限制**。若 dataagent2 必须要跨副本事件（例如管理端要实时看到所有副本的回合完成），届时的方案是新增一个 `RedisPubSubProtocol` 并让 `TraceService` 双写，而不是改 `Exchange`。

**代价**：横向扩展能力受限。已在 REGISTRY.md 的「已知限制」区标注。

---

### 偏离清单小结

| 编号 | 主题 | 性质 | bollydog 内可解 |
|------|------|------|----------------|
| D-01 | 深层事件流冒泡 | 语义差异 | ✅ 中继模式 |
| D-02 | 中断运行中回合 | 能力缺失 | ✅ 协作式中断 |
| D-03 | 跨请求人机交互 | 生命周期不匹配 | ✅ 停放-恢复 |
| D-04 | 实例级洋葱中间件 | **架构取向差异** | ⚠️ 三层分治替代，插件化程度低于 agentscope |
| D-05 | 事件 destination 绑定 | 陷阱 | ✅ registry.resolve + 启动自检 |
| D-06 | TOML 参数注入 | 用法 | ✅ `__init__` setattr |
| D-07 | 运行时动态工具 | 能力缺失 | ✅ `type()` 造类写 registry（框架自用手法） |
| D-08 | 定时触发 | 能力缺失 | ✅ `mode.Service.timer` + 归入入口层 |
| D-09 | 结构化对象边界 | 硬约束 | ✅ `BaseDomain` + `model_dump` |
| D-10 | 新增 Protocol ABC | 扩展 | ✅ 子类化 `Protocol` |
| D-11 | 订阅回调类型 | 人机工效 | ✅ `source_of` 辅助 |
| D-12 | 订阅者失败静默 | 语义使然 | ✅ 约定 + 追踪兜底 |
| D-13 | 多副本事件广播 | **框架级限制** | ❌ 单进程 + 粘性路由规避 |

**只有两条不是完全解决**：D-04（架构取向差异，可用但插件化弱）与 D-13（框架级限制，靠部署形态规避）。其余 11 条全部在 bollydog 既有概念内闭合。

---

## 4 已知限制清单（进入 REGISTRY）

| 编号 | 限制 | 影响范围 | 规避方式 |
|------|------|---------|---------|
| L1 | 并行工具批次无实时流 | 前端并行工具卡片延迟出现 | 需要实时时改用串行批 |
| L2 | 中断粒度为「安全点」 | 单个长工具无法中途打断 | 工具自身实现 timeout |
| L3 | 动态注册的命令无 HTTP 路由 | MCP 工具不能直接 HTTP 调用 | 经 `tool.toolkit.Invoke` 转发 |
| L4 | `hub.before/after` 全局生效 | 钩子多时有常数开销 | 钩子首行做 destination 前缀判定 |
| L5 | 跨副本事件不可见 | 多副本部署 | 单进程 + 会话粘性路由 + Redis 共享状态 |
| L6 | 无通用中间件插件点 | 第三方扩展需改编排或加订阅 | 提供 Event 订阅与工具分组两个官方扩展位 |
| L7 | 停放态需定时清扫 | 长期不恢复的停放态占存储 | `SchedulerService` 定时清扫（skeleton 中为 `_stub_`） |

---

## 5 Registry Delta

```
+ service AgentService domain=agent protocol=CacheLayer→SQLiteProtocol depends=context.default,tool.toolkit,session.store
+ service ChatModelService domain=model protocol=ChatModelProtocol depends=credential.vault
+ service EmbeddingService domain=model protocol=CacheLayer→EmbeddingProtocol
+ service ContextService domain=context protocol=none depends=skill.hub,plan.notebook
+ service MemoryService domain=memory protocol=VectorStoreProtocol depends=model.embed
+ service ToolkitService domain=tool protocol=CacheLayer→PermissionProtocol depends=workspace.local,mcp.gateway,credential.vault
+ service McpService domain=mcp protocol=McpProtocol
+ service SkillService domain=skill protocol=CacheLayer→SQLiteProtocol
+ service KnowledgeService domain=knowledge protocol=VectorStoreProtocol depends=model.embed
+ service PlanService domain=plan protocol=CacheLayer→SQLiteProtocol
+ service SessionService domain=session protocol=CacheLayer→SQLiteProtocol
+ service WorkspaceService domain=workspace protocol=SandboxProtocol
+ service CredentialService domain=credential protocol=CacheLayer→SQLiteProtocol
+ service TeamService domain=team protocol=MemoryProtocol
+ service TraceService domain=observe protocol=SqlAlchemyProtocol
+ service SchedulerService domain=schedule kind=entrypoint protocol=CacheLayer→SQLiteProtocol
+ command Reply dest=agent.{alias}.Reply gen=true
+ command Resume dest=agent.{alias}.Resume gen=true
+ command Interrupt dest=agent.{alias}.Interrupt
+ command Observe dest=agent.{alias}.Observe
+ command Spawn dest=agent.{alias}.Spawn gen=true
+ command Generate dest=model.{alias}.Generate gen=true
+ command CountTokens dest=model.{alias}.CountTokens
+ command Embed dest=model.embed.Embed
+ command Assemble dest=context.default.Assemble
+ command AppendContext dest=context.default.AppendContext
+ command Compress dest=context.default.Compress
+ command ListTools dest=tool.toolkit.ListTools
+ command Invoke dest=tool.toolkit.Invoke gen=true
+ command CheckPermission dest=tool.toolkit.CheckPermission
+ command ActivateGroup dest=tool.toolkit.ActivateGroup
+ command ConnectServer dest=mcp.gateway.ConnectServer
+ command DisconnectServer dest=mcp.gateway.DisconnectServer
+ command ListServers dest=mcp.gateway.ListServers
+ command ListSkills dest=skill.hub.ListSkills
+ command MatchSkills dest=skill.hub.MatchSkills
+ command LoadSkill dest=skill.hub.LoadSkill
+ command InstallSkill dest=skill.hub.InstallSkill
+ command IngestDocument dest=knowledge.base.IngestDocument gen=true
+ command UpsertChunks dest=knowledge.base.UpsertChunks
+ command Search dest=knowledge.base.Search
+ command DeleteDocument dest=knowledge.base.DeleteDocument
+ command Recall dest=memory.longterm.Recall
+ command Remember dest=memory.longterm.Remember
+ command Forget dest=memory.longterm.Forget
+ command CreatePlan dest=plan.notebook.CreatePlan
+ command UpdateTask dest=plan.notebook.UpdateTask
+ command ListTasks dest=plan.notebook.ListTasks
+ command OpenSession dest=session.store.OpenSession
+ command SaveTurn dest=session.store.SaveTurn
+ command LoadSession dest=session.store.LoadSession
+ command ListSessions dest=session.store.ListSessions
+ command DeleteSession dest=session.store.DeleteSession
+ command Park dest=session.store.Park
+ command Unpark dest=session.store.Unpark
+ command AppendEvent dest=session.store.AppendEvent
+ command ReplayEvents dest=session.store.ReplayEvents gen=true
+ command ReadPath dest=workspace.{alias}.ReadPath group=basic
+ command WritePath dest=workspace.{alias}.WritePath group=edit
+ command ListPath dest=workspace.{alias}.ListPath group=basic
+ command GlobSearch dest=workspace.{alias}.GlobSearch group=basic
+ command GrepSearch dest=workspace.{alias}.GrepSearch group=basic
+ command EditPath dest=workspace.{alias}.EditPath group=edit
+ command RunCommand dest=workspace.{alias}.RunCommand group=exec gen=true
+ command RunPython dest=workspace.{alias}.RunPython group=exec gen=true
+ command StoreArtifact dest=workspace.{alias}.StoreArtifact
+ command ReadArtifact dest=workspace.{alias}.ReadArtifact group=basic
+ command GrepArtifact dest=workspace.{alias}.GrepArtifact group=basic
+ command PutCredential dest=credential.vault.PutCredential
+ command GetCredential dest=credential.vault.GetCredential
+ command ListCredentials dest=credential.vault.ListCredentials
+ command DeleteCredential dest=credential.vault.DeleteCredential
+ command Sequential dest=team.room.Sequential gen=true
+ command Fanout dest=team.room.Fanout gen=true
+ command Broadcast dest=team.room.Broadcast
+ command JoinTopic dest=team.room.JoinTopic
+ command LeaveTopic dest=team.room.LeaveTopic
+ command QueryTrace dest=observe.tracer.QueryTrace
+ command ExportTrace dest=observe.tracer.ExportTrace
+ command ListTraces dest=observe.tracer.ListTraces
+ command AskHuman dest=agent.{alias}.AskHuman group=basic
+ command PresentFiles dest=agent.{alias}.PresentFiles group=basic
+ event ReplyStarted source=agent.{alias} subscribers=observe.tracer
+ event IterationCompleted source=agent.{alias} subscribers=observe.tracer
+ event ReplyFinished source=agent.{alias} subscribers=session.store,memory.longterm,observe.tracer
+ event ReplyInterrupted source=agent.{alias} subscribers=session.store,observe.tracer
+ event ReplyParked source=agent.{alias} subscribers=session.store,observe.tracer
+ event ModelCalled source=model.{alias} subscribers=observe.tracer
+ event ModelFailed source=model.{alias} subscribers=observe.tracer
+ event ToolInvoked source=tool.toolkit subscribers=observe.tracer
+ event ToolFailed source=tool.toolkit subscribers=observe.tracer
+ event GroupActivated source=tool.toolkit subscribers=observe.tracer
+ event ContextCompacted source=context.default subscribers=observe.tracer
+ event ServerConnected source=mcp.gateway subscribers=tool.toolkit,observe.tracer
+ event ServerLost source=mcp.gateway subscribers=tool.toolkit,observe.tracer
+ event SkillActivated source=skill.hub subscribers=observe.tracer
+ event SkillCatalogChanged source=skill.hub subscribers=observe.tracer
+ event DocumentIngested source=knowledge.base subscribers=observe.tracer
+ event PlanCreated source=plan.notebook subscribers=observe.tracer
+ event TaskUpdated source=plan.notebook subscribers=observe.tracer
+ event PlanCompleted source=plan.notebook subscribers=observe.tracer
+ event ArtifactStored source=workspace.{alias} subscribers=observe.tracer
+ event CredentialMissing source=credential.vault subscribers=observe.tracer
+ event MessageBroadcast source=team.room subscribers=agent.*
+ event RoundCompleted source=team.room subscribers=observe.tracer
+ event JobDispatched source=schedule.runner subscribers=observe.tracer
+ protocol ChatModelProtocol type=abc impls=OpenAI,Anthropic,DashScope,Gemini,Ollama,DeepSeek,LiteLLM,Scripted
+ protocol EmbeddingProtocol type=abc impls=OpenAI,DashScope,Ollama,Hash
+ protocol TTSProtocol type=abc impls=OpenAI,DashScope
+ protocol VectorStoreProtocol type=abc impls=Qdrant,Milvus,PgVector,InMemory
+ protocol SandboxProtocol type=abc impls=Local,Docker,K8s,Ssh,Fake
+ protocol McpProtocol type=abc impls=Stdio,Http,Fake
+ protocol PermissionProtocol type=abc impls=Rule
+ config agent.toml sections=17
```

---

## 6 Design Audit 自检（SOP Quality Gate）

| Phase | 审计点 | 通过标准 | 自评 |
|-------|--------|---------|------|
| 0 | 故事里零类名 | 非技术人员能看懂 | ✅ S01–S29 无任何技术名词 |
| 1 | 每个主体单一职责 | 一句话说清 | ✅ 14 个域各一句话 |
| 2 | Command / Event 区分清楚 | 祈使 vs 过去式 | ✅ 命令全祈使、事件全过去式 |
| 3 | Protocol 组合合理 | 测试能换假实现 | ✅ 实现矩阵逐行有测试列 |
| 4 | 顺序图箭头精确 | 方法 + 参数 + 返回类型 | ✅ 18 张图，每箭头标注完整 |
| 5 | 签名与故事对应 | 无凭空捏造的接口 | ✅ 接口提取索引可反查 |
| 6 | 行为测试全通过 | 无外部依赖可运行 | ⏳ 见 `tests/`，与 skeleton 同批交付 |
| 7 | 骨架端到端可跑 | CLI 可验证 | ⏳ 见下一节 |

---

## 7 Walking Skeleton 说明（P7）

见 `docs/issues/20260826-a2-redesign/skeleton.md`。
