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

```python
# cli.py
import asyncio
import click

@click.command()
@click.option('--config', '-c', default='agent.toml', help='配置文件路径')
@click.option('--message', '-m', help='单条消息模式（非交互）')
@click.option('--stream/--no-stream', default=True, help='启用流式输出')
def main(config: str, message: str, stream: bool):
    """A2 Agent CLI。"""
    from bollydog.service import load_from_config
    svc_config = toml.load(config)
    hub = asyncio.run(load_from_config(svc_config))

    if message:
        # 单条消息模式
        cmd = AgentCommand(service=hub._get_agent_service(), user_message=message)
        asyncio.run(hub.execute(cmd))
    else:
        # 交互式 REPL
        asyncio.run(_repl(hub))

async def _repl(hub):
    """交互式 REPL 循环。"""
    agent_svc = hub._get_agent_service()
    print("Agent 就绪。输入消息（或 'exit' 退出，'clear' 重置）。\n")
    while True:
        user_input = input("You> ").strip()
        if user_input.lower() == 'exit':
            break
        if user_input.lower() == 'clear':
            agent_svc.context_manager.reset()
            print("上下文已清除。\n")
            continue

        cmd = AgentCommand(service=agent_svc, user_message=user_input)
        async for chunk in hub.execute(cmd):
            if chunk.get('type') == 'text':
                print(f"\nAgent: {chunk['content']}")
            elif chunk.get('type') == 'tool_result':
                print(f"  [{chunk['name']}] {chunk['content'][:200]}")
```

## HTTP/WS 入口

```python
# http.py
from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect

app = FastAPI()

@app.post("/chat")
async def chat(request: ChatRequest):
    """非流式聊天端点。"""
    cmd = AgentCommand(service=agent_svc, user_message=request.message)
    result = await hub.execute(cmd)
    return {"response": result.state.result()}

@app.websocket("/ws")
async def websocket_chat(ws: WebSocket):
    """WebSocket 流式聊天。"""
    await ws.accept()
    while True:
        try:
            data = await ws.receive_json()
            cmd = AgentCommand(service=agent_svc, user_message=data['message'])
            async for chunk in hub.execute(cmd):
                await ws.send_json(chunk)
        except WebSocketDisconnect:
            break
```

## Files to Create

| File | Purpose |
|------|---------|
| `__init__.py` | 包初始化 |
| `config.py` | 配置加载、环境变量替换 |
| `cli.py` | CLI REPL 入口 |
| `http.py` | HTTP/WS 入口 |

## Dependencies

- `toml` 用于配置解析
- `click` 用于 CLI
- `fastapi` + `uvicorn` 用于 HTTP（可选）
- `websockets` 用于 WS（可选）
