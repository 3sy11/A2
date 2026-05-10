# 04 — 技能系统

## Context

技能是 Agent 的"专业知识库"——可复用的工作流，随时间演化。系统结合了 hermes-agent 的自动结晶和 Claude Code 的三级渐进式披露。技能解决 token 预算问题：不是启动时加载所有知识（~20K tokens），而是只加载元数据（~100 tok/技能），正文按需加载。

**核心设计决策：**
- **SkillService(AppService) 是技能的真正所有者**，持有 CompositeProtocol（CacheLayer + FileProtocol）做持久化
- **L3TaskSkillsService 是 Memory 系统视角的弱包装**，通过 depends 读取 SkillService 的数据，自己不做持久化
- **Skill 数据模型使用 Pydantic**，便于序列化/反序列化和类型校验

## 架构

```
SkillService(AppService) 门面 — 技能的真正所有者
├── depends: []                                  ← 无外部服务依赖
├── protocol: CacheLayer → FileProtocol          ← 复合协议：缓存 + 文件持久化
│
├── _skills: dict[str, Skill]                    ← 内存中的技能注册表
│
├── register(skill)                              ← 注册技能
├── load_skill(name) → str                       ← Level 2: 按需加载正文
├── load_resource(skill, resource) → str         ← Level 3: 按需加载资源
├── get_metadata_prompt() → str                  ← Level 1: 生成技能索引
├── get_always_skills_prompt() → str             ← 始终加载的技能正文
├── crystallize(task, trace) → Skill             ← 结晶：任务→可复用技能
└── get_matching(query) → list[Skill]            ← 关键词匹配

L3TaskSkillsService(AppService) — Memory 系统视角的弱包装
├── depends: ["a2.SkillService"]                 ← 读取 SkillService 的数据
├── 无自己的 Protocol                             ← 不做持久化
│
├── get_matching(query, budget) → str            ← 委托 SkillService
└── get_index() → str                            ← 委托 SkillService

依赖关系：
    MemoryService → L3TaskSkillsService → SkillService
    SkillService 独立存在，不依赖任何其他服务
    L3 是 SkillService 的弱包装，提供 Memory 系统统一接口
```

### SkillService 的 CompositeProtocol

```
SkillService.protocol
└── CacheLayer (内存缓存)
    └── FileProtocol (文件持久化)

读取流程：
    1. 查缓存 → 命中 → 返回
    2. 未命中 → 读文件 → 写入缓存 → 返回

写入流程：
    1. 写缓存
    2. 累积 N 次写入后 flush 到文件（CacheLayer 的 flush_threshold）

技能文件存储路径：
    .user/skills/           ← 项目目录下，非用户目录
    ├── pdf/SKILL.md
    ├── git/SKILL.md
    └── code-review/SKILL.md
```

### L3 为什么是弱包装

| 维度 | SkillService | L3TaskSkillsService |
|------|-------------|-------------------|
| 本质 | 业务服务 | 记忆适配器 |
| 持有数据 | 是（CompositeProtocol） | 否（委托 SkillService） |
| 操作 | 注册/加载/结晶/淘汰 | get_matching（供 MemoryService 使用） |
| 生命周期 | 独立 | 依赖 SkillService |
| 被谁调用 | Agent Loop、用户 | MemoryService |

MemoryService 的 `get_context` 需要从 L0-L4 统一接口获取数据。L3 的存在是为了让 MemoryService 不需要知道 SkillService 的内部结构——L3 把 SkillService 的数据适配为 Memory 系统的统一格式。

```
MemoryService.get_context():
    L0.get_all()        ← L0MetaRulesService
    L1.get_index()      ← L1InsightIndexService
    L2.get_relevant()   ← L2GlobalFactsService
    L3.get_matching()   ← L3TaskSkillsService → SkillService
    L4.get_recent()     ← L4SessionArchiveService
```

## 三级渐进式披露

