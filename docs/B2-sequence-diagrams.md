# B2 — 顺序图（步骤 2）

> 每条箭头 = 一次方法调用，标注 `方法名(关键参数) → 返回类型`。
> 从箭头中提取步骤 3（接口契约）。箭头对不上 = 接口缺失。
>
> 优先级：P0 = 骨架必须 | P1 = v1 必须 | P2 = v2 延后

---

## P0-1. S15 冷启动

```mermaid
sequenceDiagram
    autonumber
    participant CLI
    participant Cfg as config.py
    participant Hub
    participant LLM as LLMService
    participant ENV as EnvService
    participant SK as SkillService
    participant PL as PlannerService
    participant MCP as MCPService
    participant AS as AgentService
    participant CTX as ContextService
    participant L0 as L0(rules)
    participant Facts as Facts

    CLI->>Cfg: build_config('agent.toml') → dict
    CLI->>Cfg: load_a2_config(merged_dict)
    Cfg->>LLM: create_from(**conf) → 实例化
    Cfg->>ENV: create_from(**conf) → 实例化
    Cfg->>SK: create_from(**conf) → 实例化
    Cfg->>PL: create_from(**conf) → 实例化
    Cfg->>MCP: create_from(**conf) → 实例化
    Cfg->>AS: create_from(**conf) → 实例化
    Note over Cfg: resolve depends → add_dependency

    Hub->>LLM: on_started()
    LLM->>LLM: Router(model_list) → self.adapter

    Hub->>ENV: on_started()
    ENV->>ENV: PermissionProtocol.on_start() → _config
    ENV->>ENV: _load_commands → _tools{}

    Hub->>SK: on_started()
    SK->>SK: CacheLayer.on_start() → SELECT * FROM skills.db → _skills{}

    Hub->>PL: on_started()
    Hub->>MCP: on_started()
    MCP->>MCP: MCPServerProtocol.on_start() → ClientSession.initialize()

    Hub->>AS: on_started()
    AS->>AS: protocol.get_all() → roles.db → _roles{}
    AS->>AS: activate_role('code_reviewer')
    AS->>CTX: type() → 动态子类实例
    AS->>CTX: ctx.maybe_start()
    CTX->>L0: _create_from_spec(base_dir, spec_l0) → MemoryLayerService
    CTX->>L0: maybe_start() → CacheLayer → SELECT * FROM memory.db/rules
    CTX->>CTX: _create_from_spec → L1, L4
    CTX->>Facts: _create_from_spec(base_dir, spec_facts) → GlobalFactsService
    CTX->>Facts: maybe_start() → CacheLayer → SELECT * FROM facts.db
    AS->>AS: _active_contexts['code_reviewer'] = ctx
    Note over CLI: 就绪，等待用户输入
```

---

## P0-2. S01 直答（无工具）

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant Hub
    participant AC as AgentCommand
    participant RS as ReActStepCommand
    participant CTX as ContextService
    participant LLM as LLMService
    participant SK as SkillService
    participant L0 as L0(rules)
    participant Facts as Facts(BM25)
    participant ENV as EnvService

    U->>Hub: dispatch({dest:'agent.AgentService', goal:'什么是依赖注入?'})
    Hub->>AC: AgentCommand(goal, role_def).__call__()
    AC->>AC: TaskList(tasks=[Task(content=goal)])

    AC->>RS: yield ReActStepCommand(task, role_def, role_ctx, history=[])

    Note over RS,Facts: ── build_messages ──
    RS->>CTX: build_messages(role_def, '什么是依赖注入?', history=[]) → list[dict]
    CTX->>CTX: _render_prompt(role_def.system_prompt, role_def) → str
    CTX->>L0: get_all() → dict[str,str]
    CTX->>SK: get_metadata_prompt(role_def.skill_refs) → str
    CTX->>Facts: get_relevant('什么是依赖注入?', budget=2000) → str
    CTX->>SK: get_matching('什么是依赖注入?', role_def.skill_refs) → list[Skill]
    Note over SK: 无匹配 → 空列表，不注入 skill reminder
    CTX->>CTX: compact_if_needed(messages, role_def.model) → list[dict]
    CTX->>LLM: count_tokens(messages, role_def.model) → int
    Note over CTX: token < threshold → 原样返回

    Note over RS,ENV: ── 聚合工具 ──
    RS->>ENV: get_tool_schemas(role_def.tools) → list[dict]
    RS->>AC: get_agent_tool_schemas() → list[dict]

    Note over RS,LLM: ── Reason ──
    RS->>LLM: chat(messages, tools, role_def.model) → LLMResponse

    Note over RS: response.tool_calls=[] → 任务完成
    RS-->>AC: TaskResult(success=True, output=response.text)

    AC-->>Hub: yield {type:'text', content: response.text}
    Hub-->>U: 流式输出文字回答
