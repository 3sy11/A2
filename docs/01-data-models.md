# 数据模型架构

> 本文档集中定义 A2 框架中所有核心数据模型（Pydantic BaseModel）及其存储位置。

---

## 一、RoleDef — 角色定义

```python
class RoleDef(BaseModel):
    name: str
    system_prompt: str                     # 完整的角色系统提示词（支持 {variable} 模板）
    tools: list[str] = ['*']              # 工具白名单，'*' 表示全部
    model: str | None = None              # LLM 模型，None = 使用 LiteLLMProvider.default_model
    max_turns: int = 50
    can_spawn: bool = False               # 是否允许 spawn 子 agent
    use_planning: bool = False
    use_reflexion: bool = False
    env: str = 'env.local'                # 绑定的执行环境（运行时有且一个实例）
    skill_refs: list[str] = ['*']         # 可见技能子集
    memory_namespace: str | None = None   # 默认 = name
    service_specs: list[ServiceSpec] | None = None  # None = ROLE_DEFAULT

    @property
    def effective_namespace(self) -> str: return self.memory_namespace or self.name
    @property
    def base_dir(self) -> str: return f'.agent/roles/{self.effective_namespace}'
```

> `system_prompt` 是角色的完整系统提示词。不存在全局 `DEFAULT_SYSTEM_PROMPT` — 系统提示词完全由角色定义管理。权限配置不在 RoleDef 中，而是由 EnvService 的 PermissionProtocol 统一管理。

**存储**：`AgentService.protocol` → `.agent/roles.db` / `roles` 表 (KV: name → JSON)

---

## 二、ServiceSpec — 服务实例声明

```python
class ServiceSpec(BaseModel):
    key: str          # l0, l1, l4, facts, tasks
    module: str       # 服务类全限定路径
    db: str           # DB 文件名（放在 role base_dir 下）
    table: str        # 表名
    load_on_start: bool = False
    flush_threshold: int = 100
```

**用途**：ContextService 和 PlannerService 根据 ServiceSpec 动态创建 owned memory AppService 实例。

**存储**：`config.py` `ROLE_DEFAULT`（默认）/ `RoleDef.service_specs`（角色自定义）

---

## 三、Task / TaskList — 任务模型

```python
class Task(BaseModel):
    id: str
    run_id: str = ''
    role: str = ''
    content: str = ""
    status: Literal["pending", "in_progress", "done", "failed"] = "pending"
    result: str | None = None
    context: str = ""
    parent_run_id: str | None = None   # orchestrator 关联子任务

    def with_reflection(self, reflection: str) -> "Task":
        return self.model_copy(update={'status': 'pending',
            'context': f"{self.context}\n[Reflection]: {reflection}".strip()})

class TaskList(BaseModel):
    run_id: str
    goal: str = ""
    tasks: list[Task] = []

    def pending(self) -> list[Task]: return [t for t in self.tasks if t.status == "pending"]
    def render_progress(self) -> str: ...
```

**存储**：
- `persist_tasks=True`（默认）→ `{role.base_dir}/tasks.db` / `tasks` 表 (KV: run_id → JSON)
- `persist_tasks=False` → session 缓存，run 结束释放

---

## 四、Skill — 技能模型

```python
class Skill(BaseModel):
    name: str
    description: str
    tags: list[str] = []
    trigger_keywords: list[str] = []
    always: bool = False               # 始终加载到 system prompt
    body: str = ''                     # Markdown 正文
    resources: dict[str, str] = {}     # 附属资源
    usage_count: int = 0
    success_count: int = 0
```

**存储**：`SkillService.protocol` → CacheLayer → FileProtocol → `.user/skills/{name}.md`

**序列化格式**：Markdown + YAML frontmatter

```yaml
---
name: code-review
description: Perform structured code review
tags: [code, review]
trigger_keywords: [review, 审查, code review]
---
# Code Review Skill
...body...
```

---

## 五、PermissionRule / PermissionConfig — 权限数据模型

权限由 EnvService 的复合协议内层 `PermissionProtocol` 管理。

```python
class PermissionRule(BaseModel):
    pattern: str                  # 工具名通配符，如 'bash', 'read_*', '*'
    action: Literal['allow', 'block', 'approve'] = 'allow'

class PermissionConfig(BaseModel):
    rules: list[PermissionRule] = [
        PermissionRule(pattern='read_file', action='allow'),
        PermissionRule(pattern='list_dir', action='allow'),
        PermissionRule(pattern='grep_search', action='allow'),
        PermissionRule(pattern='*', action='allow'),           # 默认允许
    ]

    def check(self, tool_name: str) -> str:
        for rule in self.rules:
            if fnmatch(tool_name, rule.pattern): return rule.action
        return 'block'
```

**存储**：`PermissionProtocol` 在 EnvService 的复合协议内层管理，配置从 `config.py` 或 `agent.toml` 注入。TerminalProtocol 执行命令前通过内层 `self.protocol.check(tool_name)` 校验权限。

---

## 六、ToolCommand 元数据（ClassVar）

```python
class ToolCommand(BaseCommand, abstract=True):
    tool_name: ClassVar[str]           # LLM 看到的工具名
    description: ClassVar[str]         # 工具描述
    parameters: ClassVar[dict]         # JSON Schema 参数定义
    is_destructive: ClassVar[bool] = False
    required_model: ClassVar[list[str]] = []   # 空 = 任意模型
```

**存储**：代码中 ClassVar，非持久化。运行时通过 `to_schema()` 生成 LLM tools 描述。

---

## 七、MemoryLayer 数据（KV 模型）

所有 MemoryLayerService 统一使用 bollydog `SQLiteProtocol` 的 KV 表结构：

```sql
CREATE TABLE IF NOT EXISTS {table_name} (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
```

| 层级 | DB 文件 | 表名 | key 含义 | value 含义 |
|------|--------|------|---------|-----------|
| L0 Rules | `memory.db` | `rules` | rule_id | 规则文本 |
| L1 Insights | `memory.db` | `insights` | insight_id | insight JSON |
| L4 Sessions | `memory.db` | `sessions` | session_id | 会话摘要 JSON |
| L2 Facts | `facts.db` | `facts` | fact_id | fact 文本（BM25 检索） |
| Tasks | `tasks.db` | `tasks` | run_id | TaskList JSON |
| Roles | `roles.db` | `roles` | role_name | RoleDef JSON |

---

## 八、存储物理布局

```
.agent/
├── roles.db                     ← AgentService.protocol
├── roles/
│   ├── default/
│   │   ├── memory.db            ← rules + insights + sessions
│   │   ├── facts.db             ← facts + BM25
│   │   ├── tasks.db             ← PlannerService 持久化
│   │   └── outputs/             ← Artifact 产物
│   ├── explorer/
│   │   └── ...
│   └── devops/
│       └── ...
└── skills/                      ← SkillService 全局
    ├── code-review.md
    └── ...
```

**隔离策略**：角色级物理隔离（独立 DB 目录），无需 namespace 前缀。跨角色分析 → v2 DuckDB ATTACH。
