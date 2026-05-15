# 模块：配置 + CLI + 部署

---

## 一、双层配置：config.py（默认）+ agent.toml（覆盖）

### config.py — 全量默认配置

```python
# a2/config.py
from __future__ import annotations
import copy, os
from pydantic import BaseModel

class ServiceSpec(BaseModel):
    key: str; module: str; db: str; table: str
    load_on_start: bool = False; flush_threshold: int = 100

ROLE_DEFAULT: list[ServiceSpec] = [
    ServiceSpec(key='l0',    module='a2.memory.service.MemoryLayerService', db='memory.db', table='rules',    load_on_start=True),
    ServiceSpec(key='l1',    module='a2.memory.service.MemoryLayerService', db='memory.db', table='insights'),
    ServiceSpec(key='l4',    module='a2.memory.service.MemoryLayerService', db='memory.db', table='sessions'),
    ServiceSpec(key='facts', module='a2.memory.facts.GlobalFactsService',   db='facts.db',  table='facts'),
]

SERVICE_DEFAULT: dict[str, dict] = {
    'llm.LLMService': {
        'module': 'a2.llm.service.LLMService',
        'protocol': {
            'module': 'a2.llm.provider.LiteLLMProvider',
            'default_model': 'openai/gpt-4o',
            'models': {
                'openai/gpt-4o':          {'model': 'openai/gpt-4o'},
                'anthropic/claude-3.5':   {'model': 'anthropic/claude-3.5-sonnet'},
                'deepseek/deepseek-chat': {'model': 'deepseek/deepseek-chat'},
            },
        },
    },
    'env.local': {
        'module': 'a2.env.service.EnvService', 'alias': 'local',
        'commands': ['a2.env.tools'],
        'protocol': {
            'module': 'a2.env.protocol.TerminalProtocol',
            'adapter': {'module': 'a2.env.protocol.LocalTerminal', 'working_dir': '.'},
            'protocol': {
                'module': 'a2.env.protocol.PermissionProtocol',
                'rules': [
                    {'pattern': 'read_file', 'action': 'allow'},
                    {'pattern': 'list_dir', 'action': 'allow'},
                    {'pattern': 'grep_search', 'action': 'allow'},
                    {'pattern': '*', 'action': 'allow'},
                ],
            },
        },
    },
    'skills.SkillService': {
        'module': 'a2.skills.service.SkillService',
        'commands': ['a2.skills.commands'],
        'depends': ['llm.LLMService'],
    },
    'planner.PlannerService': {
        'module': 'a2.planner.service.PlannerService',
        'depends': ['llm.LLMService'],
        'use_planning': False, 'use_reflexion': False, 'persist_tasks': True,
    },
    'mcp.MCPService': {
        'module': 'a2.mcp.service.MCPService',
        'depends': ['env.local'],
        'servers': {},
    },
}

def _deep_merge(base: dict, override: dict):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict): _deep_merge(base[k], v)
        else: base[k] = v

def build_config(toml_overrides: dict | None = None) -> dict:
    merged = copy.deepcopy(SERVICE_DEFAULT)
    for key, val in (toml_overrides or {}).items():
        if key in merged: _deep_merge(merged[key], val)
        else: merged[key] = val
    def _subst(obj):
        if isinstance(obj, str) and '${' in obj:
            for var in set(v[2:-1] for v in __import__('re').findall(r'\$\{[^}]+\}', obj)):
                obj = obj.replace(f'${{{var}}}', os.environ.get(var, ''))
            return obj
        if isinstance(obj, dict): return {k: _subst(v) for k, v in obj.items()}
        if isinstance(obj, list): return [_subst(i) for i in obj]
        return obj
    return _subst(merged)
```

### load_a2_config — 不修改 bollydog

```python
def load_a2_config(merged: dict):
    from mode.utils.imports import smart_import
    from bollydog.models.service import AppService
    for node_name, node_conf in merged.items():
        module = node_conf.pop('module', node_name)
        smart_import(module).create_from(**node_conf)
    for svc in list(AppService._apps.values()):
        resolved = []
        for dep_key in (svc.depends if isinstance(svc.depends, (list, tuple)) and svc.depends and isinstance(svc.depends[0], str) else []):
            dep = AppService._apps.get(dep_key)
            if dep is None: raise ValueError(f"depends '{dep_key}' not found for {svc.domain}.{svc.alias}")
            svc.add_dependency(dep); resolved.append(dep)
        if resolved: svc.depends = resolved
    for svc in AppService._apps.values():
        if type(svc).commands: type(svc)._load_commands(type(svc).commands)
```

### agent.toml — 纯模块配置覆盖

TOML 只做各模块的配置覆盖，不包含种子角色和路由（角色由 CreateRoleCommand 或直接写入 roles.db 管理）。

