# 11 — 配置

## Context

配置通过 TOML 文件驱动整个 Agent 框架，遵循 bollydog 的现有模式。配置系统使用 `_build_protocol()` 构建嵌套协议链，`AppService.create_from()` 实例化服务，环境变量替换处理密钥。

## TOML 配置结构

```toml
# 完整 Agent 配置示例

[hub]
domain = "agent"

# ============================================================
# 顶层 Agent 服务
# ============================================================

["a2.AgentService"]
module = "a2.agent.service.AgentService"
commands = ["a2.agent.commands"]
depends = [
    "a2.LLMService",
    "a2.ToolService",
    "a2.ContextService",
    "a2.PlannerService",
    "a2.SubagentService",
    "a2.MCPService",
    "a2.SkillService",
]

["a2.AgentService".agent_loop]
max_turns = 50
stream = true

# ============================================================
# LLM 服务
# ============================================================

["a2.LLMService"]
module = "a2.llm.service.LLMService"

["a2.llm.fast.FastLLMService"]
module = "a2.llm.fast.service.FastLLMService"

["a2.llm.fast.FastLLMService".protocol]
module = "a2.llm.fallback.ModelFallbackProvider"

["a2.llm.fast.FastLLMService".protocol.protocol]
module = "a2.llm.provider.OpenAIProvider"
model = "deepseek-chat"
api_key = "${DEEPSEEK_API_KEY}"
base_url = "https://api.deepseek.com"

["a2.llm.capable.CapableLLMService"]
module = "a2.llm.capable.service.CapableLLMService"

["a2.llm.capable.CapableLLMService".protocol]
module = "a2.llm.fallback.ModelFallbackProvider"

["a2.llm.capable.CapableLLMService".protocol.protocol]
module = "a2.llm.provider.AnthropicProvider"
model = "claude-sonnet-4-6"
api_key = "${ANTHROPIC_API_KEY}"

["a2.llm.code.CodeLLMService"]
module = "a2.llm.code.service.CodeLLMService"

["a2.llm.code.CodeLLMService".protocol]
module = "a2.llm.fallback.ModelFallbackProvider"

["a2.llm.code.CodeLLMService".protocol.protocol]
module = "a2.llm.provider.OpenAIProvider"
model = "deepseek-coder"
api_key = "${DEEPSEEK_API_KEY}"
base_url = "https://api.deepseek.com"

# ============================================================
# 工具服务
# ============================================================

["a2.ToolService"]
module = "a2.tools.service.ToolService"

# 工具权限配置
["a2.ToolService".permissions]
default = "allow"

[["a2.ToolService".permissions.rules]]
tools = ["bash"]
action = "deny"
patterns = ["rm -rf /", "git push --force"]

[["a2.ToolService".permissions.rules]]
tools = ["read", "write", "edit"]
action = "allow"
sandbox = "."

["a2.env.local.LocalToolService"]
module = "a2.env.local.service.LocalToolService"

["a2.env.local.LocalToolService".protocol]
module = "a2.tools.protocol.LocalProtocol"

# Docker 环境（可选）
# ["a2.env.docker.DockerToolService"]
# module = "a2.env.docker.service.DockerToolService"
#
# ["a2.env.docker.DockerToolService".protocol]
# module = "a2.tools.protocol.DockerProtocol"
# image = "python:3.12-slim"
# volumes = [".:/workspace"]

# SSH 环境（可选）
# ["a2.env.ssh.SSHToolService"]
# module = "a2.env.ssh.service.SSHToolService"
#
# ["a2.env.ssh.SSHToolService".protocol]
# module = "a2.tools.protocol.SSHProtocol"
# host = "remote-server"
# user = "deploy"

# ============================================================
# 记忆服务
# ============================================================

["a2.MemoryService"]
module = "a2.memory.service.MemoryService"

["a2.memory.l0.L0MetaRulesService"]
module = "a2.memory.l0.service.L0MetaRulesService"

["a2.memory.l0.L0MetaRulesService".protocol]
module = "bollydog.adapters.kv.MemoryProtocol"

["a2.memory.l1.L1InsightIndexService"]
module = "a2.memory.l1.service.L1InsightIndexService"

["a2.memory.l1.L1InsightIndexService".protocol]
module = "bollydog.adapters.kv.MemoryProtocol"

["a2.memory.l2.L2GlobalFactsService"]
module = "a2.memory.l2.service.L2GlobalFactsService"
keyword_retrieval = true
llm_retrieval = false
embedding_retrieval = false

["a2.memory.l2.L2GlobalFactsService".protocol]
module = "bollydog.adapters.kv.MemoryProtocol"

["a2.memory.l3.L3TaskSkillsService"]
module = "a2.memory.l3.service.L3TaskSkillsService"
# 无 protocol 配置——数据在 SkillService 的 CompositeProtocol 中

["a2.memory.l4.L4SessionArchiveService"]
module = "a2.memory.l4.service.L4SessionArchiveService"

["a2.memory.l4.L4SessionArchiveService".protocol]
module = "bollydog.adapters.composite.CacheLayer"
flush_threshold = 100

["a2.memory.l4.L4SessionArchiveService".protocol.protocol]
module = "bollydog.adapters.file.LocalFileProtocol"
path = ".agent/session/"

# ============================================================
# 技能服务
# ============================================================

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

# ============================================================
# 上下文服务
# ============================================================

["a2.ContextService"]
module = "a2.context.service.ContextService"
token_budget = 128000
reserved_tokens = 8000
compact_threshold = 13000

# ============================================================
# 规划器
# ============================================================

["a2.PlannerService"]
module = "a2.planner.service.PlannerService"
use_planning = false
use_reflexion = false

# ============================================================
# 子智能体服务
# ============================================================

["a2.SubagentService"]
module = "a2.subagent.service.SubagentService"

# ============================================================
# MCP 服务（可选）
# ============================================================

["a2.MCPService"]
module = "a2.mcp.service.MCPService"

["a2.MCPService".servers.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_TOKEN = "${GITHUB_TOKEN}" }
```

