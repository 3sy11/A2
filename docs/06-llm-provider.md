# 06 — LLM Provider

## Context

LLM Provider 抽象 LLM API 层，兼容 OpenAI SDK 格式（DeepSeek/OpenAI/vLLM 以及大多数厂商都支持）。处理流式输出、重试逻辑、模型降级和 token 用量追踪。

## 核心设计：两层选择机制

Tool 可以用两种方式指定 LLM，优先级从高到低：

```
tool.provider = 'claude-opus'     → 直接用该 provider 实例（最精确）
tool.capability = 'capable'       → 路由到对应能力服务（较模糊）
都没指定                          → 用 Agent Loop 当前默认 capability
```

与工具系统的"环境即服务"完全对称：

```
工具系统:                                  LLM 系统:
ToolService(AppService) 门面               LLMService(AppService) 门面
├── _envs: {local, docker, ssh}            ├── _providers: {claude-opus, deepseek-v3, ...}
├── Tool.env = 'local'                     ├── Tool.provider = 'claude-opus'
├── Tool 无 env → 用默认                    ├── Tool.capability = 'capable'
│                                          │
ToolService.execute(name, env=)            LLMService.chat(msgs, provider=, capability=)
    → _envs[env].execute()                     → _providers[provider].chat()
                                               → _capabilities[capability].chat()
```

## 为什么 LLMProvider 是 Protocol

| 维度 | KVProtocol | FileProtocol | TerminalProtocol | **LLMProvider** |
|------|-----------|-------------|-----------------|----------------|
| self.adapter | redis client | file handle | container_id | **API client (AsyncOpenAI)** |
| 操作方法 | get/set | read/write | execute/read_file | **chat/stream_chat** |
| 资源管理 | connect/disconnect | open/close | start/stop | **create/close client** |
| 适配目标 | KV 存储 | 文件系统 | 执行环境 | **LLM API** |

## 架构

```
LLMService(AppService) 门面
├── depends: ["a2.llm.fast.FastLLMService"]
├── depends: ["a2.llm.capable.CapableLLMService"]
├── depends: ["a2.llm.code.CodeLLMService"]
│
├── _providers: dict[str, LLMProvider]      # provider 名称 → Protocol 实例
├── _capabilities: dict[str, AppService]    # capability 名称 → 能力服务
├── _cap_map: dict[str, list[str]]          # capability → [provider names]
│
├── resolve(provider, capability) → LLMProvider   ← 两层解析
├── chat(messages, tools, stream, provider, capability)
├── stream_chat(messages, tools, provider, capability)
└── get_usage() → 合并所有 provider 的 token 用量

能力服务 (AppService, 每个能力级别一个)
├── protocol: ModelFallbackProvider             ← Protocol 链（熔断降级）
│   ├── primary: 具体 Provider                   ← 主模型
│   └── fallback: 具体 Provider                  ← 备用模型
│
├── chat(messages, tools, stream) → 委托给 protocol
├── stream_chat(messages, tools) → 委托给 protocol
└── get_usage() → token 用量
```

## 两层解析逻辑

```python
async def resolve(self, provider: str = None, capability: str = None) -> LLMProvider:
    """解析优先级：provider > capability > default。"""
    # 1. 直接指定 provider（最精确）
    if provider and provider in self._providers:
        return self._providers[provider]

    # 2. 通过 capability 路由到能力服务
    cap = capability or self.config.get('default_capability', 'fast')
    if cap in self._capabilities:
        return self._capabilities[cap]

    # 3. 兜底
    return self._capabilities.get('fast')
```

## ModelFallbackProvider — 熔断降级

Protocol 包装器，实现**熔断降级模式**：

```
请求 → primary.chat()
       ├── 成功 → 重置连续失败计数器，返回结果
       └── 异常 → 连续失败计数器 +1
                  ├── 计数 < 阈值(3) → 重试 primary
                  └── 计数 >= 阈值(3) → 切换到 fallback.chat()
                                         ├── 成功 → 返回结果
                                         └── 异常 → 抛出错误
```

每个能力服务内部都有自己的 ModelFallbackProvider，互不影响。

## Tool 声明 LLM 需求

Tool 基类支持两种声明方式：

```python
class Tool(BaseCommand, abstract=True):
    name: ClassVar[str]
    description: ClassVar[str]
    provider: ClassVar[str] = None      # 直接指定 provider 名称（精确）
    capability: ClassVar[str] = None    # 指定能力级别（模糊）
    ...
```