```toml
[hub]
domain = "agent"

# ── Agent 模块 ──
[agent.AgentService]
module = "a2.agent.service.AgentService"
commands = ["a2.agent.commands"]
[agent.AgentService.protocol]
module = "bollydog.adapters.composite.CacheLayer"
[agent.AgentService.protocol.protocol]
module = "bollydog.adapters.memory.SQLiteProtocol"
path = ".agent/roles.db"
table = "roles"

# ── Context 模块 ──
[context.ContextService]
module = "a2.context.service.ContextService"
token_budget = 128000
compact_threshold = 13000

# ── 覆盖示例（取消注释启用）──

# 切换默认 LLM 模型
# [llm.LLMService.protocol]
# default_model = "deepseek/deepseek-chat"

# 替换环境为 Docker
# [env.local.protocol.adapter]
# module = "a2.env.protocol.DockerTerminal"
# image = "python:3.12-slim"

# 替换环境为 SSH
# [env.local.protocol.adapter]
# module = "a2.env.protocol.SSHTerminal"
# host = "prod.example.com"

# 追加 MCP server
# [mcp.MCPService.servers.github]
# command = "npx"
# args = ["-y", "@modelcontextprotocol/server-github"]
# env = { GITHUB_TOKEN = "${GITHUB_TOKEN}" }
```

---

## 二、启动流程

```
build_config(toml_overrides)     # Python 默认 + TOML 覆盖 + ${VAR} 替换
  ↓
load_a2_config(merged_dict)      # smart_import → create_from → 解析 depends → _load_commands
  ↓
bollydog 拓扑排序 → 实例化全局单例:
  LLM → Env(local) → Skill → Planner → MCP → AgentService
  ↓
AgentService.on_started():
  ├── 从 roles.db 加载 RoleDef → self._roles
  └── activate_role('default')（若 roles.db 中有 default）
      ├── type('default_ContextService', (ContextService,), ...)
      ├── 配置 role_specs + base_dir
      ├── 注入 _skill / _llm 全局引用
      └── ctx.maybe_start() → ContextService.on_start
          └── 创建 owned L0/L1/L4/Facts AppService
  ↓
等待用户交互
```

---

## 三、CLI + HTTP

### CLI（python-fire）

```python
import fire
class CLI:
    @staticmethod
    def service(config='agent.toml'):
        # build_config + load_a2_config + start
        ...
    @staticmethod
    def chat(message: str, config='agent.toml', role='default'):
        # hub.execute(AgentCommand(goal=message))
        ...

def main(): fire.Fire(CLI)
```

### HTTP（Starlette SSE）

通过 `router_mapping` 自动映射，bollydog `SseHandler` 检测 async generator → SSE 流式输出。

---

## 四、文件结构

```
A2/
├── pyproject.toml
├── agent.toml
├── cli.py                   # fire CLI
├── a2/
│   ├── __init__.py
│   ├── config.py            # SERVICE_DEFAULT + ROLE_DEFAULT + build_config() + load_a2_config()
│   ├── agent/               # 顶层编排 + 角色管理  domain="agent"
│   │   ├── service.py       #   AgentService（含 activate_role / 环境无关工具）
│   │   ├── commands.py      #   AgentCommand + SpawnAgent + CreateRole + Chat + ClearSession
│   │   └── role_def.py      #   RoleDef 模型
│   ├── llm/                 # LLM       domain="llm"
│   │   ├── service.py       #   LLMService
│   │   └── provider.py      #   LiteLLMProvider
│   ├── env/                 # 执行环境   domain="env"（含 Tool）
│   │   ├── service.py       #   EnvService（拥有 ToolCommand + 权限）
│   │   ├── tools.py         #   ToolCommand 基类 + 内置工具
│   │   ├── protocol.py      #   TerminalProtocol + Local/Docker/SSH
│   │   └── permission.py    #   PermissionConfig
│   ├── memory/              # 记忆       domain="memory"（扁平化）
│   │   ├── service.py       #   MemoryLayerService 基类
│   │   ├── facts.py         #   GlobalFactsService（BM25）
│   │   └── commands.py      #   NotifyIndexUpdateCommand
│   ├── skills/              # 技能       domain="skills"
│   │   ├── service.py       #   SkillService
│   │   ├── skill.py         #   Skill 模型
│   │   └── commands.py      #   Extract/Crystallize
│   ├── context/             # 上下文     domain="context"（per-role 动态子类）
│   │   └── service.py       #   ContextService（owns memory instances）
│   ├── planner/             # 规划       domain="planner"
│   │   ├── service.py       #   PlannerService
│   │   ├── models.py        #   Task/TaskList
│   │   └── commands.py      #   Plan/ReAct/Reflexion
│   └── mcp/                 # MCP        domain="mcp"
│       ├── service.py       #   MCPService
│       └── server.py        #   MCPServerProtocol
└── docs/
```

**~25 文件，~2000 行**（移除 ToolService/wrapper 后精简）

---

## 五、Dependencies

```toml
[project]
dependencies = ["bollydog>=0.1.0"]

[project.optional-dependencies]
llm = ["litellm>=1.0.0"]
mcp = ["mcp>=1.0.0"]
env = ["aiodocker>=0.21.0", "asyncssh>=2.14.0"]
search = ["bm25s>=0.2.0"]
all = ["litellm>=1.0.0", "mcp>=1.0.0", "aiodocker>=0.21.0", "asyncssh>=2.14.0", "bm25s>=0.2.0"]
```

bollydog **零修改**。

---

## 六、验证要点

| 层级 | 验证重点 |
|------|---------|
| 叶子服务 | Router 多模型、EnvService 工具注册/权限/降级、Memory KV 往返、Skill 三级披露 |
| MCP | 工具发现 + 聚合 |
| ContextService | per-role 动态子类 + owns memory + 三段式组装 + 模板渲染 + 压缩 |
| AgentService | 角色管理 + activate_role + 完整 loop + 多角色 spawn |
| 入口 | CLI/HTTP + 对抗测试（max_steps/depth/截断/权限/token 超标） |