```
┌─────────────────────────────────────────────────────────────────┐
│                     SkillService 三级披露                         │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Level 1: 元数据 (始终在 system prompt)                     │   │
│  │ · 内容: 技能名 + 描述 + 标签                               │   │
│  │ · Token: ~100/技能                                       │   │
│  │ · 来源: L1InsightIndexService                            │   │
│  │ · 格式: "- skill_name: description (tags: ...)"          │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Level 2: 正文 (触发时加载)                                 │   │
│  │ · 内容: 完整执行指令 (Markdown)                            │   │
│  │ · Token: ~2K/技能                                        │   │
│  │ · 来源: SkillService.protocol (CacheLayer → FileProtocol)│   │
│  │ · 触发: 关键词匹配 或 LLM 调用 load_skill                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Level 3: 资源/脚本 (按需引用)                              │   │
│  │ · 内容: 参考文档、工具脚本                                  │   │
│  │ · Token: 脚本只返回输出，不返回代码                         │   │
│  │ · 来源: 技能目录下的附属文件                                │   │
│  │ · 触发: 技能正文中的 @引用 或 LLM 判断需要                  │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Class Signatures

### Skill (Pydantic 数据模型)

```python
from pydantic import BaseModel, Field


class Skill(BaseModel):
    """结晶的执行路径，支持三级渐进式披露。"""
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    trigger_keywords: list[str] = Field(default_factory=list)
    always: bool = False              # True 时正文始终在 system prompt

    # Level 2: 完整指令
    body: str = ''

    # Level 3: 附属资源
    resources: dict[str, str] = Field(default_factory=dict)
    scripts: dict[str, str] = Field(default_factory=dict)

    # 演化追踪
    usage_count: int = 0
    success_count: int = 0
    created_from: str | None = None   # 结晶来源的 task_id

    @property
    def success_rate(self) -> float:
        return self.success_count / max(self.usage_count, 1)

    def to_metadata(self) -> str:
        """Level 1: 紧凑元数据，用于 system prompt。"""
        return f"- {self.name}: {self.description} (tags: {', '.join(self.tags)})"

    def to_markdown(self) -> str:
        """序列化为 .md 文件（YAML frontmatter + Markdown body）。"""
        frontmatter = yaml.dump({
            'name': self.name,
            'description': self.description,
            'tags': self.tags,
            'trigger_keywords': self.trigger_keywords,
            'always': self.always,
        })
        return f"---\n{frontmatter}---\n\n{self.body}"

    @classmethod
    def from_markdown(cls, content: str) -> 'Skill':
        """从 .md 文件反序列化。"""
        parts = content.split('---', 2)
        if len(parts) < 3:
            raise ValueError("Invalid skill file format")
        metadata = yaml.safe_load(parts[1])
        body = parts[2].strip()
        return cls(**metadata, body=body)