```

---

## P0-3. S02 单工具调用

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant Hub
    participant AC as AgentCommand
    participant RS as ReActStepCommand
    participant CTX as ContextService
    participant LLM as LLMService
    participant ENV as EnvService
    participant SK as SkillService

    U->>Hub: dispatch({goal:'读一下 auth.py'})
    Hub->>AC: AgentCommand.__call__()
    AC->>AC: TaskList(tasks=[Task(content=goal)])
    AC->>RS: yield ReActStepCommand(task, role_def, role_ctx, history=[])

    RS->>CTX: build_messages(role_def, '读一下 auth.py', []) → list[dict]
    RS->>ENV: get_tool_schemas(role_def.tools) → list[dict]
    RS->>LLM: chat(messages, tools, model) → LLMResponse
    Note over LLM: tool_calls=[{name:'read_file', arguments:{path:'auth.py'}}]

    Note over RS,ENV: ── Act: 工具执行 ──
    RS->>ENV: execute_tool('read_file', {path:'auth.py'}) → str
    ENV->>ENV: protocol.check_permission('read_file') → 'allow'
    ENV->>ENV: protocol.read_file('auth.py') → str
    ENV-->>RS: observation = '文件内容...'

    Note over RS: history += [assistant(tool_calls), tool(observation)]

    Note over RS,LLM: ── 第二轮 Reason ──
    RS->>CTX: build_messages(role_def, query, history) → list[dict]
    RS->>LLM: chat(messages, tools, model) → LLMResponse
    Note over LLM: tool_calls=[] → 文字回复

    RS-->>AC: TaskResult(success=True, output=response.text)
    AC-->>Hub: yield {type:'text', content: response.text}
    Hub-->>U: 输出文件内容摘要
```

---

## P0-4. S03 多轮工具 + 分析

> S03 与 S02 结构相同，区别在于 LLM 可能连续调用多个工具（读文件 → grep → 分析）。
> 此处只画与 S02 的差异部分：ReAct 循环内多次工具调用。

```mermaid
sequenceDiagram
    autonumber
    participant RS as ReActStepCommand
    participant CTX as ContextService
    participant LLM as LLMService
    participant ENV as EnvService

    Note over RS: step=0, history=[]

    RS->>CTX: build_messages(role_def, '审查 auth.py 安全问题', []) → list[dict]
    RS->>LLM: chat(messages, tools, model) → LLMResponse
    Note over LLM: tool_calls=[read_file(path='auth.py')]

    RS->>ENV: execute_tool('read_file', {path:'auth.py'}) → str
    RS->>RS: history += [assistant(tool_calls), tool(observation)]

    Note over RS: step=1

    RS->>CTX: build_messages(role_def, query, history) → list[dict]
    RS->>LLM: chat(messages, tools, model) → LLMResponse
    Note over LLM: tool_calls=[grep_search(pattern='SELECT.*\\+', path='auth.py')]

    RS->>ENV: execute_tool('grep_search', {pattern, path}) → str
    RS->>RS: history += [assistant(tool_calls), tool(observation)]

    Note over RS: step=2

    RS->>CTX: build_messages(role_def, query, history) → list[dict]
    RS->>LLM: chat(messages, tools, model) → LLMResponse
    Note over LLM: tool_calls=[] → 文字回复: 审查报告

    RS-->>RS: return TaskResult(success=True, output='审查报告...')
```

---

