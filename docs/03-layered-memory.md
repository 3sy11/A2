# 03 — 分层记忆系统

## Context

分层记忆是 GenericAgent 的核心创新——5 层记忆层级（L0-L4），实现高效上下文使用（<30K tokens）的同时保持丰富回忆。每层有不同的持久化策略、访问模式和 token 成本。

**核心设计决策：每层是独立的 AppService，各自管理自己的 Protocol。MemoryService 作为门面，通过 depends 声明依赖所有子服务。** 与 LLMService / ToolService 的"门面 → 子服务 → Protocol"模式完全对称。

## 架构

```
MemoryService(AppService) 门面
├── depends: ["a2.memory.l0.L0MetaRulesService"]
├── depends: ["a2.memory.l1.L1InsightIndexService"]
├── depends: ["a2.memory.l2.L2GlobalFactsService"]
├── depends: ["a2.memory.l3.L3TaskSkillsService"]
└── depends: ["a2.memory.l4.L4SessionArchiveService"]
│
├── get_context(query, token_budget) → dict    ← 组装所有层的上下文
├── add_fact(category, content)                ← 委托 L2
├── add_skill(skill)                           ← 委托 L3（L3 内部通知 L1）
└── append_message(message)                    ← 委托 L4

子服务 (AppService, 每层一个)
├── L0MetaRulesService    → protocol: KVProtocol (启动时扫描 CLAUDE.md)
├── L1InsightIndexService → protocol: KVProtocol (技能元数据路由表)
├── L2GlobalFactsService  → protocol: KVProtocol (全局事实 + 检索)
├── L3TaskSkillsService   → protocol: FileProtocol (技能 .md 文件)
│   └── depends: ["a2.memory.l1.L1InsightIndexService"]  ← 结晶时更新索引
└── L4SessionArchiveService → protocol: CacheLayer → FileProtocol (会话历史)

与工具系统 / LLM 系统完全对称的"门面 → 子服务 → Protocol"模式：

工具系统:                                  LLM 系统:                    记忆系统:
ToolService(AppService) 门面               LLMService(AppService) 门面  MemoryService(AppService) 门面
├── depends: 3 个环境服务                   ├── depends: 3 个能力服务      ├── depends: 5 个层级服务
│                                          │                             │
├── LocalToolService(AppService)            ├── FastLLMService(AppService)├── L0MetaRulesService(AppService)
│   └── protocol: LocalProtocol            │   └── protocol: Protocol链  │   └── protocol: KVProtocol
├── DockerToolService(AppService)           ├── CapableLLMService         ├── L1InsightIndexService(AppService)
│   └── protocol: DockerProtocol           │   └── protocol: Protocol链  │   └── protocol: KVProtocol
└── SSHToolService(AppService)              └── CodeLLMService            ├── L2GlobalFactsService(AppService)
    └── protocol: SSHProtocol                   └── protocol: Protocol链  │   └── protocol: KVProtocol
                                                                          ├── L3TaskSkillsService(AppService)
                                                                          │   └── protocol: FileProtocol
                                                                          └── L4SessionArchiveService(AppService)
                                                                              └── protocol: CacheLayer → FileProtocol
```

### 为什么每层是独立 AppService

| 条件 | LLM 子服务 | 工具子服务 | 记忆子服务 |
|------|-----------|-----------|-----------|
| 不同的 Protocol | ModelFallbackProvider | TerminalProtocol | KVProtocol / FileProtocol / CacheLayer |
| 不同的存储/执行环境 | OpenAI/Anthropic API | 本机/Docker/SSH | 内存KV/文件/缓存+文件 |
| 不同的访问模式 | chat/stream_chat | execute/read_file | get/set/read/write |
| 可独立测试 | 是 | 是 | 是 |
| 可独立配置 | 是 | 是 | 是 |

每层有独立的 Protocol、独立的配置、独立的生命周期。框架的 depends 机制自动处理启动顺序和依赖注入。

### 依赖关系图

```
L0MetaRulesService       → 无跨层依赖
L1InsightIndexService    → 无跨层依赖
L2GlobalFactsService     → depends: LLMService (LLM 辅助检索, 可选)
L3TaskSkillsService      → depends: SkillService (弱包装，读取技能数据)
L4SessionArchiveService  → 无跨层依赖

SkillService (独立)      → 无外部依赖，持有 CompositeProtocol 做持久化
```