```

### SkillService (AppService 门面)

```python
class SkillService(AppService):
    """技能门面：管理技能生命周期、三级披露、结晶。
    持有 CompositeProtocol（CacheLayer + FileProtocol）做持久化。"""
    domain = 'a2'
    alias = 'SkillService'
    depends = []  # 无外部服务依赖

    _skills: dict[str, Skill] = {}

    async def on_start(self):
        # 从 Protocol（文件）加载已有技能
        await self._load_from_protocol()

    async def _load_from_protocol(self):
        """从 CompositeProtocol 加载所有技能文件。"""
        skill_files = await self._scan_skills_dir()
        for path in skill_files:
            content = await self.protocol.read(path)
            skill = Skill.from_markdown(content)
            self._skills[skill.name] = skill

    def register(self, skill: Skill):
        """注册技能到内存注册表。"""
        self._skills[skill.name] = skill

    def get_metadata_prompt(self) -> str:
        """Level 1: 生成技能索引，用于 system prompt。"""
        lines = ['# Available Skills\n']
        for skill in self._skills.values():
            if skill.always:
                continue
            lines.append(skill.to_metadata())
        return '\n'.join(lines)

    def get_always_skills_prompt(self) -> str:
        """始终加载的技能正文。"""
        parts = []
        for skill in self._skills.values():
            if skill.always:
                parts.append(f'<skill name="{skill.name}">\n{skill.body}\n</skill>')
        return '\n\n'.join(parts)

    async def load_skill(self, name: str) -> str:
        """Level 2: 按需加载技能正文。"""
        skill = self._skills.get(name)
        if not skill:
            return f"Error: Unknown skill '{name}'"
        skill.usage_count += 1
        return f'<skill name="{name}">\n{skill.body}\n</skill>'

    def load_resource(self, skill_name: str, resource_name: str) -> str:
        """Level 3: 按需加载附属资源。"""
        skill = self._skills.get(skill_name)
        if not skill:
            return f"Error: Unknown skill '{skill_name}'"
        if resource_name in skill.resources:
            return skill.resources[resource_name]
        if resource_name in skill.scripts:
            return f"[Script content — execute via bash to get output]"
        return f"Error: Resource '{resource_name}' not found in skill '{skill_name}'"

    def get_matching(self, query: str) -> list[Skill]:
        """按关键词匹配技能。"""
        matched = []
        query_lower = query.lower()
        for skill in self._skills.values():
            if any(kw in query_lower for kw in skill.trigger_keywords):
                matched.append(skill)
            elif any(tag in query_lower for tag in skill.tags):
                matched.append(skill)
        return matched

    async def crystallize(self, task: str, execution_trace: list[dict]) -> Skill | None:
        """将成功的任务执行转化为可复用技能。
        通过 CompositeProtocol 持久化。"""
        tool_calls = [t for t in execution_trace if t.get('type') == 'tool_call']
        if len(tool_calls) < 3:
            return None
        if len(self._skills) >= 20:
            return None

        skill = await self._extract_skill_pattern(task, execution_trace)
        if not skill:
            return None

        self.register(skill)
        # 持久化到 CompositeProtocol（CacheLayer → FileProtocol）
        await self.protocol.write(
            f'.user/skills/{skill.name}/SKILL.md',
            skill.to_markdown(),
        )
        return skill

    async def _extract_skill_pattern(self, task: str, trace: list[dict]) -> Skill | None:
        """使用 LLM 从执行轨迹中提取可复用模式。"""
        prompt = f"""分析这个成功的任务执行，提取可复用的技能模式。

任务: {task}

执行轨迹:
{json.dumps(trace, indent=2)}

提取:
1. 技能名称（简短、描述性）
2. 触发条件（何时使用此技能）
3. 分步指令（工作流）
4. 所需工具
5. 成功标准

输出 JSON 格式。
"""
        # 通过 LLMService 路由
        # 注意：这里需要通过 Command 化的方式调用 LLMService
        # 待 Command 分离后实现
        response = await self._llm.chat(
            messages=[{'role': 'user', 'content': prompt}],
            capability='fast',
        )
        return Skill.from_llm_response(response)

    async def _scan_skills_dir(self) -> list[str]:
        """扫描 .user/skills/ 目录中的 SKILL.md 文件。"""
        ...
```

### L3TaskSkillsService (弱包装)

```python
class L3TaskSkillsService(AppService):
    """Memory 系统视角的技能适配器。
    弱包装 SkillService，提供 Memory 系统统一接口。
    自己不做持久化——数据在 SkillService 的 CompositeProtocol 中。"""
    domain = 'a2.memory'
    alias = 'L3TaskSkillsService'
    depends = ['a2.SkillService']  # ← 读取 SkillService 的数据

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

## 技能文件格式

技能使用 YAML frontmatter + Markdown body：