| 工具 | provider | capability | 解析结果 |
|------|----------|-----------|---------|
| code_review | `'claude-opus'` | — | 直接用 claude-opus provider |
| search | — | `'fast'` | 路由到 FastLLMService |
| bash | — | — | 用当前默认 capability |
| read/write/edit | — | — | 文件操作不需要 LLM |

## Agent Loop 中的解析

```python
# AgentCommand.__call__() 中：

for tool_call in tool_calls:
    tool = tool_service.get_tool(tool_call.name)

    # 优先级：tool.provider > tool.capability > 当前默认
    if tool.provider:
        # 直接指定 provider → spawn 子智能体用该 provider
        result = await subagent_service.spawn(
            task=f"Execute: {tool_call.name}({tool_call.params})",
            agent_type='general',
            provider=tool.provider,
        )
    elif tool.capability and tool.capability != current_capability:
        # 指定 capability 且不同于当前 → spawn 子智能体
        result = await subagent_service.spawn(
            task=f"Execute: {tool_call.name}({tool_call.params})",
            agent_type='general',
            capability=tool.capability,
        )
    else:
        # 无特殊要求 → 直接执行
        result = await tool_service.execute(tool_call.name, tool_call.params)
```

## Class Signatures

### LLMProvider (Protocol 基类)

```python
class LLMProvider(Protocol, abstract=True):
    """LLM API 适配器基类。继承 Protocol，复用生命周期管理。"""
    model: str
    api_key: str
    base_url: Optional[str]
    max_retries: int = 3
    timeout: int = 120
    input_tokens: int = 0
    output_tokens: int = 0

    async def chat(self, messages: list[dict], tools: list[dict] = None,
                   stream: bool = False, model: str = None) -> 'LLMResponse':
        raise NotImplementedError

    async def stream_chat(self, messages: list[dict], tools: list[dict] = None,
                          model: str = None) -> AsyncGenerator[dict, None]:
        raise NotImplementedError

    def get_usage(self) -> dict:
        return {'input_tokens': self.input_tokens, 'output_tokens': self.output_tokens}
```

### OpenAIProvider

```python
class OpenAIProvider(LLMProvider):
    """OpenAI 兼容 provider（适用于 OpenAI / DeepSeek / vLLM 等）。"""

    async def on_start(self):
        self.adapter = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    async def on_stop(self):
        await self.adapter.close()

    async def chat(self, messages, tools=None, stream=False, model=None):
        target_model = model or self.model
        for attempt in range(self.max_retries):
            try:
                response = await self.adapter.chat.completions.create(
                    model=target_model, messages=messages, tools=tools,
                    stream=stream, timeout=self.timeout)
                self._track_usage(response)
                return response
            except RateLimitError:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt * 1.0)
                else:
                    raise

    async def stream_chat(self, messages, tools=None, model=None):
        target_model = model or self.model
        stream = await self.adapter.chat.completions.create(
            model=target_model, messages=messages, tools=tools, stream=True)
        async for chunk in stream:
            yield chunk

    def _track_usage(self, response):
        if hasattr(response, 'usage') and response.usage:
            self.input_tokens += response.usage.prompt_tokens
            self.output_tokens += response.usage.completion_tokens
```

### AnthropicProvider

```python
class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider。"""

    async def on_start(self):
        self.adapter = AsyncAnthropic(api_key=self.api_key)

    async def on_stop(self):
        await self.adapter.close()

    async def chat(self, messages, tools=None, stream=False, model=None):
        target_model = model or self.model
        system, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

        for attempt in range(self.max_retries):
            try:
                response = await self.adapter.messages.create(
                    model=target_model, system=system, messages=anthropic_messages,
                    tools=anthropic_tools, max_tokens=8096)
                return self._to_openai_format(response)
            except RateLimitError:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise

    def _convert_messages(self, messages):
        system = ''
        converted = []
        for msg in messages:
            if msg['role'] == 'system':
                system = msg['content']
            else:
                converted.append(msg)
        return system, converted

    def _to_openai_format(self, response):
        ...
```

### ModelFallbackProvider

```python
class ModelFallbackProvider(LLMProvider):
    """Protocol 包装器：主模型连续失败时自动降级到备用模型。"""
    primary: LLMProvider
    fallback: LLMProvider
    consecutive_failures: int = 0
    fallback_threshold: int = 3

    async def on_start(self):
        await self.primary.on_start()
        await self.fallback.on_start()

    async def on_stop(self):
        await self.primary.on_stop()
        await self.fallback.on_stop()

    async def chat(self, messages, tools=None, stream=False, model=None):
        provider = self.primary if self.consecutive_failures < self.fallback_threshold else self.fallback
        try:
            result = await provider.chat(messages, tools, stream, model)
            self.consecutive_failures = 0
            return result
        except Exception:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.fallback_threshold:
                return await self.fallback.chat(messages, tools, stream, model)
            raise

    async def stream_chat(self, messages, tools=None, model=None):
        provider = self.primary if self.consecutive_failures < self.fallback_threshold else self.fallback
        try:
            async for chunk in provider.stream_chat(messages, tools, model):
                yield chunk
            self.consecutive_failures = 0
        except Exception:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.fallback_threshold:
                async for chunk in self.fallback.stream_chat(messages, tools, model):
                    yield chunk
            else:
                raise
```

