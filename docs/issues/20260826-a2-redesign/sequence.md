# A2 重设计 — 端到端顺序图（P4）

> Issue: `20260826-a2-redesign`
> 规则：每条箭头 = 一次方法调用，标注 `方法名(参数: 类型) → 返回类型`。Command 一律展开成 `CommandName(field: type, …) → ReturnType`。箭头对不上就是接口缺失。
> 所有 Command 字段与返回值只允许 `str / int / float / bool / list / dict / None` 及其联合。

## 通用约定（贯穿全部图）

| 记号 | 含义 |
|------|------|
| `hub.dispatch(cmd) → cmd` | 投入 Queue，立即返回；`cmd.state` 为 `Future` 或 `StreamState` |
| `hub.execute(cmd) → Any` | `dispatch` + `await cmd.state` |
| `hub.emit(evt) → None` | 派发 Event，Exchange 在其 state 完成时触发订阅者 |
| `yield cmd` | `_run_gen` 拦截 → `_submit` → 结果经 `gen.asend()` 回传 |
| `yield [cmd, …]` | `_run_gen` 拦截 → `asyncio.gather` 并行扇出，结果 list 回传 |
| `yield value` | 非 Command 值 → `state.put(value)`，即流式输出一块 |
| `R(dest)` | `registry.resolve(dest)` —— 取回带 `destination` 的绑定类 |
| `S` | bollydog 全局 `session`（回合内便签，KV） |

**服务引用配置化**：`app.model_ref` / `app.context_ref` / `app.tool_ref` / `app.session_ref` / `app.plan_ref` 都是 TOML 注入的字符串（如 `'model.chat'`）。子命令一律 `R(f'{app.model_ref}.Generate')(...)` 构造，因此 `type(cmd).destination` 非空，`registry.resolve_app` 能正确绑定 `app` / `protocol` 全局。

**横切上下文**：`trace_id` / `parent_span_id` 由 `BaseCommand.model_post_init` 从 `message` 全局自动继承。业务级横切（`session_id` / `user_id` / `turn_id` / `agent`）放进 `BaseCommand.data`。

---

## 图 0 — 冷启动（S29 恢复能力的前提）

```
$ a2 service --config agent.toml
  → Bootstrap.__init__(config: str)
      → Bootstrap.config → dict                      # SERVICE_CONFIG ∪ TOML
      → Bootstrap._build_services() → BollydogServices
          → smart_import("a2.agent.service.AgentService").create_from(**conf) → AgentService
              → AppService._build_protocol({module: CacheLayer, protocol:{module: SQLiteProtocol, path:…}}) → CacheLayer
              → AgentService.add_dependency(CacheLayer) → CacheLayer      # self.protocol = CacheLayer
          → …（14 类服务逐段实例化，key = f'{domain}.{alias}'）
          → 二次遍历：depends: list[str] → dict[str, AppService]
              → AgentService.add_dependency(services['context.default'])
      → RegistryService.register() → None
          → _register_commands('agent.assistant', svc) → None
              # 扫描 a2.agent.commands，每个有 __call__ 的 BaseCommand 子类
              # 生成绑定子类 destination='agent.assistant.Reply' 写入 registry.commands
          → _register_subscribers('session.store', svc) → None
              # subscribers={'agent.*.ReplyFinished': 'on_reply_finished'}
              # 生成 handler Command destination='session.store.on_reply_finished'
              # registry.subscribers['agent.*.ReplyFinished'] ∋ 该 destination
      → _services_ctx_stack / _registry_ctx_stack / _session_ctx_stack / _hub_ctx_stack 压栈
  → Bootstrap.on_started()
      → for svc in services.values(): svc.maybe_start()
          → ToolkitService.on_first_start() → None
              → hub.before(ToolkitService._guard) → callable      # 权限 + 中断守卫
              → hub.after(ToolkitService._postprocess) → callable # 大结果截断 + 归档
          → McpService.on_started() → None
              → McpProtocol.list_tools() → list[dict]
              → McpService.register_tools(server: str, tools: list[dict]) → int
                  → type(f'mcp__{server}__{name}', (BaseCommand,), {...}) → type
                  → registry.commands[f'mcp.gateway.mcp__{server}__{name}'] = cls
              → hub.emit(R('mcp.gateway.ServerConnected')(server=…, tool_count=…))
          → ToolkitService.on_started() → None
              → ToolkitService.reindex() → int                    # 工具名 ↔ destination
          → HttpService.on_start() → None
              → 遍历 registry.commands × 各服务 routers → add_route
                # 'Reply': ['SSE','/api/agent/{agent}/reply'] 且 Reply 是 async gen → SseHandler
```