## P1-1. S04 任务规划 + 逐步执行

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant Hub
    participant AC as AgentCommand
    participant PC as PlanCommand
    participant PL as PlannerService
    participant LLM as LLMService
    participant RS as ReActStepCommand
    participant CTX as ContextService
    participant ENV as EnvService

    U->>Hub: dispatch({goal:'重构整个认证模块', role_def.use_planning=true})
    Hub->>AC: AgentCommand.__call__()

    Note over AC,LLM: ── 规划阶段 ──
    AC->>PC: yield PlanCommand(goal, run_id)
    PC->>LLM: chat([system(规划提示词), user(goal)], model) → LLMResponse
    Note over LLM: 返回 JSON: {tasks: [{content:'分析现有代码'}, {content:'设计接口'}, {content:'重写文件'}, {content:'跑测试'}]}
    PC->>PC: TaskList.model_validate_json(response.text)
    PC->>PL: save_task_list(task_list, base_dir)
    PL->>PL: ensure_tasks_store(base_dir) → MemoryLayerService
    PL->>PL: store.set(run_id, task_list.json())
    PC-->>AC: task_list (4 tasks)

    Note over AC,ENV: ── 逐步执行（每个 task 走 ReAct 循环）──

    loop 对每个 task in task_list.pending()
        AC->>AC: task.status = 'in_progress'
        AC-->>Hub: yield {type:'progress', task_id, status:'in_progress'}
        AC->>RS: yield ReActStepCommand(task, role_def, role_ctx, history)
        Note over RS,ENV: （同 S03 的 ReAct 循环）
        RS-->>AC: TaskResult(success, output)
        AC->>AC: task.status = 'done', task.result = output
        AC-->>Hub: yield {type:'progress', task_id, status:'done'}
    end

    AC-->>Hub: yield {type:'text', content: task_list.render_progress()}
    Hub-->>U: 输出汇总报告
```

---

## P1-2. S05 多角色并行

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant Hub
    participant OC as AgentCommand<br/>(orchestrator)
    participant RS as ReActStepCommand
    participant LLM as LLMService
    participant SP1 as SpawnAgentCmd<br/>(staging)
    participant SP2 as SpawnAgentCmd<br/>(prod)
    participant AS as AgentService
    participant AC1 as AgentCommand<br/>(staging_reader)
    participant AC2 as AgentCommand<br/>(prod_reader)
    participant ENV as EnvService

    U->>Hub: dispatch({goal:'对比 staging 和 prod 配置差异'})
    Hub->>OC: AgentCommand(orchestrator).__call__()

    Note over OC,LLM: ── 规划(use_planning=true) ──
    OC->>LLM: PlanCommand → chat(...) → TaskList[spawn staging, spawn prod, 汇总]

    Note over OC,LLM: ── Task1: LLM 决定 spawn ──
    OC->>RS: yield ReActStepCommand(task1, orchestrator)
    RS->>LLM: chat(messages, tools=[spawn_agent, ask_user]) → LLMResponse
    Note over LLM: tool_calls=[spawn_agent(staging_reader), spawn_agent(prod_reader)]

    par 并行 spawn
        RS->>SP1: yield SpawnAgentCommand(role='staging_reader', input={task:'读 staging 配置'})
        SP1->>AS: activate_role('staging_reader') → ContextService
        SP1->>SP1: session.get('agent_depth') → 0, set → 1
        SP1->>AC1: yield AgentCommand(staging_reader, goal)
        AC1->>LLM: ReActStep → chat → tool_calls=[read_file]
        AC1->>ENV: execute_tool('read_file', 'staging/config.yaml') → str
        AC1->>LLM: chat(+observation) → LLMResponse(text)
        AC1-->>SP1: TaskResult(success=True, output='staging配置内容')
        SP1->>SP1: session.set('agent_depth', 0)
        SP1-->>RS: staging_result
    and
        RS->>SP2: yield SpawnAgentCommand(role='prod_reader', input={task:'读 prod 配置'})
        SP2->>AS: activate_role('prod_reader') → ContextService
        SP2->>SP2: session.get('agent_depth') → 0, set → 1
        SP2->>AC2: yield AgentCommand(prod_reader, goal)
        AC2->>LLM: ReActStep → chat → tool_calls=[read_file]
        AC2->>ENV: execute_tool('read_file', 'production/config.yaml') → str
        AC2->>LLM: chat(+observation) → LLMResponse(text)
        AC2-->>SP2: TaskResult(success=True, output='prod配置内容')
        SP2->>SP2: session.set('agent_depth', 0)
        SP2-->>RS: prod_result
    end

    Note over RS: history += [tool(staging_result), tool(prod_result)]

    Note over OC,LLM: ── Task3: 汇总 ──
    OC->>RS: yield ReActStepCommand(task3, orchestrator, history)
    RS->>LLM: chat(messages+history, tools) → LLMResponse(text='迁移方案...')
    RS-->>OC: TaskResult(success=True, output='迁移方案')

    OC-->>Hub: yield {type:'text', content:'迁移方案'}
    Hub-->>U: 输出迁移方案
```