### 能力服务

```python
class FastLLMService(AppService):
    """快速能力：适合简单任务、上下文压缩、摘要。"""
    domain = 'a2.llm'
    alias = 'FastLLMService'
    depends = []

    async def chat(self, messages, tools=None, stream=False, model=None):
        return await self.protocol.chat(messages, tools, stream, model)

    async def stream_chat(self, messages, tools=None, model=None):
        async for chunk in self.protocol.stream_chat(messages, tools, model):
            yield chunk

    def get_usage(self):
        return self.protocol.get_usage()


class CapableLLMService(AppService):
    """强推理能力：适合复杂推理、代码审查、架构设计。"""
    domain = 'a2.llm'
    alias = 'CapableLLMService'
    depends = []

    async def chat(self, messages, tools=None, stream=False, model=None):
        return await self.protocol.chat(messages, tools, stream, model)

    async def stream_chat(self, messages, tools=None, model=None):
        async for chunk in self.protocol.stream_chat(messages, tools, model):
            yield chunk

    def get_usage(self):
        return self.protocol.get_usage()


class CodeLLMService(AppService):
    """代码能力：适合代码生成、重构、调试。"""
    domain = 'a2.llm'
    alias = 'CodeLLMService'
    depends = []

    async def chat(self, messages, tools=None, stream=False, model=None):
        return await self.protocol.chat(messages, tools, stream, model)

    async def stream_chat(self, messages, tools=None, model=None):
        async for chunk in self.protocol.stream_chat(messages, tools, model):
            yield chunk

    def get_usage(self):
        return self.protocol.get_usage()
```

### LLMService (门面)

```python
class LLMService(AppService):
    """LLM 门面：两层选择——provider（精确）> capability（模糊）。"""
    domain = 'a2'
    alias = 'LLMService'
    depends = [
        'a2.llm.fast.FastLLMService',
        'a2.llm.capable.CapableLLMService',
        'a2.llm.code.CodeLLMService',
    ]

    # provider 注册表（在 on_start 中从 config 构建）
    _providers: dict[str, LLMProvider] = {}
    # capability → 能力服务
    _capabilities: dict[str, AppService] = {}
    # capability → [provider names]（用于 get_usage 汇总）
    _cap_map: dict[str, list[str]] = {}

    async def on_start(self):
        self._capabilities = {
            'fast': self._fast,
            'capable': self._capable,
            'code': self._code,
        }
        # 从 config 构建 provider 注册表
        for name, conf in self.config.get('providers', {}).items():
            provider = _build_protocol(conf)  # bollydog 标准构建
            await provider.on_start()
            self._providers[name] = provider
        # capability → provider 映射
        self._cap_map = self.config.get('capabilities', {})

    async def resolve(self, provider: str = None, capability: str = None):
        """两层解析：provider > capability > default。"""
        # 1. 直接指定 provider
        if provider and provider in self._providers:
            return self._providers[provider]
        # 2. capability 路由
        cap = capability or self.config.get('default_capability', 'fast')
        if cap in self._capabilities:
            return self._capabilities[cap]
        # 3. 兜底
        return self._capabilities.get('fast')

    async def chat(self, messages, tools=None, stream=False,
                   model=None, provider=None, capability=None):
        target = await self.resolve(provider, capability)
        return await target.chat(messages, tools, stream, model)

    async def stream_chat(self, messages, tools=None,
                          model=None, provider=None, capability=None):
        target = await self.resolve(provider, capability)
        async for chunk in target.stream_chat(messages, tools, model):
            yield chunk

    def get_usage(self) -> dict:
        """合并所有 provider 的 token 用量。"""
        total = {'input_tokens': 0, 'output_tokens': 0}
        for p in self._providers.values():
            usage = p.get_usage()
            total['input_tokens'] += usage.get('input_tokens', 0)
            total['output_tokens'] += usage.get('output_tokens', 0)
        return total
```

## TOML 配置