**暴露的接口**：`McpService.register_tools`、`ToolkitService.reindex`、`ToolkitService._guard`、`ToolkitService._postprocess`。

---

## 图 1 — S01 直接回答（最薄端到端切片）

```
POST /api/agent/assistant/reply  {"session_id":"s1","inputs":[{"role":"user","content":[{"type":"text","text":"你好"}]}]}
  → SseHandler.__call__(scope, receive, send)
      → R('agent.assistant.Reply')(session_id:str, inputs:list, structured_schema:dict|None, resume_state:dict|None) → Reply
      → hub.execute(Reply) → 后台 task
      → async for value in Reply.state: yield f"data: {json}\n\n"

Reply.__call__() → AsyncGenerator[dict]
  ├→ S.set(f'turn:{session_id}', {'turn_id':…, 'iter':0, 'interrupted':False}) → None
  ├→ yield {'type':'reply.started','agent':'assistant','turn_id':…}
  ├→ hub.emit(R('agent.assistant.ReplyStarted')(session_id:str, turn_id:str, agent:str)) → None
  │
  ├→ ctx = yield R('context.default.Assemble')(
  │        session_id:str, agent:str, inputs:list, system_prompt:str,
  │        tool_names:list, budget:int) → dict
  │     └→ Assemble.__call__() → dict
  │          ├→ S.history(session_id, field='context') → list[dict]
  │          ├→ app.get_dependency('skill.hub').catalog() → list[dict]
  │          ├→ app.get_dependency('plan.notebook').render_cached(session_id) → str
  │          ├→ app.assemble(parts: dict) → list[dict]          # 纯计算
  │          └→ return {'messages': list[dict], 'tokens': int, 'need_compress': bool}
  │
  ├→ tools = yield R('tool.toolkit.ListTools')(session_id:str, agent:str) → list[dict]
  │     └→ ListTools.__call__() → list
  │          ├→ protocol.get(f'groups:{session_id}') → list[str]
  │          └→ app.schemas(groups: list) → list[dict]   # registry.commands 过滤 + model_json_schema()
  │
  ├── 推理（流式中继）───────────────────────────────────────────
  │   gen = R('model.chat.Generate')(messages:list, tools:list, tool_choice:str, stream:bool)
  │   await hub.dispatch(gen) → gen
  │   async for chunk in gen.state:                 # StreamState 逐块
  │       yield chunk                               # 中继到 SSE
  │       if chunk['type'] == 'model.completed': completed = chunk
  │
  │   Generate.__call__() → AsyncGenerator[dict]
  │     ├→ app.format(messages: list, mode: str) → list[dict]        # 纯计算 formatter
  │     ├→ app.get_dependency('credential.vault').inject_model(app.provider) → dict
  │     ├→ async for delta in protocol.stream(payload: dict) → AsyncIterator[dict]
  │     │     yield {'type':'model.delta','block':'text','text':…}
  │     ├→ app.merge_deltas(deltas: list) → dict
  │     ├→ hub.emit(R('model.chat.ModelCalled')(model:str, usage:dict, latency_ms:int, finish_reason:str))
  │     └→ yield {'type':'model.completed','content':list,'usage':dict,'finish_reason':'stop'}
  │
  ├→ app.next_action(completed: dict, iter: int) → str          # 'exit'（无 tool_call）
  ├→ yield R('context.default.AppendContext')(session_id:str, messages:list) → int
  ├→ yield {'type':'reply.finished','finish_reason':'stop','content':[…]}
  └→ hub.emit(R('agent.assistant.ReplyFinished')(
         session_id:str, turn_id:str, agent:str, content:list,
         usage:dict, finish_reason:str, events:list)) → None

Exchange.bind_subscriber_callbacks(ReplyFinished)         # dispatch 时已绑定
  → ReplyFinished.state 完成 → _on_subscriber_done(dest, evt, state)
      → hub.dispatch(R('session.store.on_reply_finished')())     # _source = ReplyFinished
          → SessionService.on_reply_finished(message: BaseCommand) → dict
              → protocol.set(f'turn:{session_id}:{turn_id}', {...}) → None
      → hub.dispatch(R('observe.tracer.on_any')())
      → hub.dispatch(R('memory.longterm.on_reply_finished')())
```

**暴露的接口**：`ContextService.assemble`、`ToolkitService.schemas`、`ChatModelService.format` / `merge_deltas`、`AgentService.next_action`、`ChatModelProtocol.stream`、`CredentialService.inject_model`。