---

## P1-3. S06 子角色失败回退

> 在 S05 基础上，prod_reader 执行失败的分支。

```mermaid
sequenceDiagram
    autonumber
    participant RS as ReActStepCommand<br/>(orchestrator)
    participant SP2 as SpawnAgentCmd<br/>(prod)
    participant AC2 as AgentCommand<br/>(prod_reader)
    participant LLM as LLMService
    participant ENV as EnvService
    participant U as User

    RS->>SP2: yield SpawnAgentCommand(role='prod_reader')
    SP2->>AC2: yield AgentCommand(prod_reader, goal)
    AC2->>LLM: chat(messages, tools) → LLMResponse
    Note over LLM: tool_calls=[read_file(path='production/config.yaml')]
    AC2->>ENV: execute_tool('read_file', {path}) → str
    ENV->>ENV: protocol.check_permission('read_file') → 'allow'
    ENV->>ENV: protocol.read_file('production/config.yaml')
    ENV-->>AC2: 'Error: Permission denied'
    AC2->>LLM: chat(+error observation) → LLMResponse
    Note over LLM: tool_calls=[] → text='无法读取 production 配置，权限不足'
    AC2-->>SP2: TaskResult(success=False, output='权限不足')
    SP2-->>RS: prod_result = {success:False}

    Note over RS: history += [tool(prod_result)]
    Note over RS: 回到 orchestrator 的 ReAct 循环

    RS->>LLM: chat(messages+history, tools=[spawn_agent, ask_user]) → LLMResponse
    Note over LLM: 看到 prod 失败 → tool_calls=[ask_user(message:'请提供 prod 配置')]
    RS-->>U: yield AskUserCommand(message='请提供 production 配置内容')
    U-->>RS: 用户粘贴配置内容
    Note over RS: history += [tool(用户提供的配置)]

    RS->>LLM: chat(messages+history) → LLMResponse(text='迁移方案...')
```

---

## P1-4. S07 危险操作审批

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant Hub
    participant RS as ReActStepCommand
    participant CTX as ContextService
    participant LLM as LLMService
    participant ENV as EnvService

    Note over RS: 已进入 ReAct 循环

    RS->>CTX: build_messages(role_def, '执行部署脚本 ./deploy.sh', history) → list[dict]
    RS->>LLM: chat(messages, tools, model) → LLMResponse
    Note over LLM: tool_calls=[{name:'bash', arguments:{command:'./deploy.sh'}}]

    Note over RS,ENV: ── 权限检查 ──
    RS->>ENV: execute_tool('bash', {command:'./deploy.sh'}) → str
    ENV->>ENV: protocol.check_permission('bash') → 'approve'

    Note over ENV,U: ── 审批中断 ──
    ENV-->>RS: 'approve'
    RS-->>Hub: yield AskUserCommand('Agent 请求执行 bash(./deploy.sh)，是否允许？')
    Hub-->>U: 显示审批请求
    U-->>Hub: 'y'
    Hub-->>RS: approved=true

    Note over RS,ENV: ── 执行 ──
    RS->>ENV: execute_tool('bash', {command:'./deploy.sh'}) → str
    ENV->>ENV: protocol.execute('./deploy.sh', timeout=30) → str
    ENV-->>RS: observation = 'deploy output...'

    RS->>RS: history += [assistant(tool_calls), tool(observation)]
    RS->>CTX: build_messages(role_def, query, history) → list[dict]
    RS->>LLM: chat(messages, tools, model) → LLMResponse
    Note over LLM: tool_calls=[] → text='部署完成，输出正常'
    RS-->>RS: TaskResult(success=True, output=text)