```toml
# 注册所有 provider 实例（命名）
["a2.llm.LLMService".providers.claude-opus]
module = "a2.llm.provider.AnthropicProvider"
model = "claude-opus-4-0"
api_key = "${ANTHROPIC_API_KEY}"

["a2.llm.LLMService".providers.deepseek-v3]
module = "a2.llm.provider.OpenAIProvider"
model = "deepseek-chat"
api_key = "${DEEPSEEK_API_KEY}"
base_url = "https://api.deepseek.com"

["a2.llm.LLMService".providers.deepseek-coder]
module = "a2.llm.provider.OpenAIProvider"
model = "deepseek-coder"
api_key = "${DEEPSEEK_API_KEY}"
base_url = "https://api.deepseek.com"

["a2.llm.LLMService".providers.qwen-turbo]
module = "a2.llm.provider.OpenAIProvider"
model = "qwen-turbo"
api_key = "${QWEN_API_KEY}"

# 能力分组（引用 provider 名称）
["a2.llm.LLMService".capabilities]
fast = ["deepseek-v3", "qwen-turbo"]
capable = ["claude-opus"]
code = ["deepseek-coder"]

["a2.llm.LLMService"]
default_capability = "fast"

# 能力服务配置（通过 _build_protocol 构建 Protocol 链）
["a2.llm.fast.FastLLMService".protocol]
module = "a2.llm.fallback.ModelFallbackProvider"
fallback_threshold = 3

["a2.llm.fast.FastLLMService".protocol.primary]
module = "a2.llm.provider.OpenAIProvider"
model = "deepseek-chat"
api_key = "${DEEPSEEK_API_KEY}"
base_url = "https://api.deepseek.com"

["a2.llm.fast.FastLLMService".protocol.fallback]
module = "a2.llm.provider.OpenAIProvider"
model = "qwen-turbo"
api_key = "${QWEN_API_KEY}"

["a2.llm.capable.CapableLLMService".protocol]
module = "a2.llm.fallback.ModelFallbackProvider"

["a2.llm.capable.CapableLLMService".protocol.primary]
module = "a2.llm.provider.AnthropicProvider"
model = "claude-opus-4-0"
api_key = "${ANTHROPIC_API_KEY}"

["a2.llm.capable.CapableLLMService".protocol.fallback]
module = "a2.llm.provider.OpenAIProvider"
model = "deepseek-reasoner"
api_key = "${DEEPSEEK_API_KEY}"

["a2.llm.code.CodeLLMService".protocol]
module = "a2.llm.fallback.ModelFallbackProvider"

["a2.llm.code.CodeLLMService".protocol.primary]
module = "a2.llm.provider.OpenAIProvider"
model = "deepseek-coder"
api_key = "${DEEPSEEK_API_KEY}"
```

## 完整请求流程

```
用户: "Review this code and then run the tests"

Agent Loop (current_capability = 'fast'):
    │
    ├── LLMService.chat(capability='fast')
    │   → FastLLMService.chat() → DeepSeek-V3
    │   → tool_calls: [
    │       {name: 'code_review', args: {code: ...}},
    │       {name: 'bash', args: {command: 'pytest'}},
    │   ]
    │
    ├── code_review:
    │   ├── tool.provider = 'claude-opus'（直接指定）
    │   ├── spawn subagent (provider='claude-opus')
    │   │   └── LLMService.resolve('claude-opus')
    │   │       → _providers['claude-opus'] → AnthropicProvider
    │   │       → Claude Opus 返回 review 结果
    │   └── result → 追加到 messages
    │
    ├── bash:
    │   ├── tool.provider = None, tool.capability = None
    │   ├── 直接执行（用当前 'fast' 的 LLM 做后续推理）
    │   └── result → 追加到 messages
    │
    └── LLMService.chat(capability='fast')
        → 最终回复
```

## Files to Create

| File | Purpose |
|------|---------|
| `llm/__init__.py` | Exports |
| `llm/service.py` | LLMService(AppService) 门面：provider 注册表 + 两层解析 |
| `llm/provider.py` | LLMProvider(Protocol) + OpenAIProvider + AnthropicProvider |
| `llm/fallback.py` | ModelFallbackProvider(Protocol): 熔断降级包装器 |
| `llm/fast/service.py` | FastLLMService(AppService): 快速能力 |
| `llm/capable/service.py` | CapableLLMService(AppService): 强推理能力 |
| `llm/code/service.py` | CodeLLMService(AppService): 代码能力 |

## Dependencies

- `bollydog.models.protocol.Protocol` — LLMProvider 基类
- `bollydog.models.service.AppService` — LLMService / 能力服务基类
- `openai` Python SDK — OpenAIProvider
- `anthropic` Python SDK（可选）— AnthropicProvider
