# A2 Walking Skeleton（P7）

> Issue: `20260826-a2-redesign`
> 最薄可运行切片：S01 直接回答 + S02 读文件工具。

## 已实现（本 issue）

| 场景 | 路径 | 验证 |
|------|------|------|
| S01 直接回答 | Reply → Assemble → Generate → reply.finished | `tests/test_reply_s01.py::test_s01_direct_reply` |
| S02 读文件 | tool.toolkit.Invoke → workspace.local.ReadPath | `tests/test_reply_s01.py::test_read_path_tool` |
| 会话打开 | session.store.OpenSession | `tests/test_reply_s01.py::test_open_session` |
| 工具发现 | tool.toolkit.ListTools | `tests/test_reply_s01.py::test_list_tools` |

## 运行方式

```bash
# 安装（需要本地 bollydog）
uv sync

# 测试
uv run pytest tests/ -v

# CLI 单次执行
uv run bollydog execute Reply \
  --config config/agent.toml \
  --session_id demo \
  --inputs '[{"role":"user","content":"你好"}]'

# 启动服务（HTTP + SSE）
uv run bollydog service --config config/agent.toml
```

## 目录结构

```
a2/
├── kernel/          # A2Service, ref, chunk, relay, utils
├── message/         # Msg, Block 领域模型
├── protocols/       # ChatModel, Embedding, Vector, Sandbox, Permission ABCs
├── agent/           # ReAct 主循环
├── model/           # ChatModel + Embedding
├── context/         # 上下文装配/压缩
├── tool/            # 工具注册与执行
├── session/         # 跨回合持久化
├── workspace/       # 沙箱与文件工具
├── plan/ skill/ memory/ mcp/ knowledge/ credential/ team/ observe/
└── entrypoint/      # HTTP, Scheduler
config/agent.toml    # 默认配置
tests/               # 四层测试（L1 kernel + L4 E2E）
```

## P8 待办（_stub_ 清单）

- [ ] LiteLLM 生产 ChatModel 实现
- [ ] MCP Stdio/Http 真实连接
- [ ] Flow 域（dataagent2 ChatFlowEngine 对等）
- [ ] Harness 中间件（confirm/spill/flow intercept）
- [ ] 停放态过期清扫
- [ ] Skill InstallSkill（ZIP/GitHub）
- [ ] dataagent2 专用：turn router、artifact spill、eval platform