```

---

## P1-5. S08 外部工具（MCP）

```mermaid
sequenceDiagram
    autonumber
    participant RS as ReActStepCommand
    participant CTX as ContextService
    participant LLM as LLMService
    participant ENV as EnvService
    participant MCP as MCPService
    participant GH as MCPServerProtocol<br/>(github)

    RS->>CTX: build_messages(role_def, '查一下 PR #42 的构建状态', history) → list[dict]

    Note over RS: ── 聚合三类工具 ──
    RS->>ENV: get_tool_schemas(role_def.tools) → list[dict]
    RS->>RS: get_agent_tool_schemas() → list[dict]
    RS->>MCP: get_tool_schemas() → list[dict]
    Note over MCP: 包含 mcp__github__get_pull_request 等

    RS->>LLM: chat(messages, tools, model) → LLMResponse
    Note over LLM: tool_calls=[{name:'mcp__github__get_pull_request', arguments:{owner,repo,pull_number:42}}]

    Note over RS,GH: ── MCP 路由执行 ──
    RS->>RS: tool_name.startswith('mcp__') → MCP 路径
    RS->>MCP: call_tool('mcp__github__get_pull_request', {owner,repo,pull_number:42}) → str
    MCP->>MCP: parse name → server='github', tool='get_pull_request'
    MCP->>GH: call_tool('get_pull_request', {owner,repo,pull_number:42}) → str
    GH->>GH: adapter.call_tool() → ClientSession → GitHub API
    GH-->>MCP: PR JSON
    MCP-->>RS: observation = 'PR #42: status=failed, ...'

    RS->>RS: history += [assistant(tool_calls), tool(observation)]
    RS->>LLM: chat(messages+history, tools) → LLMResponse(text='PR #42 构建失败原因...')
    RS-->>RS: TaskResult(success=True, output=text)
```

---

## P1-6. S09 权限拒绝

```mermaid
sequenceDiagram
    autonumber
    participant RS as ReActStepCommand
    participant CTX as ContextService
    participant LLM as LLMService
    participant ENV as EnvService

    RS->>CTX: build_messages(role_def, '删除 /etc/passwd', history) → list[dict]
    RS->>LLM: chat(messages, tools, model) → LLMResponse
    Note over LLM: tool_calls=[{name:'bash', arguments:{command:'rm /etc/passwd'}}]

    RS->>ENV: execute_tool('bash', {command:'rm /etc/passwd'}) → str
    ENV->>ENV: protocol.check_permission('bash') → 'block'
    ENV-->>RS: "Error: Tool 'bash' is blocked"

    RS->>RS: history += [assistant(tool_calls), tool('Error: blocked')]
    RS->>CTX: build_messages(role_def, query, history) → list[dict]
    RS->>LLM: chat(messages, tools, model) → LLMResponse
    Note over LLM: tool_calls=[] → text='抱歉，该操作被权限规则阻止...'
    RS-->>RS: TaskResult(success=True, output=text)
```

---

## P1-7. S10 长对话自动压缩

> 此图展示 ContextService 内部压缩链路，触发点在 build_messages 中。

```mermaid
sequenceDiagram
    autonumber
    participant RS as ReActStepCommand
    participant CTX as ContextService
    participant LLM as LLMService
    participant L0 as L0(rules)
    participant SK as SkillService
    participant Facts as Facts
    participant L1 as L1(insights)
    participant L4 as L4(sessions)

    RS->>CTX: build_messages(role_def, query, history) → list[dict]

    Note over CTX: ── 组装 messages ──
    CTX->>L0: get_all() → dict
    CTX->>SK: get_metadata_prompt(skill_refs) → str
    CTX->>Facts: get_relevant(query, 2000) → str
    CTX->>SK: get_matching(query, skill_refs) → list[Skill]

    Note over CTX: ── compact_if_needed ──
    CTX->>LLM: count_tokens(messages, model) → 14500
    Note over CTX: 14500 > threshold(13000) → 进入压缩

    Note over CTX: Layer 1: _micro_compact(messages)
    CTX->>CTX: 截断远距 tool_result → 移除旧 reminder → 保护最近 2 轮
    CTX->>LLM: count_tokens(compressed, model) → 13500
    Note over CTX: 仍超标

    Note over CTX: Layer 2: _session_compact(messages)
    CTX->>L4: get('summary_{session_id}') → None
    Note over CTX: 无旧摘要，跳过

    Note over CTX,L1: Layer 3 前: _memory_flush(to_compress)
    CTX->>LLM: chat([system(提取insights提示词), user(对话)], 'deepseek/deepseek-chat') → str
    CTX->>L1: set(insight_key, insight_value)
    CTX->>LLM: chat([system(提取facts提示词), user(对话)], 'deepseek/deepseek-chat') → str
    CTX->>Facts: add_fact(fact_key, fact_value)
    Facts->>Facts: _rebuild_index()

    Note over CTX,L4: Layer 3: _full_compact(messages, model)
    CTX->>LLM: chat([system(9段式摘要提示词), user(待压缩消息)], model) → str
    CTX->>L4: set('summary_{session_id}', summary)
    CTX->>CTX: [system] + [summary] + [最近2轮] → compressed

    CTX->>LLM: count_tokens(compressed, model) → 6000
    Note over CTX: 6000 < threshold → 返回
    CTX-->>RS: compressed messages