### get_context 上下文组装流程

```
MemoryService.get_context(query, budget):
    1. L0.get_all()         → 系统规则前缀（始终加载）
    2. L1.get_index()       → 技能索引（始终加载元数据）
    3. L2.get_relevant()    → 相关事实（按启用的检索方法过滤）
    4. L3.get_matching()    → 匹配技能（关键词触发时加载正文）
    5. L4.get_recent()      → 最近消息（填满剩余 token 预算）
    → 组装为 messages[] 返回
```

## 记忆层级

```
┌─────────────────────────────────────────────────────────────────┐
│                     MemoryService (L0-L4)                        │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ L0: MetaRulesService (元规则)                             │   │
│  │ · 加载: 每次启动, 全量                                    │   │
│  │ · 来源: CLAUDE.md / AGENTS.md / 系统 prompt               │   │
│  │ · Token: ~500                                            │   │
│  │ · Protocol: KVProtocol                                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ L1: InsightIndexService (洞察索引)                        │   │
│  │ · 加载: 每次启动, 元数据                                   │   │
│  │ · 内容: 技能名 + 描述 (路由表)                             │   │
│  │ · Token: ~100/技能                                       │   │
│  │ · Protocol: KVProtocol                                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ L2: GlobalFactsService (全局事实)                         │   │
│  │ · 加载: 按启用的检索方法过滤                               │   │
│  │ · 内容: 用户偏好 / 项目知识 / 架构决策                      │   │
│  │ · Token: ~2K                                             │   │
│  │ · Protocol: KVProtocol                                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ L3: TaskSkillsService (弱包装 SkillService)               │   │
│  │ · 加载: 关键词匹配时触发                                   │   │
│  │ · 内容: 结晶的执行路径 (工作流)                             │   │
│  │ · Token: ~2K/技能                                        │   │
│  │ · 无 Protocol: 委托 SkillService                          │   │
│  │ · depends: SkillService                                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ L4: SessionArchiveService (会话档案)                      │   │
│  │ · 加载: 最近消息 (受 token 预算约束)                       │   │
│  │ · 内容: 当前对话历史                                       │   │
│  │ · Token: 可变                                             │   │
│  │ · Protocol: CacheLayer → FileProtocol                    │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Class Signatures

### MemoryService (AppService 门面)

```python
class MemoryService(AppService):
    """记忆门面：管理 L0-L4 各子服务，组装上下文。"""
    domain = 'a2'
    alias = 'MemoryService'
    depends = [
        'a2.memory.l0.L0MetaRulesService',
        'a2.memory.l1.L1InsightIndexService',
        'a2.memory.l2.L2GlobalFactsService',
        'a2.memory.l3.L3TaskSkillsService',
        'a2.memory.l4.L4SessionArchiveService',
    ]

    async def get_context(self, query: str, token_budget: int) -> dict:
        """从所有层组装上下文，受 token 预算约束。"""
        context = {}
        budget_remaining = token_budget

        # L0: 始终加载（最小，最高优先级）
        context['meta_rules'] = await self._l0.get_all()
        budget_remaining -= count_tokens(context['meta_rules'])

        # L1: 技能索引（始终加载元数据）
        context['skill_index'] = await self._l1.get_index()
        budget_remaining -= count_tokens(context['skill_index'])

        # L2: 相关事实（按启用的检索方法过滤）
        context['facts'] = await self._l2.get_relevant(query, budget=budget_remaining // 2)
        budget_remaining -= count_tokens(context['facts'])

        # L3: 匹配技能（触发时加载）
        context['skills'] = await self._l3.get_matching(query, budget=budget_remaining // 2)
        budget_remaining -= count_tokens(context['skills'])

        # L4: 会话历史（填满剩余预算）
        context['session'] = await self._l4.get_recent(budget=budget_remaining)

        return context

    async def add_fact(self, category: str, content: str):
        await self._l2.add_fact(category, content)

    async def add_skill(self, skill):
        # L3 内部会通知 L1 更新索引（服务间调用）
        await self._l3.crystallize(skill)

    async def append_message(self, message: dict):
        await self._l4.append(message)
```

### L0: MetaRulesService (AppService)

```python
class L0MetaRulesService(AppService):
    """系统级规则服务。启动时扫描 CLAUDE.md 等规则文件。"""
    domain = 'a2.memory'
    alias = 'L0MetaRulesService'
    depends = []

    async def get_all(self) -> str:
        """返回所有元规则的格式化字符串。"""
        rules = await self._scan_directory()
        return '\n\n'.join(rules)

    async def _scan_directory(self) -> list[str]:
        """扫描项目目录中的规则文件（CLAUDE.md, AGENTS.md 等）。"""
        ...
```

**TOML 配置：**
```toml
["a2.memory.l0.L0MetaRulesService"]
module = "a2.memory.l0.service.L0MetaRulesService"

["a2.memory.l0.L0MetaRulesService".protocol]
module = "bollydog.adapters.kv.MemoryProtocol"
```

### L1: InsightIndexService (AppService)

```python
class L1InsightIndexService(AppService):
    """技能索引服务。维护技能名 + 描述的路由表。"""
    domain = 'a2.memory'
    alias = 'L1InsightIndexService'
    depends = []

    async def get_index(self) -> str:
        """返回格式化的技能索引，用于 system prompt。"""
        skills = await self._list_skills()
        lines = ['# Available Skills']
        for skill in skills:
            lines.append(f"- {skill.name}: {skill.description}")
        return '\n'.join(lines)

    async def add_skill(self, skill: 'Skill'):
        """添加技能元数据到索引。"""
        await self.protocol.set(skill.name, skill.metadata)

    async def remove_skill(self, name: str):
        """从索引中移除技能。"""
        await self.protocol.delete(name)
```

**TOML 配置：**
```toml
["a2.memory.l1.L1InsightIndexService"]
module = "a2.memory.l1.service.L1InsightIndexService"

["a2.memory.l1.L1InsightIndexService".protocol]
module = "bollydog.adapters.kv.MemoryProtocol"
```

### L2: GlobalFactsService (AppService)

```python
class L2GlobalFactsService(AppService):
    """全局事实服务。存储用户偏好、项目知识、架构决策。
    支持多种检索方法，通过开关独立控制。"""
    domain = 'a2.memory'
    alias = 'L2GlobalFactsService'
    depends = []  # llm_retrieval 启用时需添加 LLMService

    async def get_relevant(self, query: str, budget: int) -> str:
        """按启用的检索方法依次过滤，返回相关事实。"""
        all_facts = await self._list_all()
        if not all_facts:
            return ''

        candidates = all_facts

        # 1. BM25 关键词初筛
        if self.config.get('keyword_retrieval', True):
            candidates = self._bm25_filter(query, candidates)

        # 2. Embedding 向量检索
        if self.config.get('embedding_retrieval', False):
            candidates = await self._embedding_filter(query, candidates)

        # 3. LLM 精排
        if self.config.get('llm_retrieval', False):
            candidates = await self._llm_select(query, candidates, max_tokens=budget)

        return '\n\n'.join(candidates)

    async def add_fact(self, category: str, content: str):
        """添加事实（user/feedback/project/reference）。"""
        key = f'{category}:{uuid.uuid4().hex[:8]}'
        await self.protocol.set(key, {'category': category, 'content': content})

    def _bm25_filter(self, query: str, facts: list) -> list:
        """BM25 关键词匹配，零 LLM 成本，毫秒级。"""
        ...

    async def _embedding_filter(self, query: str, facts: list) -> list:
        """Embedding 向量检索，语义匹配。"""
        ...

    async def _llm_select(self, query: str, facts: list, max_tokens: int) -> list:
        """LLM 精排，质量最高但有成本和延迟。"""
        ...
```

**TOML 配置：**
```toml
["a2.memory.l2.L2GlobalFactsService"]
module = "a2.memory.l2.service.L2GlobalFactsService"
keyword_retrieval = true      # BM25 关键词匹配
llm_retrieval = false         # LLM 语义检索
embedding_retrieval = false   # Embedding 向量检索

["a2.memory.l2.L2GlobalFactsService".protocol]
module = "bollydog.adapters.kv.MemoryProtocol"
```

**检索方法独立开关：**

| 场景 | keyword | llm | embedding | 效果 |
|------|---------|-----|-----------|------|
| 开发/测试 | true | false | false | 零成本，毫秒级 |
| 生产（低成本） | true | false | false | BM25 足够大部分场景 |
| 生产（高质量） | true | true | false | BM25 初筛 + LLM 精排 |
| 生产（向量） | false | false | true | Embedding 语义检索 |
| 全量 | true | true | true | 三级过滤，最高质量 |

### L3: TaskSkillsService (弱包装)

```python
class L3TaskSkillsService(AppService):
    """Memory 系统视角的技能适配器。
    弱包装 SkillService，提供 Memory 系统统一接口。
    自己不做持久化——数据在 SkillService 的 CompositeProtocol 中。"""
    domain = 'a2.memory'
    alias = 'L3TaskSkillsService'
    depends = ['a2.SkillService']  # ← 读取 SkillService 的数据，无自己的 Protocol

    async def get_matching(self, query: str, budget: int) -> str:
        """返回与当前 query 匹配的技能正文。供 MemoryService.get_context 使用。"""
        matched = self._skill_service.get_matching(query)
        result = []
        for skill in matched[:3]:
            result.append(f'<skill name="{skill.name}">\n{skill.body}\n</skill>')
        return '\n\n'.join(result)

    async def get_index(self) -> str:
        """返回技能索引。"""
        return self._skill_service.get_metadata_prompt()
```

**TOML 配置：**
```toml
["a2.memory.l3.L3TaskSkillsService"]
module = "a2.memory.l3.service.L3TaskSkillsService"
# 无 protocol 配置——数据在 SkillService 的 CompositeProtocol 中
```

### L4: SessionArchiveService (AppService)

```python
class L4SessionArchiveService(AppService):
    """会话档案服务。
    使用 CompositeProtocol：CacheLayer（内存缓存）+ FileProtocol（文件持久化）。"""
    domain = 'a2.memory'
    alias = 'L4SessionArchiveService'
    depends = []

    async def get_recent(self, budget: int) -> list[dict]:
        """返回 token 预算内的最近消息。"""
        messages = await self._load_messages()
        return self._fit_budget(messages, budget)

    async def append(self, message: dict):
        await self._append_message(message)
```

**TOML 配置：**
```toml
["a2.memory.l4.L4SessionArchiveService"]
module = "a2.memory.l4.service.L4SessionArchiveService"

["a2.memory.l4.L4SessionArchiveService".protocol]
module = "bollydog.adapters.composite.CacheLayer"
flush_threshold = 100

["a2.memory.l4.L4SessionArchiveService".protocol.protocol]
module = "bollydog.adapters.file.LocalFileProtocol"
path = ".agent/session/"
```

## 自演化流程（来自 GenericAgent）

```
新任务
    ↓
Agent Loop 执行任务
    ↓
任务成功？
    ├── 是 → L3TaskSkillsService.crystallize()
    │         提取: 触发条件、工具序列、成功标准
    │         写入 L3 FileProtocol
    │         → 自动通知 L1InsightIndexService.add_skill()（服务间调用）
    │
    └── 否 → L2GlobalFactsService.add_fact('failure', ...)
              记录失败模式，避免重复相同方法
```

## Files to Create

| File | Purpose |
|------|---------|
| `memory/__init__.py` | Exports |
| `memory/service.py` | MemoryService(AppService) 门面：depends 5 个子服务、get_context 组装 |
| `memory/l0/__init__.py` | Exports |
| `memory/l0/service.py` | L0MetaRulesService(AppService)：元规则、文件扫描 |
| `memory/l1/__init__.py` | Exports |
| `memory/l1/service.py` | L1InsightIndexService(AppService)：技能索引、路由表 |
| `memory/l2/__init__.py` | Exports |
| `memory/l2/service.py` | L2GlobalFactsService(AppService)：全局事实、多模式检索 |
| `memory/l3/__init__.py` | Exports |
| `memory/l3/service.py` | L3TaskSkillsService(AppService)：任务技能、结晶、通知 L1 |
| `memory/l4/__init__.py` | Exports |
| `memory/l4/service.py` | L4SessionArchiveService(AppService)：会话档案、缓存+持久化 |

## Dependencies

- `bollydog.models.service.AppService` — 所有服务基类
- `bollydog.models.protocol.KVProtocol` — L0, L1, L2 的 Protocol
- `bollydog.adapters._base.FileProtocol` — L3 的 Protocol
- `bollydog.adapters.composite.CacheLayer` — L4 的 Protocol（内存缓存 + 文件持久化）