```markdown
---
name: pdf-processing
description: Process PDF files and extract data
tags: [documents, analysis, extraction]
trigger_keywords: [pdf, document, extract]
always: false
---

## PDF Processing Steps

1. Use PyMuPDF to open the file
2. Extract text per page
3. Parse tables if present
4. Return structured data

## Resources
- See @FORMS.md for form filling guide
- See @REFERENCE.md for API reference

## Scripts
- scripts/fill_form.py — Auto-fill PDF forms
```

## 技能目录结构

```
.user/skills/                              ← 项目目录下
├── pdf/
│   ├── SKILL.md          # Level 2: 主指令
│   ├── FORMS.md          # Level 3: 表单填写指南
│   ├── REFERENCE.md      # Level 3: API 参考
│   └── scripts/
│       └── fill_form.py  # Level 3: 工具脚本
├── git/
│   └── SKILL.md
└── code-review/
    ├── SKILL.md
    └── CHECKLIST.md
```

## 结晶流程

```
任务成功完成
    ↓
复杂度检查：工具调用次数 ≥ 3？
    ├── 否 → 跳过结晶
    └── 是 → 提取模式:
              1. 收集执行轨迹（工具调用 + 结果）
              2. LLMService.chat(capability='fast') 提取模式
              3. 生成 Skill 对象 (Pydantic)
              4. SkillService.register() 注册到内存
              5. SkillService.protocol.write() 持久化到 .user/skills/
              ↓
         下次类似任务出现时:
              1. L1 索引在 system prompt 中匹配
              2. LLM 调用 load_skill
              3. 完整正文注入上下文
              4. Agent 按结晶步骤执行
```

## 技能激活控制矩阵

| 触发方式 | Level 1 | Level 2 | Level 3 |
|---------|---------|---------|---------|
| always 技能 | N/A | 正文在 system prompt | N/A |
| 启动时 | 元数据在 system prompt | — | — |
| 用户 query 关键词匹配 | — | LLM 调用 load_skill | — |
| 技能正文中 @引用 | — | — | LLM 读取资源文件 |
| 需要执行脚本 | — | — | LLM 通过 bash 执行 |

## 演化控制

| 参数 | 值 | 位置 |
|------|---|------|
| 结晶门槛 | ≥3 次工具调用 | SkillService.crystallize() |
| 技能数量上限 | 20 个 | SkillService.crystallize() |
| 成功率淘汰 | success_rate < 0.3 降级 | 定期检查 |
| 成功率删除 | success_rate < 0.1 且 usage > 10 | 定期检查 |

## TOML 配置

```toml
# SkillService：技能的真正所有者，持有 CompositeProtocol
["a2.SkillService"]
module = "a2.skills.service.SkillService"
skills_dir = ".user/skills/"
crystallize_min_tools = 3
max_skills = 20

["a2.SkillService".protocol]
module = "bollydog.adapters.composite.CacheLayer"
flush_threshold = 10

["a2.SkillService".protocol.protocol]
module = "bollydog.adapters.file.LocalFileProtocol"
path = ".user/skills/"

# L3TaskSkillsService：Memory 系统视角的弱包装
["a2.memory.l3.L3TaskSkillsService"]
module = "a2.memory.l3.service.L3TaskSkillsService"
```

## Files to Create

| File | Purpose |
|------|---------|
| `skills/__init__.py` | Exports |
| `skills/service.py` | SkillService(AppService) 门面：技能管理、三级披露、结晶、CompositeProtocol |
| `skills/skill.py` | Skill(BaseModel) Pydantic 数据模型 |

## Dependencies

- `pydantic` — Skill 数据模型
- `bollydog.adapters.composite.CacheLayer` — SkillService 的 Protocol（缓存层）
- `bollydog.adapters.file.LocalFileProtocol` — SkillService 的 Protocol（文件持久化）
- `a2.memory.l3.L3TaskSkillsService` → `a2.SkillService` — L3 弱包装 SkillService
