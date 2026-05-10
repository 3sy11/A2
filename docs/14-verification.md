# 14 — 验证与测试策略

## Context

测试 Agent 框架需要多个层级：单元测试（独立组件）、集成测试（子系统交互）、端到端测试（完整 Agent Loop）。测试策略遵循 Claude Code 验证 Agent 的模式——对抗性测试，而非仅测试快乐路径。

## 测试层级

### Level 1：单元测试

独立测试每个组件。

#### 工具测试

```python
# tests/test_tools.py
import pytest
from a2.tools.builtin import ReadTool, WriteTool, BashTool

class TestReadTool:
    async def test_read_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        tool = ReadTool()
        result = await tool(path=str(f))
        assert result == "hello"

    async def test_read_with_offset(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3")
        tool = ReadTool()
        result = await tool(path=str(f), offset=1, limit=1)
        assert "line2" in result

class TestBashTool:
    async def test_simple_command(self):
        tool = BashTool()
        result = await tool(command="echo hello")
        assert "hello" in result

    async def test_timeout(self):
        tool = BashTool()
        result = await tool(command="sleep 10", timeout=1)
        assert "timed out" in result.lower() or "error" in result.lower()
```

#### 记忆测试

```python
# tests/test_memory.py
import pytest
from a2.memory.l0.service import L0MetaRulesService
from a2.memory.l2.service import L2GlobalFactsService

class TestL0MetaRules:
    async def test_loads_claude_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# 项目规则\n使用 2 空格缩进。")
        l0 = L0MetaRulesService()
        rules = await l0.get_all()
        assert "2 空格缩进" in rules

class TestL2GlobalFacts:
    async def test_add_and_retrieve(self):
        l2 = L2GlobalFactsService()
        await l2.add_fact("user", "偏好 Python 而非 JavaScript")
        facts = await l2.get_relevant("用户偏好什么语言？", budget=1000)
        assert "Python" in facts
```

#### 技能测试

```python
# tests/test_skills.py
from a2.skills.skill import Skill

class TestSkill:
    def test_serialization(self):
        skill = Skill(
            name="pdf-processing",
            description="处理 PDF 文件",
            tags=["documents"],
            body="## 步骤\n1. 打开文件",
        )
        md = skill.to_markdown()
        restored = Skill.from_markdown(md)
        assert restored.name == skill.name
        assert restored.body == skill.body
```

#### 上下文管理测试

```python
# tests/test_context.py
from a2.context.service import ContextService

class TestContextService:
    async def test_build_context_within_budget(self):
        cs = ContextService()
        cs.token_budget = 10000
        messages = [{"role": "user", "content": "你好"}]
        context = await cs.build_context(messages)
        assert cs._count_tokens(context) <= 10000

    async def test_micro_compact(self):
        cs = ContextService()
        messages = [
            {"role": "system", "content": "你是助手。"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "嗨！"},
            {"role": "tool", "content": "x" * 5000},  # 大的旧工具结果
            {"role": "tool", "content": "y" * 5000},  # 大的旧工具结果
            {"role": "user", "content": "继续"},
        ]
        result = cs._micro_compact(messages)
        # 旧工具结果应被截断
        assert len(result[3]["content"]) < 5000
```

### Level 2：集成测试

测试子系统交互。

#### Agent Loop 集成

```python
# tests/test_agent_loop.py
class TestAgentLoop:
    async def test_simple_tool_call(self, mock_llm):
        """Agent 调用工具并返回结果。"""
        mock_llm.set_responses([
            # 第一次响应：工具调用
            {"choices": [{"message": {
                "tool_calls": [{"id": "tc1", "function": {"name": "bash", "arguments": '{"command": "echo hi"}'}}]
            }}]},
            # 第二次响应：完成
            {"choices": [{"message": {"content": "命令输出是：hi"}}]},
        ])

        agent = AgentCommand(service=mock_service, user_message="运行 echo hi")
        chunks = []
        async for chunk in agent():
            chunks.append(chunk)

        assert any(c.get("type") == "tool_result" for c in chunks)
        assert any(c.get("type") == "text" and "hi" in c.get("content", "") for c in chunks)

    async def test_max_turns_limit(self, mock_llm):
        """Agent 在 max_turns 时停止。"""
        mock_llm.set_always_response({
            "choices": [{"message": {
                "tool_calls": [{"id": "tc1", "function": {"name": "bash", "arguments": '{"command": "echo loop}"'}}]
            }}]
        })

        agent = AgentCommand(service=mock_service, user_message="无限循环")
        agent.max_turns = 5
        chunks = []
        async for chunk in agent():
            chunks.append(chunk)

        assert any("最大轮次" in c.get("content", "") for c in chunks)

    async def test_error_recovery(self, mock_llm):
        """Agent 从工具错误中恢复。"""
        mock_llm.set_responses([
            {"choices": [{"message": {
                "tool_calls": [{"id": "tc1", "function": {"name": "bash", "arguments": '{"command": "invalid_cmd"}'}}]
            }}]},
            {"choices": [{"message": {
                "tool_calls": [{"id": "tc2", "function": {"name": "bash", "arguments": '{"command": "echo fixed"}'}}]
            }}]},
            {"choices": [{"message": {"content": "修复了！"}}]},
        ])
        # Agent 应重试并成功
```

