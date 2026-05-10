# 05 — 上下文管理

## Context

上下文管理是 Agent 框架的核心——字面意义上的"上下文工程"。ContextService 编排每轮 LLM 调用时看到的内容，平衡信息丰富度与 token 预算。实现 Claude Code 的 3 层压缩系统：MicroCompact（规则驱动）、Session Memory（复用摘要）、Full LLM Compact（LLM 生成结构化摘要）。

**核心设计决策：ContextService(AppService) 直接实现上下文管理逻辑。** 通过 depends 依赖 MemoryService（L0-L4 内容）和 LLMService（压缩时 LLM 调用）。

## 架构

```
ContextService(AppService)
├── depends: ["a2.MemoryService"]     ← 获取 L0-L4 内容
├── depends: ["a2.LLMService"]        ← 压缩时 LLM 调用
│
├── build_context(messages) → list[dict]    ← 组装完整上下文
├── update(messages)                        ← 持久化到 L4
├── compact(messages) → list[dict]          ← 3 层压缩
│
├── _micro_compact(messages)                ← Layer 1: 规则驱动，无 LLM
├── _session_memory_compact(messages)       ← Layer 2: 复用摘要，零成本
├── _full_llm_compact(messages)             ← Layer 3: LLM 生成结构化摘要
│
├── token_budget: int = 128000              ← 最大上下文窗口
├── reserved_tokens: int = 8000             ← 为回复预留
├── compact_threshold: int = 13000          ← 触发压缩的缓冲区
└── _session_memory: str | None             ← 缓存的会话摘要

3 层压缩管线：
    Layer 1: MicroCompact     — 规则驱动，无 LLM，即时完成
    Layer 2: Session Memory   — 复用已有摘要，零 LLM 成本
    Layer 3: Full LLM Compact — LLM 生成结构化摘要（9 段式模板）
```

### 与框架模式的对齐

```
LLM 系统:                                  工具系统:                    上下文系统:
LLMService(AppService) 门面               ToolService(AppService) 门面  ContextService(AppService)
├── depends: 3 个能力服务                   ├── depends: 3 个环境服务      ├── depends: MemoryService
│                                          │                             ├── depends: LLMService
└── chat(provider, capability)              └── execute(name, env)        └── build_context(messages)
```

ContextService 是业务服务：
- 通过 MemoryService 获取 L0-L4 内容（服务间调用）
- 通过 LLMService 做压缩时的 LLM 调用（capability 路由）
- 3 层压缩是 ContextService 的内部逻辑

## build_context 上下文组装流程

```
ContextService.build_context(messages):

    1. 可用预算 = token_budget - reserved_tokens

    2. 从 MemoryService 获取 L0-L3 内容：
       mem_context = MemoryService.get_context(query, budget // 3)
       → L0: 系统规则
       → L1: 技能索引
       → L2: 相关事实
       → L3: 匹配技能

    3. 组装 system prompt：
       system_prompt = L0 + L1 + L2 + L3

    4. 合并 session messages：
       [system: system_prompt] + [user/assistant/tool: ...]

    5. 检查是否需要压缩：
       如果 total_tokens + compact_threshold > token_budget
       → 触发 3 层压缩管线

    6. 返回 messages[]
```

## 3 层压缩管线

```
messages 进入压缩管线
    │
    ├── Layer 1: MicroCompact（始终先执行）
    │   ├── 截断旧 tool 输出（保留最近 5 条）
    │   ├── 替换图片为 token 估算
    │   ├── 移除旧 system-reminder 标签
    │   └── 结果: 压缩后 messages
    │       ├── 仍然超预算 → 继续 Layer 2
    │       └── 已在预算内 → 返回
    │
    ├── Layer 2: Session Memory Compact
    │   ├── 条件: 有缓存摘要 且 messages > 10K tokens
    │   ├── 用缓存摘要替换旧消息
    │   └── 结果: 压缩后 messages
    │       ├── 仍然超预算 → 继续 Layer 3
    │       └── 已在预算内 → 返回
    │
    └── Layer 3: Full LLM Compact
        ├── 通过 LLMService.chat(capability='fast') 调用 LLM
        ├── 9 段式模板: Intent/Concepts/Files/Errors/...
        ├── 隐式 CoT: <analysis> 内思考，<summary> 输出
        ├── 缓存摘要到 _session_memory（供 Layer 2 复用）
        └── 返回压缩后 messages
```