```

---

## P1-8. S11 跨会话回忆

> S01 的变体：build_messages 时 Facts 检索命中历史信息。

```mermaid
sequenceDiagram
    autonumber
    participant RS as ReActStepCommand
    participant CTX as ContextService
    participant L0 as L0(rules)
    participant SK as SkillService
    participant Facts as Facts(BM25)
    participant LLM as LLMService

    RS->>CTX: build_messages(role_def, '上次帮我改的 CI 配置在哪', []) → list[dict]
    CTX->>CTX: _render_prompt(system_prompt, role_def) → str
    CTX->>L0: get_all() → dict
    CTX->>SK: get_metadata_prompt(skill_refs) → str
    CTX->>Facts: get_relevant('CI 配置', budget=2000) → str
    Note over Facts: BM25 检索命中: "CI 配置路径: .github/workflows/ci.yml，上次修改了 node-version"
    Facts-->>CTX: 事实文本注入到 system prompt

    CTX->>SK: get_matching('CI 配置', skill_refs) → list[Skill]
    CTX-->>RS: messages（system 中包含历史事实）

    RS->>LLM: chat(messages, tools, model) → LLMResponse
    Note over LLM: 据 system 中的事实回答，tool_calls=[]
    RS-->>RS: TaskResult(success=True, output='CI 配置在 .github/workflows/ci.yml...')
```

---

## P1-9. S12 技能自动匹配

> S03 的变体：build_messages 时 SkillService 匹配命中，注入 skill reminder。

```mermaid
sequenceDiagram
    autonumber
    participant RS as ReActStepCommand
    participant CTX as ContextService
    participant SK as SkillService
    participant LLM as LLMService
    participant FS as FileSystem

    RS->>CTX: build_messages(role_def, '帮我做代码审查', []) → list[dict]

    Note over CTX,SK: ── 三段式组装 ──
    CTX->>SK: get_metadata_prompt(skill_refs) → str
    Note over SK: "- code-review: 代码安全审查 (keywords:review,security)"

    CTX->>SK: get_matching('帮我做代码审查', skill_refs) → [Skill(name='code-review')]
    Note over SK: '审查' ∈ trigger_keywords → 命中

    loop 对每个匹配 skill（最多 2 个）
        CTX->>SK: load_skill('code-review') → str
        SK->>FS: read('.agent/skills/code-review/SKILL.md') → str
        SK-->>CTX: skill 正文
        CTX->>CTX: messages.insert(1, {role:'system', content: skill正文})
    end

    Note over CTX: messages = [system静态] + [system skill reminder] + [user query]
    CTX-->>RS: messages

    RS->>LLM: chat(messages, tools, model) → LLMResponse
    Note over LLM: 按 skill 工作流执行：先 read_file → grep → 输出审查报告
```

---

## P1-10. S13 手动触发结晶

```mermaid
sequenceDiagram
    autonumber
    participant Admin as 管理员(HTTP)
    participant Hub
    participant CC as CrystallizeSkillCmd
    participant EP as ExtractPatternCmd
    participant LLM as LLMService
    participant SK as SkillService
    participant L4 as L4(sessions)
    participant L1 as L1(insights)
    participant FS as FileSystem

    Admin->>Hub: POST /api/skills/crystallize {session_id:'abc123'}
    Hub->>CC: CrystallizeSkillCommand(session_id='abc123').__call__()

    CC->>L4: get('abc123') → str
    Note over L4: 返回会话历史 JSON

    CC->>EP: yield ExtractPatternCommand(session_data)
    EP->>LLM: chat([system(提取模式提示词), user(session_data)], model) → LLMResponse
    Note over LLM: 返回 Skill JSON: {name, description, tags, trigger_keywords, body}
    EP-->>CC: pattern dict

    CC->>CC: Skill(name=pattern.name, body_path=f'{name}/SKILL.md', ...)
    CC->>SK: save_skill(skill)
    SK->>SK: protocol.set(skill.name, skill.json()) → skills.db
    SK->>FS: write('.agent/skills/{name}/SKILL.md', body)
    SK->>SK: _skills[name] = skill

    CC->>L1: set(skill.name, skill.description) → memory.db/insights
    CC-->>Hub: {success:True, skill: skill.name}
    Hub-->>Admin: 200 OK
