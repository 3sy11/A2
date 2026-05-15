# 模块：SkillService

> domain=`skills` | 单一实例 | Protocol: CacheLayer → FileProtocol

---

## 架构

```
SkillService(AppService), domain="skills", depends=["llm.LLMService"]
├── protocol: CacheLayer → FileProtocol
├── _skills: dict[str, Skill]
├── get_metadata_prompt() → Level 1 索引
├── get_always_skills_prompt() → 始终加载
├── load_skill(name) → Level 2 正文
├── get_matching(query) → 关键词匹配
└── save_skill(skill) → 持久化
```

## Skill 模型

```python
class Skill(BaseModel):
    name: str; description: str; tags: list[str] = []
    trigger_keywords: list[str] = []; always: bool = False
    body: str = ''; resources: dict[str, str] = {}
    usage_count: int = 0; success_count: int = 0

    def to_markdown(self) -> str:
        frontmatter = yaml.dump({'name': self.name, 'description': self.description,
                                  'tags': self.tags, 'trigger_keywords': self.trigger_keywords})
        return f"---\n{frontmatter}---\n\n{self.body}"

    @classmethod
    def from_markdown(cls, content: str) -> 'Skill': ...
```

## 三级渐进式披露

| Level | 内容 | ~Token | 触发 |
|-------|------|--------|------|
| 1 元数据 | 名 + 描述 + 标签 | ~100/技能 | 始终在 system |
| 2 正文 | 完整执行指令 | ~2K/技能 | 关键词匹配 / LLM 请求 |
| 3 资源 | 附属文档、脚本 | 可变 | 正文 @引用 |

## 结晶（Command 化）

```
任务成功 + tool_calls ≥ 3 → yield CrystallizeSkillCommand (AgentService)
  → yield ExtractPatternCommand → app._llm.chat(model=role_def.model)
  → app._skill.save_skill(skill)
  → yield NotifyIndexUpdateCommand → memory.l1.set()
```

## 技能隔离：全局 Hub + 角色 skill_refs

Skill 是全局共享资源，结晶产物写入全局 `.user/skills/`，所有角色可受益。每个角色通过 `skill_refs` 控制可见子集。

```python
class RoleDef(BaseModel):
    skill_refs: list[str] = ['*']   # '*'=全部, []=无, 或指定名称列表
```

| 角色 | skill_refs | 可见技能 |
|------|-----------|---------|
| default | `['*']` | 全部 |
| explorer | `['code-review']` | 仅 code-review |
| orchestrator | `[]` | 无 |

```python
def get_metadata_prompt(self, skill_refs: list[str] = None) -> str:
    skills = self._skills.values() if not skill_refs or '*' in skill_refs else [s for s in self._skills.values() if s.name in skill_refs]
    return '\n'.join(s.to_index_line() for s in skills)

def get_matching(self, query: str, skill_refs: list[str] = None) -> list[Skill]:
    candidates = list(self._skills.values()) if not skill_refs or '*' in skill_refs else [s for s in self._skills.values() if s.name in skill_refs]
    return [s for s in candidates if any(kw in query.lower() for kw in s.trigger_keywords)]
```

## 配置 → config.py

```python
'skills.SkillService': {
    'module': 'a2.skills.service.SkillService',
    'commands': ['a2.skills.commands'],
    'depends': ['llm.LLMService'],
},
```

## Files

`skills/service.py`、`skills/skill.py`、`skills/commands.py`（ExtractPattern + Crystallize）