## Class Signatures

### ContextService (AppService)

```python
class ContextService(AppService):
    """上下文管理服务：组装 L0-L4 内容、3 层压缩、token 预算控制。"""
    domain = 'a2'
    alias = 'ContextService'
    depends = ['a2.MemoryService', 'a2.LLMService']

    token_budget: int = 128000
    reserved_tokens: int = 8000
    compact_threshold: int = 13000

    _session_memory: str | None = None
    _turn_count: int = 0

    async def build_context(self, messages: list[dict]) -> list[dict]:
        """组装完整上下文供 LLM 调用。"""
        available_budget = self.token_budget - self.reserved_tokens

        # 从 MemoryService 获取 L0-L3 内容
        query = self._extract_query(messages)
        mem_context = await self._memory.get_context(query, token_budget=available_budget // 3)

        # 组装 system prompt
        system_sections = [
            mem_context.get('meta_rules', ''),
            mem_context.get('skill_index', ''),
            mem_context.get('facts', ''),
            mem_context.get('skills', ''),
        ]
        system_prompt = '\n\n'.join(filter(None, system_sections))

        # 合并 session messages
        session_messages = messages.copy()
        if session_messages and session_messages[0]['role'] == 'system':
            session_messages[0]['content'] = system_prompt + '\n\n' + session_messages[0]['content']
        else:
            session_messages.insert(0, {'role': 'system', 'content': system_prompt})

        # 检查是否需要压缩
        total_tokens = self._count_tokens(session_messages)
        if total_tokens + self.compact_threshold > self.token_budget:
            session_messages = await self.compact(session_messages)

        return session_messages

    async def update(self, messages: list[dict]):
        """持久化消息到 L4 会话档案。"""
        await self._memory.append_message(messages[-1])
        self._turn_count += 1

    async def compact(self, messages: list[dict]) -> list[dict]:
        """3 层压缩管线：从最便宜的开始，逐级升级。"""
        # Layer 1: MicroCompact（始终先执行）
        messages = self._micro_compact(messages)
        if self._count_tokens(messages) + self.compact_threshold <= self.token_budget:
            return messages

        # Layer 2: Session Memory Compact
        if self._session_memory and self._count_tokens(messages) > 10000:
            messages = self._session_memory_compact(messages)
            if self._count_tokens(messages) + self.compact_threshold <= self.token_budget:
                return messages

        # Layer 3: Full LLM Compact
        messages = await self._full_llm_compact(messages)
        return messages
```

### Layer 1: MicroCompact

```python
def _micro_compact(self, messages: list[dict]) -> list[dict]:
    """规则驱动压缩，无 LLM 调用。
    - 截断旧 tool 输出（保留最近 N 条）
    - 替换图片为 token 估算
    - 移除旧 system-reminder 标签
    """
    COMPACTABLE_TOOLS = {'bash', 'read', 'grep', 'glob'}
    KEEP_RECENT_RESULTS = 5

    tool_results = []
    for i, msg in enumerate(messages):
        if msg['role'] == 'tool':
            tool_results.append(i)

    # 截断旧 tool 输出
    if len(tool_results) > KEEP_RECENT_RESULTS:
        old_results = tool_results[:-KEEP_RECENT_RESULTS]
        for idx in old_results:
            content = messages[idx]['content']
            if len(content) > 500:
                messages[idx]['content'] = content[:500] + '\n... [truncated by MicroCompact]'

    # 移除旧 system-reminder
    for msg in messages[:-3]:  # 最近 3 条不动
        if isinstance(msg.get('content'), str):
            msg['content'] = re.sub(
                r'<system-reminder>.*?</system-reminder>',
                '[system-reminder removed]',
                msg['content'],
                flags=re.DOTALL
            )

    return messages
```

### Layer 2: Session Memory Compact