---

## 图 2 — S02 单次工具调用（含权限守卫）

承接图 1 的推理段，`completed['content']` 含 `tool_call`：

```
  ├→ app.next_action(completed, iter) → 'act'
  ├→ calls = app.extract_tool_calls(completed: dict) → list[dict]
  │        # [{'id':'c1','name':'read_path','input':{'path':'auth.py'}}]
  │
  ├→ decision = yield R('tool.toolkit.CheckPermission')(
  │        session_id:str, agent:str, tool:str, args:dict) → dict
  │     └→ CheckPermission.__call__() → dict
  │          ├→ protocol.match(tool: str, args: dict) → dict     # PermissionProtocol
  │          └→ return {'decision':'allow'|'deny'|'ask', 'reason':str}
  │
  ├── decision == 'allow' ─────────────────────────────────────
  │   yield {'type':'tool.call','id':'c1','name':'read_path','input':{...}}
  │   tcmd = R('tool.toolkit.Invoke')(
  │             call_id:str, tool:str, args:dict, session_id:str, agent:str)
  │   await hub.dispatch(tcmd) → tcmd
  │   async for chunk in tcmd.state: yield chunk               # 工具流式中继
  │   result = tcmd.state.result()[-1]
  │
  │   Invoke.__call__() → AsyncGenerator[dict]
  │     ├── hub.before 守卫 ToolkitService._guard(message) → dict | None
  │     │     ├→ S.get(f'turn:{session_id}') → dict
  │     │     └→ 若 interrupted → return {'type':'tool.result','status':'interrupted'}  # 短路
  │     ├→ dest = app.resolve_tool(tool: str) → str        # 'workspace.local.ReadPath'
  │     ├→ inner = R(dest)(**args)
  │     ├→ await hub.dispatch(inner)
  │     ├→ async for c in inner.state: yield {'type':'tool.chunk','id':call_id,'data':c}
  │     ├→ raw = inner.state.result()
  │     ├→ payload, spilled = app.truncate(raw: dict) → tuple
  │     │     └→ 超阈值时 yield R('workspace.local.StoreArtifact')(...) → str  # 见图 10
  │     ├→ hub.emit(R('tool.toolkit.ToolInvoked')(tool:str, call_id:str, ok:bool, ms:int))
  │     └→ yield {'type':'tool.result','id':call_id,'status':'ok','output':payload,'artifact':spilled}
  │
  │   ReadPath.__call__() → dict            # workspace.local 域
  │     ├→ app.resolve(path: str) → str     # 越界检查
  │     ├→ protocol.read(abs_path: str) → str   # LocalFileProtocol
  │     └→ return {'path':…, 'content':…, 'lines':int}
  │
  ├── decision == 'deny' ──────────────────────────────────────  # S27
  │   yield {'type':'tool.result','id':'c1','status':'denied','reason':…}
  │
  ├── decision == 'ask' ───────────────────────────────────────  # S06 → 图 4
  │
  ├→ yield R('context.default.AppendContext')(session_id:str, messages:list) → int
  ├→ hub.emit(R('agent.assistant.IterationCompleted')(session_id:str, turn_id:str, iter:int))
  └→ iter += 1 → 回到推理段
```

**暴露的接口**：`AgentService.extract_tool_calls`、`ToolkitService.resolve_tool` / `truncate`、`PermissionProtocol.match`、`WorkspaceService.resolve`、`FileProtocol.read`。

---

## 图 3 — S04 并行工具调用（扇出／扇入）

```
  ├→ calls = app.extract_tool_calls(completed) → list[dict]      # 3 个
  ├→ batches = app.batch_calls(calls: list) → list[dict]
  │        # [{'mode':'concurrent','calls':[c1,c2,c3]}]  依据 ToolkitService.is_concurrency_safe
  │
  ├── concurrent 批 ────────────────────────────────────────────
  │   cmds = [R('tool.toolkit.Invoke')(call_id=c['id'], tool=c['name'],
  │            args=c['input'], session_id=…, agent=…) for c in batch['calls']]
  │   results = yield cmds                     # _run_gen 的 list 分支
  │        → asyncio.gather(*(hub._submit(cmd) for cmd in cmds), return_exceptions=True)
  │        → list[list[dict] | Exception]      # 每项是该 Invoke 累积的全部 chunk
  │   for r in results:
  │       yield {'type':'tool.result', …}      # 逐条补发（并行分支不做实时中继）
  │
  ├── sequential 批 ────────────────────────────────────────────
  │   for c in batch['calls']:
  │       tcmd = R('tool.toolkit.Invoke')(…);  await hub.dispatch(tcmd)
  │       async for chunk in tcmd.state: yield chunk        # 串行分支保留实时中继
  │       if S.get(f'turn:{session_id}')['interrupted']: break
```