#### LLM Provider 集成

```python
# tests/test_llm.py
class TestLLMProvider:
    async def test_openai_provider_retry(self):
        """速率限制时重试。"""
        provider = OpenAIProvider(model="test", api_key="test")
        # Mock 429 然后 200
        ...

    async def test_model_fallback(self):
        """连续失败时降级到备用模型。"""
        primary = MockProvider(fail_count=3)
        fallback = MockProvider()
        provider = ModelFallbackProvider(primary=primary, fallback=fallback)
        result = await provider.chat(messages=[...])
        assert result  # 应通过 fallback 成功
```

### Level 3：端到端测试

使用真实 LLM 测试完整工作流（可选，需要 API key 的 CI）。

```python
# tests/test_e2e.py
@pytest.mark.e2e
class TestAgentE2E:
    async def test_file_creation_workflow(self):
        """Agent 创建文件、读回、验证内容。"""
        # 使用真实 LLM 的完整集成
        ...

    async def test_skill_crystallization(self):
        """复杂任务后，技能被创建并可复用。"""
        ...
```

## 验证清单

### Phase 1：基础
- [ ] AgentCommand.__call__ yield 流式 chunk
- [ ] ToolService 分发到正确的工具
- [ ] 所有 11 个内置工具正确执行
- [ ] ToolService 权限检查：TOML 配置的 deny 规则阻止匹配的工具调用
- [ ] ToolService 路径沙箱：sandbox 配置防止文件操作逃逸工作目录
- [ ] LLMService 处理 OpenAI API 格式
- [ ] 错误恢复：JSON 解析失败 → 重试
- [ ] max_turns 安全限制生效

### Phase 2：记忆 + 上下文
- [ ] L0-L4 层正确加载和持久化
- [ ] ContextService.build_context 遵守 token 预算
- [ ] MicroCompact 截断旧工具结果
- [ ] SessionMemoryCompact 复用已有摘要
- [ ] FullLLMCompact 生成结构化 9 段式摘要

### Phase 3：技能 + 规划
- [ ] SkillService 加载技能元数据到 system prompt
- [ ] load_skill 按需注入完整正文
- [ ] 结晶从执行轨迹创建技能
- [ ] PlannerService 正确组合增强层（ReAct/PlanAhead/Reflexion）
- [ ] TaskList 持久化到磁盘并能在重启后恢复

### Phase 4：子智能体 + MCP
- [ ] 子智能体在隔离上下文中运行
- [ ] 只有摘要流回父智能体
- [ ] MCP 工具注册并执行

### Phase 5：集成
- [ ] TOML 配置加载所有子系统
- [ ] CLI REPL 端到端工作
- [ ] HTTP/WS 端点功能正常
- [ ] bollydog Hub 正确分发 agent 命令
- [ ] Exchange pub/sub 用于 agent 事件

## 运行测试

```bash
# 单元测试
pytest tests/ -v --ignore=tests/test_e2e.py

# 集成测试
pytest tests/ -v -m "not e2e"

# E2E 测试（需要 API key）
DEEPSEEK_API_KEY=xxx pytest tests/test_e2e.py -v

# 覆盖率
pytest tests/ --cov=a2 --cov-report=html
```

## 对抗性测试（Claude Code 模式）

受验证 Agent 启发——测试可能出错的地方：

1. **无限循环**：Agent 持续调用工具不终止 → 测试 max_turns
2. **上下文投毒**：坏的工具结果污染后续轮次 → 测试错误处理
3. **Token 溢出**：上下文超出预算 → 测试压缩触发
4. **工具幻觉**：LLM 调用不存在的工具 → 测试优雅错误
5. **递归子智能体**：子智能体尝试生成子子智能体 → 测试反递归
6. **路径逃逸**：工具尝试读取 /etc/passwd → 测试路径沙箱
7. **破坏性操作**：Agent 运行 rm -rf → 测试安全钩子