```python
def _session_memory_compact(self, messages: list[dict]) -> list[dict]:
    """用缓存摘要替换旧消息。零 LLM 成本——复用已有摘要。"""
    KEEP_RECENT = 6
    SM_MIN_TOKENS = 10000
    SM_MIN_MESSAGES = 5

    if self._count_tokens(messages) < SM_MIN_TOKENS:
        return messages
    if len(messages) < SM_MIN_MESSAGES:
        return messages

    # 分割：旧消息（待压缩）+ 最近消息（保留）
    old_messages = messages[:-KEEP_RECENT]
    recent_messages = messages[-KEEP_RECENT:]

    # 用摘要替换旧消息
    summary_message = {
        'role': 'user',
        'content': f'<system-reminder>\nPrevious conversation summary:\n{self._session_memory}\n</system-reminder>'
    }

    # 保留第一条消息（system prompt）+ 摘要 + 最近消息
    return [messages[0], summary_message] + recent_messages
```

### Layer 3: Full LLM Compact

```python
async def _full_llm_compact(self, messages: list[dict]) -> list[dict]:
    """LLM 生成结构化摘要，遵循 Claude Code 的 9 段式模板。
    使用隐式 CoT：模型在 <analysis> 中思考，在 <summary> 中输出。"""
    COMPACT_PROMPT = """NO_TOOLS_PREAMBLE: You are about to compact a conversation. DO NOT call any tools. Tool calls will be rejected and waste your one chance.

Summarize this conversation into a structured format. First analyze in <analysis> tags, then output the summary in <summary> tags.

Required sections:
1. Primary Request and Intent
2. Key Technical Concepts
3. Files and Code Sections
4. Errors and Fixes
5. Problem Solving
6. All User Messages (verbatim)
7. Pending Tasks
8. Current Work
9. Optional Next Step

<analysis>
[Your analysis here]
</analysis>

<summary>
[Your structured summary here]
</summary>"""

    KEEP_RECENT = 4
    old_messages = messages[:-KEEP_RECENT]
    recent_messages = messages[-KEEP_RECENT:]

    # 通过 LLMService 调用 LLM
    compact_messages = [
        {'role': 'system', 'content': COMPACT_PROMPT},
        {'role': 'user', 'content': json.dumps(old_messages)},
    ]
    response = await self._llm.chat(
        messages=compact_messages,
        capability='fast',  # 轻量模型即可
    )

    # 提取摘要（去除 <analysis> 块）
    summary = self._extract_summary(response.content)
    self._session_memory = summary  # 缓存供 Layer 2 复用

    summary_message = {
        'role': 'user',
        'content': f'<system-reminder>\nConversation summary (auto-compact):\n{summary}\n</system-reminder>'
    }

    return [messages[0], summary_message] + recent_messages
```

## Token 计数

```python
def _count_tokens(self, messages: list[dict]) -> int:
    """估算 token 数量。
    - 文本: ~4 字符/token（英文）
    - 图片: 固定 2000 token 估算
    - Tool 输出: 内容长度 / 4
    """
    total = 0
    for msg in messages:
        content = msg.get('content', '')
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if block.get('type') == 'image':
                    total += 2000
                elif block.get('type') == 'text':
                    total += len(block.get('text', '')) // 4
    return total
```

## 9 段式摘要模板

Full LLM Compact 生成的结构化摘要包含 9 个部分：

| 段落 | 内容 | 用途 |
|------|------|------|
| 1. Primary Request and Intent | 用户的主要请求和意图 | 理解任务目标 |
| 2. Key Technical Concepts | 关键技术概念 | 保持技术上下文 |
| 3. Files and Code Sections | 涉及的文件和代码 | 文件追踪 |
| 4. Errors and Fixes | 遇到的错误和修复 | 避免重复错误 |
| 5. Problem Solving | 问题解决过程 | 保持决策链 |
| 6. All User Messages | 用户消息原文 | 不丢失用户意图 |
| 7. Pending Tasks | 待办任务 | 保持工作连续性 |
| 8. Current Work | 当前工作 | 知道在哪中断 |
| 9. Optional Next Step | 下一步建议 | 快速恢复 |

## TOML 配置

```toml
["a2.ContextService"]
module = "a2.context.service.ContextService"
token_budget = 128000
reserved_tokens = 8000
compact_threshold = 13000
```

## Files to Create

| File | Purpose |
|------|---------|
| `context/__init__.py` | Exports |
| `context/service.py` | ContextService(AppService)：上下文组装、3 层压缩、token 预算 |

## Dependencies

- `a2.MemoryService` — 获取 L0-L4 内容
- `a2.LLMService` — 压缩时 LLM 调用（capability='fast'）