**设计取舍**：并行分支用 `yield [cmd,…]` 拿的是聚合结果，牺牲实时流以换取 bollydog 原生的 `gather` 扇入；串行分支用中继保留实时流。二者都在 bollydog 语义内，无自造机制。

**暴露的接口**：`AgentService.batch_calls`、`ToolkitService.is_concurrency_safe`。

---

## 图 4 — S06/S07 人机确认与追问（停放 → 恢复）

### 4a 停放

```
  ├→ decision['decision'] == 'ask'
  ├→ pending = {'call_id':…, 'tool':…, 'args':…, 'iter':iter,
  │             'messages':ctx['messages'], 'reason':decision['reason']}
  ├→ yield R('session.store.Park')(session_id:str, turn_id:str, pending:dict) → str
  │     └→ Park.__call__() → str
  │          ├→ protocol.set(f'park:{session_id}', pending) → None
  │          └→ return park_id
  ├→ yield {'type':'require_user_confirm','park_id':…,'tool':…,'args':…,'reason':…}
  ├→ hub.emit(R('agent.assistant.ReplyParked')(session_id:str, turn_id:str, park_id:str, kind:str))
  └→ return                                   # 生成器结束 → state.put(None) → SSE 关闭
```

`ask_human`（S07）走同一条路：工具 `ask_human` 本身就是一个 Command，其 `__call__` 直接 `yield R('session.store.Park')(…, kind='question')` 后返回，Reply 收到 `status='parked'` 后同样收敛。

### 4b 恢复

```
POST /api/agent/assistant/resume  {"session_id":"s1","park_id":"p1","decision":"approve"}
  → SseHandler → R('agent.assistant.Resume')(session_id:str, park_id:str, decision:str, answer:str) → Resume
  → hub.execute(Resume)

Resume.__call__() → AsyncGenerator[dict]
  ├→ pending = yield R('session.store.Unpark')(session_id:str, park_id:str) → dict
  ├→ 若 decision == 'reject' → yield {'type':'tool.result','status':'rejected'} ；否则原样带上
  ├→ rcmd = R('agent.assistant.Reply')(session_id:str, inputs:list, resume_state:dict)
  ├→ await hub.dispatch(rcmd) → rcmd
  └→ async for chunk in rcmd.state: yield chunk          # 命令中继模式
```

`Reply` 检测到 `resume_state` 非空时跳过「新回合初始化」，直接从 `resume_state['iter']` 与 `resume_state['messages']` 续跑。

**暴露的接口**：`SessionService.park_key`、`Park` / `Unpark` 命令。

---

## 图 5 — S05 中断

```
POST /api/agent/assistant/interrupt  {"session_id":"s1"}
  → hub.execute(R('agent.assistant.Interrupt')(session_id:str, reason:str)) → dict

Interrupt.__call__() → dict
  ├→ st = await S.get(f'turn:{session_id}') → dict
  ├→ st['interrupted'] = True ; st['reason'] = reason
  ├→ await S.set(f'turn:{session_id}', st) → None
  └→ return {'session_id':…, 'accepted': True, 'turn_id': st['turn_id']}

# 生效路径（两条，互补）
① 正在跑的 Reply 在每个安全点（推理前、每个工具批之间、迭代末尾）
     → S.get(f'turn:{session_id}')['interrupted'] → True
     → yield {'type':'reply.interrupted'} ; hub.emit(ReplyInterrupted(…)) ; return
② 已在 Queue 里排队的 tool.toolkit.Invoke
     → hub.before ToolkitService._guard(message) → {'type':'tool.result','status':'interrupted'}
     → CommandRunnerMixin._execute 短路：message.state.set_result(short)，runner 不执行
```

**关键**：中断是**协作式**的，靠 `Session` 标记 + `hub.before` 短路，不依赖任何 bollydog 未提供的取消 API。

---

## 图 6 — S08 上下文压缩