```

---

## P2-1. S14 角色热更新

```mermaid
sequenceDiagram
    autonumber
    participant Admin as 管理员(HTTP)
    participant Hub
    participant UC as UpdateRoleCommand
    participant AS as AgentService

    Admin->>Hub: POST /api/roles/devops/update {updates:{system_prompt:'新提示词'}}
    Hub->>UC: UpdateRoleCommand(role='devops', updates={...}).__call__()

    UC->>AS: _roles.get('devops') → RoleDef
    UC->>UC: 检查: system_prompt ∈ hot_fields → 允许
    UC->>UC: setattr(role_def, 'system_prompt', '新提示词')
    UC->>AS: protocol.set('devops', role_def.json()) → roles.db
    UC->>AS: _roles['devops'] = role_def

    UC-->>Hub: {success:True, updated:['system_prompt']}
    Hub-->>Admin: 200 OK
```

---

## 顺序图 → 接口提取索引

从上述所有箭头中提取的公开方法（步骤 3 的输入）：

| 类 | 方法 | 首次出现 |
|---|------|---------|
| config | `build_config(toml_path) → dict` | S15 |
| config | `load_a2_config(merged) → None` | S15 |
| LLMService | `chat(messages, tools, model, **kw) → LLMResponse` | S01 |
| LLMService | `count_tokens(messages, model) → int` | S01 |
| EnvService | `get_tool_schemas(role_filter) → list[dict]` | S01 |
| EnvService | `execute_tool(name, params) → str` | S02 |
| TerminalProtocol | `check_permission(tool_name) → str` | S02 |
| TerminalProtocol | `read_file(path) → str` | S02 |
| TerminalProtocol | `execute(command, timeout) → str` | S07 |
| SkillService | `get_metadata_prompt(skill_refs) → str` | S01 |
| SkillService | `get_matching(query, skill_refs) → list[Skill]` | S01 |
| SkillService | `load_skill(name) → str` | S12 |
| SkillService | `save_skill(skill) → None` | S13 |
| PlannerService | `save_task_list(task_list, base_dir) → None` | S04 |
| PlannerService | `ensure_tasks_store(base_dir) → MemoryLayerService` | S04 |
| ContextService | `build_messages(role_def, query, history) → list[dict]` | S01 |
| ContextService | `compact_if_needed(messages, model) → list[dict]` | S01 |
| ContextService | `_render_prompt(template, role_def) → str` | S01 |
| ContextService | `_micro_compact(messages) → list[dict]` | S10 |
| ContextService | `_session_compact(messages) → list[dict]` | S10 |
| ContextService | `_memory_flush(messages) → None` | S10 |
| ContextService | `_full_compact(messages, model) → list[dict]` | S10 |
| MCPService | `get_tool_schemas() → list[dict]` | S08 |
| MCPService | `call_tool(full_name, arguments) → str` | S08 |
| MCPServerProtocol | `call_tool(name, arguments) → str` | S08 |
| AgentService | `activate_role(role_name) → ContextService` | S15 |
| AgentService | `get_agent_tool_schemas() → list[dict]` | S01 |
| AgentService | `get_available_description() → str` | S05 |
| MemoryLayerService | `get(key) → str?` | S10 |
| MemoryLayerService | `set(key, value) → None` | S10 |
| MemoryLayerService | `get_all() → dict[str,str]` | S01 |
| GlobalFactsService | `get_relevant(query, budget) → str` | S01 |
| GlobalFactsService | `add_fact(key, value) → None` | S10 |

**缺失类型（需要在步骤 3 中定义）**：

| 类型 | 属性 | 首次出现 |
|------|------|---------|
| `LLMResponse` | text: str, tool_calls: list[ToolCall], usage: dict | S01 |
| `ToolCall` | id: str, name: str, arguments: dict | S02 |
| `TaskResult` | success: bool, output: str | S01 |
| yield chunk | type: str, content/task_id/status | S01 |
