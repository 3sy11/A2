# 03 — 知识管线（Phase 2-3）

> 依赖 Phase 1 叶子服务。记忆 + MCP + 上下文组装。

---

## 一、MemoryLayerService（L0/L1/L4）

### 基类

```python
class MemoryLayerService(AppService):
    depends = []; load_on_start: bool = False

    async def on_start(self):
        self.load_on_start = self.config.get('load_on_start', False)
        if self.load_on_start: await self._preload()

    async def get(self, key: str) -> str | None:
        return await self.protocol.get(key)
    async def set(self, key: str, value: str):
        await self.protocol.set(key, value)
    async def get_all(self) -> dict[str, str]:
        return await self.protocol.get_all() if hasattr(self.protocol, 'get_all') else {}
```

L0/L1/L4 通过 TOML 实例化，各自不同 Protocol，无独立 Python 类。

### TOML

```toml
[a2.memory.l0]
module = "a2.memory.service.MemoryLayerService"
load_on_start = true
[a2.memory.l0.protocol]
module = "bollydog.adapters.file.FileProtocol"
path = ".agent/rules/"

[a2.memory.l1]
module = "a2.memory.service.MemoryLayerService"
[a2.memory.l1.protocol]
module = "bollydog.adapters.kv.KVProtocol"

[a2.memory.l4]
module = "a2.memory.service.MemoryLayerService"
[a2.memory.l4.protocol]
module = "bollydog.adapters.composite.CacheLayer"
flush_threshold = 100
[a2.memory.l4.protocol.protocol]
module = "bollydog.adapters.file.FileProtocol"
path = ".agent/sessions/"
```

---

## 二、L2GlobalFactsService

独立 Service（有 BM25 检索逻辑）：

```python
class L2GlobalFactsService(AppService):
    domain = 'a2'; alias = 'L2GlobalFactsService'; depends = []
    _index: 'bm25s.BM25' = None; _facts: dict[str, str] = {}

    def _rebuild_index(self):
        import bm25s
        corpus = list(self._facts.values())
        if corpus:
            self._index = bm25s.BM25()
            self._index.index(bm25s.tokenize(corpus))

    async def add_fact(self, key: str, value: str):
        self._facts[key] = value
        await self.protocol.set(key, value)
        self._rebuild_index()

    async def search(self, query: str, top_k: int = 5) -> list[str]:
        if not self._index: return []
        import bm25s
        tokens = bm25s.tokenize([query])
        results, scores = self._index.retrieve(tokens, k=min(top_k, len(self._facts)))
        keys = list(self._facts.keys())
        return [self._facts[keys[i]] for i in results[0] if scores[0][i] > 0]

    async def get_relevant(self, query: str, budget: int = 2000) -> str:
        facts = await self.search(query)
        result, total = [], 0
        for f in facts:
            est = len(f) // 4
            if total + est > budget: break
            result.append(f); total += est
        return '\n'.join(result)
```

### TOML

```toml
[a2.L2GlobalFactsService]
module = "a2.memory.l2.service.L2GlobalFactsService"
[a2.L2GlobalFactsService.protocol]
module = "bollydog.adapters.kv.KVProtocol"
```

### Files

`memory/service.py`（基类）、`memory/l2/service.py`（L2）、`memory/commands.py`（NotifyIndexUpdateCommand）

---

## 三、MCPService

### 架构

```
MCPService(AppService), depends=["a2.ToolService"]
├── _servers: dict[str, MCPServerProtocol]
├── on_start → 连接所有服务器 → 工具注册到 ToolService
└── on_stop → 断开

MCPServerProtocol(Protocol) → adapter = mcp.ClientSession
├── on_start → stdio_client/sse_client → ClientSession.initialize()
├── list_tools() → tool 列表
└── call_tool(name, args) → 结果

MCPToolWrapper(Tool) → 委托到 Protocol
├── name = "mcp__{server}__{tool}"
└── execute(**kw) → server_protocol.call_tool()
```

### MCPServerProtocol

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPServerProtocol(Protocol):
    name: str = ''; command: str = ''; args: list[str] = []
    env: dict[str, str] = {}; transport: str = 'stdio'

    async def on_start(self):
        if self.transport == 'stdio':
            params = StdioServerParameters(command=self.command, args=self.args, env={**os.environ, **self.env})
            self._ctx = stdio_client(params)
            read, write = await self._ctx.__aenter__()
        else:
            from mcp.client.sse import sse_client
            self._ctx = sse_client(self.command)
            read, write = await self._ctx.__aenter__()
        self.adapter = ClientSession(read, write)
        await self.adapter.initialize()

    async def list_tools(self) -> list[dict]:
        result = await self.adapter.list_tools()
        return [{'name': t.name, 'description': t.description or '', 'inputSchema': t.inputSchema or {}} for t in result.tools]

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self.adapter.call_tool(name, arguments)
        return '\n'.join(c.text for c in result.content if hasattr(c, 'text'))
```

### 执行路径

```
内置: Tool → ToolService.execute() → EnvService → Protocol
MCP:  Tool → ToolService.execute() → MCPToolWrapper → MCPServerProtocol.call_tool()
两者统一走权限检查。
```

### TOML

```toml
[a2.MCPService]
module = "a2.mcp.service.MCPService"
[a2.MCPService.servers.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_TOKEN = "${GITHUB_TOKEN}" }
```

### Files

`mcp/service.py`、`mcp/server.py`（MCPServerProtocol）、`mcp/wrapper.py`（MCPToolWrapper）

---

## 四、ContextService

### 架构

```
ContextService(AppService)
depends: ["a2.memory.l0", "a2.memory.l1", "a2.L2GlobalFactsService",
          "a2.SkillService", "a2.memory.l4", "a2.LLMService"]

├── build_messages(role_def, query, history) → list[dict]
├── compact_if_needed(messages, model) → list[dict]
└── count_tokens(messages, model) → int
```

### build_messages

```python
async def build_messages(self, role_def, query, history) -> list[dict]:
    parts = [DEFAULT_SYSTEM_PROMPT, role_def.system_prompt]
    l0_data = await self._l0.get_all()
    if l0_data: parts.append('\n'.join(l0_data.values()))
    parts.append(self._skill.get_metadata_prompt())
    relevant = await self._l2.get_relevant(query, budget=2000)
    if relevant: parts.append(f"# Relevant Context\n{relevant}")

    messages = [{'role': 'system', 'content': '\n\n'.join(parts)}] + history

    # System Reminder: L3 技能正文
    for s in self._skill.get_matching(query)[:2]:
        body = await self._skill.load_skill(s.name)
        messages.insert(1, {'role': 'system', 'content': body})

    return await self.compact_if_needed(messages, role_def.model)
```

### 三层压缩

| 层 | 机制 | 成本 |
|----|------|------|
| MicroCompact | 规则截断：远距 tool_result→200字符、旧 reminder→移除、保护最近 2 轮 | 零 |
| SessionMemory | 已有摘要 → 替换旧消息区段 | 零 |
| FullLLMCompact | LLM 生成 9 段式摘要 | ~$0.01 |

9 段式摘要：Primary intent / Key concepts / Relevant files / Errors / Attempted approaches / What worked / Current state / Open questions / User preferences

### 配置

| 参数 | 默认值 |
|------|--------|
| `token_budget` | 128000 |
| `compact_threshold` | 13000 |
| `micro_compact_distance` | 10 |

### TOML

```toml
[a2.ContextService]
module = "a2.context.service.ContextService"
token_budget = 128000
compact_threshold = 13000
micro_compact_distance = 10
```

### Files

`context/service.py`