```
  ├→ ctx = yield R('context.default.Assemble')(…) → dict     # ctx['need_compress'] == True
  ├→ yield {'type':'context.compacting','tokens':ctx['tokens']}
  ├→ res = yield R('context.default.Compress')(
  │        session_id:str, agent:str, keep_ratio:float, model_ref:str) → dict
  │     └→ Compress.__call__() → dict
  │          ├→ msgs = await S.history(session_id, field='context') → list[dict]
  │          ├→ head, tail = app.split_window(msgs: list, keep_ratio: float) → tuple
  │          ├→ summary_cmd = R(f'{self.model_ref}.Generate')(
  │          │        messages=app.compression_prompt(head), tools=[], stream=False)
  │          ├→ chunks = yield summary_cmd → list[dict]
  │          ├→ summary = app.pick_text(chunks: list) → str
  │          ├→ await S.set(f'summary:{session_id}', summary) → None
  │          ├→ await S.set(f'context:{session_id}', {'context': tail}) → None
  │          ├→ hub.emit(R('context.default.ContextCompacted')(
  │          │        session_id:str, before_tokens:int, after_tokens:int, folded:int))
  │          └→ return {'summary':str,'before_tokens':int,'after_tokens':int,'folded':int}
  ├→ yield {'type':'context.compacted', **res}
  └→ ctx = yield R('context.default.Assemble')(…) → dict     # 重新装配后继续
```

**暴露的接口**：`ContextService.split_window` / `compression_prompt` / `pick_text`。

---

## 图 7 — S10 文档入库与检索增强

### 7a 入库（流式进度）

```
POST /api/knowledge/base/ingest  (SSE)
  → R('knowledge.base.IngestDocument')(doc_id:str, path:str, collection:str, chunk_size:int) → cmd

IngestDocument.__call__() → AsyncGenerator[dict]
  ├→ sections = app.parse(path: str) → list[dict]           # 按扩展名选解析器
  ├→ yield {'type':'ingest.parsed','sections':len(sections)}
  ├→ chunks = app.chunk(sections: list, size: int, overlap: int) → list[dict]
  ├→ for batch in app.batches(chunks: list) → list[list]:
  │     vecs = yield R('model.embed.Embed')(texts:list, model:str) → list
  │     yield R('knowledge.base.UpsertChunks')(collection:str, items:list) → int
  │     yield {'type':'ingest.progress','done':…,'total':len(chunks)}
  ├→ hub.emit(R('knowledge.base.DocumentIngested')(doc_id:str, chunks:int, collection:str))
  └→ yield {'type':'ingest.completed','doc_id':…,'chunks':len(chunks)}

UpsertChunks.__call__() → int
  └→ protocol.upsert(collection: str, items: list) → int      # VectorStoreProtocol
```

### 7b 检索注入（静态模式，Assemble 内）

```
Assemble.__call__() → dict
  ├→ 若 app.rag_refs 非空且为本回合首轮：
  │     hits = yield R('knowledge.base.Search')(
  │              collection:str, query:str, top_k:int, score_threshold:float) → list
  │       └→ Search.__call__() → list
  │            ├→ vec = yield R('model.embed.Embed')(texts=[query]) → list
  │            ├→ raw = await protocol.search(collection, vec[0], top_k) → list[dict]
  │            ├→ return app.rerank(raw: list, query: str) → list[dict]
  │     parts['retrieved'] = app.render_hits(hits: list) → str
  └→ app.assemble(parts) → list[dict]
```

**agentic 模式**：不在 `Assemble` 里检索，而是把 `knowledge.base.Search` 注册进工具分组 `knowledge`，由模型自行决定何时检索。两种模式由 `AgentService.rag_mode` 配置切换。

---

## 图 8 — S12 接入外部工具服务

```
POST /api/mcp/gateway/connect  {"server":"github","transport":"stdio","command":"npx","args":[…]}
  → hub.execute(R('mcp.gateway.ConnectServer')(
        server:str, transport:str, command:str, args:list, url:str, env:dict)) → dict

ConnectServer.__call__() → dict
  ├→ proto = app.build_protocol(transport: str, conf: dict) → McpProtocol
  ├→ app.add_dependency(proto) → McpProtocol         # 生命周期交给 mode 托管
  ├→ await proto.maybe_start() → None
  ├→ tools = await proto.list_tools() → list[dict]
  ├→ n = app.register_tools(server: str, tools: list) → int
  │     └→ 对每个 tool：
  │          fields = app.jsonschema_to_fields(tool['inputSchema']) → dict
  │          cls = type(f'mcp__{server}__{tool["name"]}', (BaseCommand,), {
  │                    '__doc__': tool['description'],
  │                    '__annotations__': fields['annotations'],
  │                    'destination': f'mcp.gateway.mcp__{server}__{tool["name"]}',
  │                    '__call__': _make_caller(server, tool['name']),
  │                    **fields['defaults']})
  │          registry.commands[cls.destination] = cls
  ├→ hub.emit(R('mcp.gateway.ServerConnected')(server:str, tool_count:int, transport:str))
  └→ return {'server':…, 'tool_count': n, 'tools':[…]}

Exchange → ToolkitService.on_server_connected(message: BaseCommand) → dict
  ├→ evt = message._source                                 # 原始 ServerConnected
  └→ app.reindex() → int                                   # 刷新工具名 ↔ destination 索引
```

