# 模块：SkillService

> domain=`skills` | 单一实例 | Protocol: CacheLayer → SQLiteProtocol（skills 表）+ 文件系统

---

## 架构

```
SkillService(AppService), domain="skills", depends=["llm.LLMService"]
├── protocol: CacheLayer → SQLiteProtocol(path='.agent/skills.db', table='skills')
├── skills_dir: str = '.agent/skills'     ← 文件系统根目录
├── _skills: dict[str, Skill]             ← 内存缓存
├── get_metadata_prompt() → Level 1 索引
├── get_always_skills_prompt() → 始终加载
├── load_skill(name) → Level 2 正文（从文件读取）
├── get_matching(query) → 关键词匹配（从 DB 查询）
└── save_skill(skill) → DB 写元数据 + 文件写正文
```

## Skill 模型（配置化 + 文件引用）

Skill 元数据存 DB（高频查询），正文和资源通过文件路径引用（低频大体积）。

```python
class Skill(BaseModel):
    name: str; description: str; tags: list[str] = []
    trigger_keywords: list[str] = []; always: bool = False
    body_path: str = ''                       # 相对于 skills_dir 的正文文件路径
    resource_paths: dict[str, str] = {}       # 附属资源文件引用
    script_paths: dict[str, str] = {}         # 可执行脚本引用
    usage_count: int = 0; success_count: int = 0
```

详见 [01-data-models.md](01-data-models.md) §四。

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

## 配置 → skills/config.py

```python
# a2/skills/config.py
SKILLS_DEFAULT = {
    'module': 'a2.skills.service.SkillService',
    'commands': ['a2.skills.commands'],
    'depends': ['llm.LLMService'],
    'protocol': {
        'module': 'bollydog.adapters.composite.CacheLayer',
        'protocol': {
            'module': 'bollydog.adapters.memory.SQLiteProtocol',
            'path': '.agent/skills.db',
            'table': 'skills',
        },
    },
    'skills_dir': '.agent/skills',
}
```

TOML 覆盖示例：
```toml
[skills.SkillService]
skills_dir = "/shared/team-skills"
```

## Files

`skills/service.py`、`skills/skill.py`、`skills/commands.py`（ExtractPattern + Crystallize）、`skills/config.py`