## 环境变量替换

bollydog 的 `load_from_config()` 应支持 `${VAR}` 语法：

```python
def _substitute_env(value: str) -> str:
    """将 ${VAR} 替换为环境变量值。"""
    import re
    def _replace(match):
        var = match.group(1)
        return os.environ.get(var, match.group(0))
    return re.sub(r'\$\{(\w+)\}', _replace, value)
```

## 配置加载流程

```python
# 在 load_from_config() 或 AgentService.create_from() 中：
config = toml.load('agent.toml')

# 1. 替换环境变量
config = _deep_substitute(config)

# 2. 通过 AppService.create_from() 创建服务
# bollydog 自动处理：
#   - 解析 depends 依赖图
#   - 按拓扑顺序实例化服务
#   - _build_protocol() 构建协议链
#   - _load_commands() 加载命令模块
hub = await load_from_config(config)
```

## CLI 入口

使用 bollydog 的 fire CLI 模式保持一致性（bollydog 用 python-fire）：

```python
# cli.py — 复用 bollydog CLI 模式
from bollydog.service import load_from_config
from bollydog.service.app import Hub
from bollydog.bootstrap import Bootstrap
from bollydog.models.base import BaseCommand, BaseService
import fire

class CLI:
    @staticmethod
    def service(config: str = 'agent.toml'):
        """启动 Agent 服务（HTTP/WS/UDS）。"""
        load_from_config(config)
        hub = Hub()
        raise Bootstrap(hub, override_logging=False).execute_from_commandline()

    @staticmethod
    def execute(command: str = 'Chat', config: str = 'agent.toml', **kwargs):
        """直接执行一条命令。"""
        load_from_config(config)
        hub = Hub()
        cmd_cls = BaseService.registry.get(command) or BaseService.registry[f'a2.AgentService.{command}']
        msg = cmd_cls(**kwargs)
        import asyncio
        async def _run():
            async with hub:
                await hub.execute(msg)
        asyncio.run(_run())

    @staticmethod
    def chat(message: str, config: str = 'agent.toml'):
        """单条消息模式。"""
        CLI.execute('Chat', config=config, user_message=message)

    @staticmethod
    def ls(config: str = 'agent.toml'):
        """列出所有注册的命令。"""
        load_from_config(config)
        from bollydog.entrypoint.cli import CLI as BolCLI
        BolCLI.ls(config=config)

def main():
    fire.Fire(CLI)
```

## HTTP/WS 入口

使用 bollydog 内置的 HttpService / SocketService（通过 BOLLYDOG_HTTP_ENABLED 环境变量启用），
无需自定义 FastAPI——bollydog 的 HttpHandler/SseHandler 自动将 router_mapping 映射为 HTTP 端点。

```python
# http.py — 仅在需要自定义端点时使用，通常直接用 bollydog entrypoint
# AgentService 的 router_mapping 已声明 Chat 命令的 SSE 路由
# 启动方式：BOLLYDOG_HTTP_ENABLED=1 python -m a2.cli service --config agent.toml
```

```toml
# agent.toml 中的 router_mapping
["a2.AgentService".router_mapping]
Chat = ["SSE", "/api/chat"]
ClearSession = ["POST", "/api/clear"]
```

bollydog 的 SseHandler 检测 async generator → 自动走 SSE 流式输出。
普通 POST → HttpHandler → 等待 result → JSONResponse。

## Files to Create

| File | Purpose |
|------|---------|
| `__init__.py` | 包初始化 |
| `config.py` | 配置加载、环境变量替换 |
| `cli.py` | CLI REPL 入口 |
| `http.py` | HTTP/WS 入口 |

## Dependencies

- `bollydog` — 核心框架（含 tomllib 配置解析、fire CLI、Starlette HTTP/WS）
- `fire` — CLI（bollydog 已带依赖）
- 不需要额外安装 click/fastapi——复用 bollydog 内置的 entrypoint 层