**关键**：动态生成 Command 子类并写入 `registry.commands`，与 bollydog `RegistryService._register_subscribers` 为订阅方法动态造 handler Command 是**同一手法**，不是新概念。

---

## 图 9 — S14 子智能体委派

```
（Reply 推理段产出 tool_call: {'name':'spawn_agent','input':{'agent':'researcher','task':'…'}}）
  ├→ tcmd = R('tool.toolkit.Invoke')(tool='spawn_agent', args={…}, …)
  │
  │   Invoke → dest = app.resolve_tool('spawn_agent') → 'agent.assistant.Spawn'
  │   Spawn.__call__() → AsyncGenerator[dict]
  │     ├→ depth = int(message.data.get('agent_depth', 0))
  │     ├→ 若 depth >= app.max_depth → yield {'error':'max agent depth'} ; return
  │     ├→ sub = R(f'agent.{self.agent}.Reply')(session_id=f'{sid}#{iid}', inputs=[…])
  │     ├→ sub.data = {**message.data, 'agent_depth': depth + 1, 'parent_turn': …}
  │     ├→ await hub.dispatch(sub) → sub
  │     ├→ async for chunk in sub.state:
  │     │     yield {'type':'subagent.chunk','agent':self.agent,'data':chunk}
  │     └→ yield {'type':'subagent.result','agent':self.agent,
  │               'content': app.pick_final(sub.state.result())}
```

`trace_id` 自动继承（`model_post_init`），`parent_span_id` 自动指向 `Spawn`，因此 `observe.tracer` 里父子智能体天然是一棵树。

---

## 图 10 — S17 大结果归档与检索

```
Invoke.__call__() 尾部
  ├→ payload, ref = app.truncate(raw: dict) → tuple[dict, str | None]
  │     └→ len(json.dumps(raw)) > app.spill_threshold
  ├→ 若 ref 需要生成：
  │     ref = yield R('workspace.local.StoreArtifact')(
  │              key:str, content:str, mime:str, session_id:str) → str
  │       └→ StoreArtifact.__call__() → str
  │            ├→ path = app.artifact_path(session_id: str, key: str) → str
  │            ├→ await protocol.write(path: str, content: str) → None   # FileProtocol
  │            ├→ hub.emit(R('workspace.local.ArtifactStored')(ref:str, bytes:int, mime:str))
  │            └→ return ref                        # 'artifact://s1/tool-c1.json'
  └→ yield {'type':'tool.result','output':payload,'artifact':ref,'truncated':True}

（之后模型可调 grep_artifact 工具）
GrepArtifact.__call__() → dict
  ├→ path = app.ref_to_path(ref: str) → str
  ├→ text = await protocol.read(path) → str
  └→ return {'ref':…, 'pattern':…, 'matches': list[dict], 'total': int}
```

同一手法由 `hub.after` 钩子 `ToolkitService._postprocess` 兜底：任何未走 `Invoke` 的工具结果超阈值时也会被归档。

---

## 图 11 — S19 模型回退

```
Generate.__call__() → AsyncGenerator[dict]
  ├→ for model_name in app.fallbacks() → list[str]:            # ['gpt-4o','deepseek-chat']
  │     for attempt in range(app.max_retries + 1):
  │         try:
  │             async for delta in protocol.stream(payload, model=model_name): yield delta
  │             break（成功）
  │         except TransientModelError as e:
  │             yield {'type':'model.retry','model':model_name,'attempt':attempt,'error':str(e)}
  │             await asyncio.sleep(app.backoff(attempt) → float)
  │     若成功 → break
  ├→ 全部失败：
  │     hub.emit(R('model.chat.ModelFailed')(model:str, error:str, attempts:int))
  │     yield {'type':'model.failed','error':…}                # S25
  └→ Reply 收到 model.failed → yield {'type':'reply.finished','finish_reason':'model_error'}
     ； 停放现场供重试：yield R('session.store.Park')(…, kind='retry')
```

---

## 图 12 — S15 多智能体广播协作

