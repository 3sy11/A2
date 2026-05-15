# 模块：ContextService（per-role 动态子类）

> domain=`context` | per-role 动态子类 | depends: [SkillService, LLMService] + owns memory

---

## 架构

```
ContextService(AppService), domain="context"
depends: ["skills.SkillService", "llm.LLMService"]  ← 全局单例引用

owns（per-role，由 on_start 创建管理）:
├── _l0: MemoryLayerService → CacheLayer → SQLiteProtocol(rules)
├── _l1: MemoryLayerService → CacheLayer → SQLiteProtocol(insights)
├── _l4: MemoryLayerService → CacheLayer → SQLiteProtocol(sessions)
└── _facts: GlobalFactsService → CacheLayer → SQLiteProtocol(facts) + BM25

methods:
├── build_messages(role_def, query, history) → list[dict]
├── compact_if_needed(messages, model) → list[dict]
└── count_tokens(messages, model) → int
```

**关键设计**：ContextService 是唯一的 per-role 动态子类。AgentService.activate_role 用 `type()` 创建子类，配置 `role_specs` 和 `base_dir`。on_start 中根据 ServiceSpec 创建 owned memory 实例并管理其生命周期。

## on_start — 创建 owned memory 实例

```python
class ContextService(AppService):
    depends = ['skills.SkillService', 'llm.LLMService']
    _l0 = None; _l1 = None; _l4 = None; _facts = None
    _owned_services: list = []

    async def on_start(self):
        specs = self.config.get('role_specs', ROLE_DEFAULT)
        base_dir = self.config.get('base_dir', '.agent/roles/default')
        for spec in specs:
            svc = _create_from_spec(base_dir, spec)
            await svc.maybe_start()
            self._owned_services.append(svc)
            setattr(self, f'_{spec.key}', svc)

    async def on_stop(self):
        for svc in self._owned_services: await svc.stop()

    async def on_started(self):
        for dep in self.depends:
            match dep.alias:
                case 'SkillService': self._skill = dep
                case 'LLMService': self._llm = dep
```

## _create_from_spec — 与 AgentService.activate_role 共用

```python
from mode.utils.imports import smart_import
from bollydog.models.service import _build_protocol

def _create_from_spec(base_dir: str, spec: ServiceSpec) -> AppService:
    base_cls = smart_import(spec.module)
    alias = f'{spec.key}'
    cls = type(alias, (base_cls,), {'alias': alias})
    svc = cls()
    svc.config = {'load_on_start': spec.load_on_start}
    proto = _build_protocol({
        'module': 'bollydog.adapters.composite.CacheLayer',
        'flush_threshold': spec.flush_threshold,
        'protocol': {
            'module': 'bollydog.adapters.memory.SQLiteProtocol',
            'path': f'{base_dir}/{spec.db}', 'table': spec.table,
        }
    })
    svc.add_dependency(proto)
    return svc
```

## System Prompt 管理 — 角色定义驱动

系统提示词完全由 `RoleDef.system_prompt` 管理，不存在全局 `DEFAULT_SYSTEM_PROMPT`。每个角色自行定义完整的系统提示词。

## Prompt 模板变量

System prompt 支持 `{variable}` 占位符，build_messages 时渲染：

| 变量 | 来源 | 示例值 |
|------|------|--------|
| `{date}` | `datetime.now()` | `2026-05-15 Thu` |
| `{time}` | `datetime.now()` | `09:30` |
| `{platform}` | `platform.system()` | `Darwin` |
| `{cwd}` | `os.getcwd()` | `/Users/dev/project` |
| `{role_name}` | `role_def.name` | `default` |
| `{model}` | `role_def.model` | `anthropic/claude-sonnet-4-20250514` |

```python
def _build_vars(self, role_def: RoleDef) -> dict[str, str]:
    return dict(date=datetime.now().strftime('%Y-%m-%d %a'), time=datetime.now().strftime('%H:%M'),
                platform=platform.system(), cwd=os.getcwd(), python_version=sys.version.split()[0],
                role_name=role_def.name, model=role_def.model or 'default')

def _render_prompt(self, template: str, role_def: RoleDef) -> str:
    return template.format_map(defaultdict(str, **self._build_vars(role_def)))
```

## build_messages — 直接使用 owned memory

```python
async def build_messages(self, role_def, query, history) -> list[dict]:
    base_prompt = self._render_prompt(role_def.system_prompt, role_def)

    parts = [base_prompt]
    if self._l0:
        l0_data = await self._l0.get_all()
        if l0_data: parts.append('\n'.join(l0_data.values()))
    parts.append(self._skill.get_metadata_prompt(skill_refs=role_def.skill_refs))
    if self._facts:
        relevant = await self._facts.get_relevant(query, budget=2000)
        if relevant: parts.append(f"# Relevant Context\n{relevant}")

    messages = [{'role': 'system', 'content': '\n\n'.join(parts)}] + history

    for s in self._skill.get_matching(query, skill_refs=role_def.skill_refs)[:2]:
        body = await self._skill.load_skill(s.name)
        messages.insert(1, {'role': 'system', 'content': body})

    return await self.compact_if_needed(messages, role_def.model)
```

## 三层压缩

| 层 | 机制 | 成本 |
|----|------|------|
| MicroCompact | 规则截断：远距 tool_result→200字符、旧 reminder→移除、保护最近 2 轮 | 零 |
| SessionMemory | 已有摘要 → 替换旧消息区段 | 零 |
| FullLLMCompact | LLM 生成 9 段式摘要（触发前先 flush insights/facts） | ~$0.01 |

## 配置

| 参数 | 默认值 |
|------|--------|
| `token_budget` | 128000 |
| `compact_threshold` | 13000 |
| `micro_compact_distance` | 10 |

基类配置在 agent.toml 中（token 参数可调），per-role 的 `role_specs` / `base_dir` 由 `activate_role` 注入。

## Files

`context/service.py`