```
hub.execute(R('team.room.Broadcast')(topic:str, sender:str, content:list)) → dict

Broadcast.__call__() → dict
  ├→ members = app.members(topic: str) → list[str]          # ['assistant','critic','writer']
  ├→ hub.emit(R('team.room.MessageBroadcast')(
  │        topic:str, sender:str, content:list, members:list)) → None
  └→ return {'topic':…, 'delivered': len(members) - 1}

Exchange 匹配 'team.room.MessageBroadcast'
  → 每个 AgentService.on_broadcast(message: BaseCommand) → dict
      ├→ evt = message._source
      ├→ 若 evt.sender == self.alias → return {'skipped': True}
      └→ hub.dispatch(R(f'agent.{self.alias}.Observe')(
             session_id=f'team:{evt.topic}', inputs=evt.content, sender=evt.sender))

Observe.__call__() → int
  ├→ await S.append(f'context:{session_id}', 'context', msg) → None
  └→ return 1

# 顺序协作
Sequential.__call__() → AsyncGenerator[dict]
  ├→ payload = self.inputs
  ├→ for name in self.agents:
  │     cmd = R(f'agent.{name}.Reply')(session_id=f'team:{self.topic}', inputs=payload)
  │     await hub.dispatch(cmd)
  │     async for chunk in cmd.state: yield {'agent':name, **chunk}
  │     payload = app.to_inputs(cmd.state.result()) → list
  └→ hub.emit(R('team.room.RoundCompleted')(topic:str, agents:list, rounds:int))

# 并行协作
Fanout.__call__() → AsyncGenerator[dict]
  ├→ cmds = [R(f'agent.{n}.Reply')(session_id=…, inputs=self.inputs) for n in self.agents]
  ├→ results = yield cmds                       # _run_gen list 分支 → gather
  └→ yield {'type':'fanout.completed','results': app.zip(self.agents, results)}
```

---

## 图 13 — S03 规划驱动

```
  ├→ tool_call: {'name':'create_plan','input':{'tasks':[…]}}
  ├→ Invoke → dest 'plan.notebook.CreatePlan'
  │   CreatePlan.__call__() → dict
  │     ├→ tasks = app.normalize(self.tasks: list) → list[dict]
  │     ├→ await protocol.set(f'plan:{session_id}', {'tasks':tasks}) → None
  │     ├→ hub.emit(R('plan.notebook.PlanCreated')(session_id:str, count:int))
  │     └→ return {'plan_id':…, 'tasks': tasks, 'rendered': app.render(tasks) → str}
  │
  ├→ 后续每轮 Assemble 注入清单：
  │     app.get_dependency('plan.notebook').render_cached(session_id) → str
  │     → parts['plan_hint']
  │
  ├→ tool_call: {'name':'update_task','input':{'task_id':'t1','state':'completed'}}
  │   UpdateTask.__call__() → dict
  │     ├→ plan = await protocol.get(f'plan:{session_id}') → dict
  │     ├→ app.apply(plan: dict, task_id: str, state: str, note: str) → dict
  │     ├→ await protocol.set(f'plan:{session_id}', plan) → None
  │     ├→ hub.emit(R('plan.notebook.TaskUpdated')(session_id:str, task_id:str, state:str))
  │     ├→ 若 app.progress(plan)['pending'] == 0
  │     │     → hub.emit(R('plan.notebook.PlanCompleted')(session_id:str))
  │     └→ return {'tasks': plan['tasks'], 'progress': app.progress(plan) → dict}
```

---

## 图 14 — S23 工具分组渐进披露

```
ListTools.__call__() → list
  ├→ active = await protocol.get(f'groups:{session_id}') → list[str] | None
  ├→ active = active or app.always_on_groups                 # ['basic']
  └→ return app.schemas(active) + [app.meta_tool_schema(app.dormant_groups(active))]

（模型调用 meta tool）
ActivateGroup.__call__() → dict
  ├→ active = await protocol.get(f'groups:{session_id}') → list[str]
  ├→ nxt = app.apply_groups(active: list, enable: list, disable: list) → list
  ├→ await protocol.set(f'groups:{session_id}', nxt) → None
  ├→ hub.emit(R('tool.toolkit.GroupActivated')(session_id:str, groups:list))
  └→ return {'groups': nxt, 'tools': [s['name'] for s in app.schemas(nxt)],
             'instructions': app.group_instructions(nxt) → str}
```

技能三级披露同构：`ListSkills`（目录）→ `MatchSkills`（相关）→ `LoadSkill`（正文）。

---

## 图 15 — S16 沙箱执行

```
Invoke → dest 'workspace.docker.RunCommand'

RunCommand.__call__() → AsyncGenerator[dict]
  ├→ 若不通过 app.policy(self.command: str) → bool
  │     → yield {'status':'denied','reason':…} ; return
  ├→ async for line in protocol.exec_stream(
  │        command: str, cwd: str, env: dict, timeout: int) → AsyncIterator[dict]:
  │     yield {'type':'exec.output','stream':line['stream'],'text':line['text']}
  ├→ files = await protocol.list_changed(since: float) → list[dict]
  └→ yield {'type':'exec.completed','exit_code':int,'files':files,'ms':int}
```

`SandboxProtocol` 的四种实现（Local / Docker / K8s / Ssh）签名一致，TOML 换一行 `module=` 即切换；测试用 `FakeSandboxProtocol` 回放固定输出。

---

## 图 16 — S21 定时执行

```
SchedulerService（入口层）
  @mode.Service.timer(interval=30.0)
  async def _tick(self) → None
    ├→ due = await self.protocol.due(now: float) → list[dict]
    ├→ for job in due:
    │     cmd = R(job['destination'])(**job['payload'])
    │     cmd.data = {'schedule_id': job['id'], 'user_id': job['user_id']}
    │     await hub.dispatch(cmd) → cmd
    │     await self.protocol.mark_started(job['id'], cmd.iid) → None
    └→ hub.emit(R('schedule.runner.JobDispatched')(job_id:str, destination:str))
```

`SchedulerService` 与 `HttpService` / `SocketService` 同为**入口**（entrypoint），因此允许主动 dispatch；业务域的 `AppService` 一律不主动 dispatch。

---

## 图 17 — S20/S24 可观测与断线重放

```
# 落盘（Event 侧，不在主链路上）
Exchange 匹配 '#'
  → TraceService.on_any(message: BaseCommand) → dict
      ├→ evt = message._source
      ├→ span = app.to_span(evt: BaseCommand) → dict
      │     # {'trace_id','span_id','parent_span_id','name','ts','duration_ms','attrs'}
      ├→ await protocol.add(span) → None                # CRUDProtocol
      └→ 同时把事件字典追加到会话事件序列：
         await protocol.add({'kind':'event','session_id':…, 'seq':…, 'payload':…})

# 查询
QueryTrace.__call__() → dict
  ├→ rows = await protocol.list(trace_id=self.trace_id) → list[dict]
  └→ return {'trace_id':…, 'spans': app.tree(rows) → list[dict]}

# 断线重放
GET /api/session/store/replay?session_id=s1&last_seq=42   (SSE)
ReplayEvents.__call__() → AsyncGenerator[dict]
  ├→ rows = await protocol.list(session_id=self.session_id, seq_gt=self.last_seq) → list[dict]
  ├→ for row in rows: yield row['payload']
  └→ yield {'type':'replay.completed','last_seq': rows[-1]['seq'] if rows else self.last_seq}
```

---

## 图 18 — S29 崩溃恢复

```
（进程重启后）
GET /api/session/store/open?session_id=s1
  → hub.execute(R('session.store.OpenSession')(session_id:str, user_id:str)) → dict

OpenSession.__call__() → dict
  ├→ rec = await protocol.get(f'session:{session_id}') → dict | None
  ├→ 若 None → app.new_record(session_id, user_id) → dict ; protocol.set(...)
  ├→ park = await protocol.get(f'park:{session_id}') → dict | None
  ├→ turns = await protocol.keys(f'turn:{session_id}:*') → list[str]
  ├→ 回填回合内便签：await S.set(f'context:{session_id}',
  │        {'context': app.context_from_turns(turns) → list}) → None
  └→ return {'session_id':…, 'turns': len(turns), 'parked': park,
             'resumable': park is not None}
```

`CacheLayer.on_started` 冷启动时会从 `SQLiteProtocol` 全量 `load()`，因此会话档案在重启后自动回到内存缓存。

---

## 接口提取索引

下表把上述图里出现过的每一条箭头归到 P5 的三类契约，`interfaces.md` 逐条落定签名。

| 类别 | 出现在 | 数量 |
|------|--------|------|
| Command 签名 | 图 1–18 全部 `R(dest)(…)` | 62 |
| AppService 业务方法 | 全部 `app.xxx(…)` | 58 |
| Protocol 方法 | 全部 `protocol.xxx(…)` | 34 |
| Subscriber 方法 | 图 1、8、12、17 | 7 |
| 事件流块类型（yield 的 dict） | 图 1–17 全部 `yield {'type':…}` | 28 |
