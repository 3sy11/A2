# 附件三 — 中间件全景与 SDK 能力对比

> 来源于对 LangChain/DeepAgents、Claude Agent SDK、Codex SDK 的分析，供 A2 架构参考。

---

## 一、LangChain 中间件全景：6 大类别 · 16 预构建中间件

LangChain/DeepAgents 将 agent 执行过程中的交叉关注点归纳为六大类别，每类提供预构建中间件，可组合注入 agent 执行链。

### 1. 规划与委派

| 中间件 | 功能 | 说明 |
|--------|------|------|
| **To-do list** | 任务规划追踪 | 维护结构化任务列表，跟踪完成状态，agent 结束前检查未完成项 |
| **Subagent** | 生成子 Agent | 动态创建子 agent 执行子任务，支持并发限制和 token 归属 |

### 2. 上下文管理

| 中间件 | 功能 | 说明 |
|--------|------|------|
| **Summarization** | 自动总结 | 多级压缩策略（COLLAPSE → TRUNCATE → MICROCOMPACT → SUMMARIZE），token 超阈值时触发 |
| **Context editing** | 修剪上下文 | 精确裁剪历史消息，移除冗余 tool result，保持上下文窗口高效利用 |

### 3. 安全与审批

| 中间件 | 功能 | 说明 |
|--------|------|------|
| **Human-in-the-loop** | 人工审批 | 敏感操作前暂停执行等待人类确认，支持超时和自动放行策略 |
| **PII detection** | 隐私检测 | 扫描输入/输出中的个人身份信息，脱敏或阻断 |

### 4. 弹性与重试

| 中间件 | 功能 | 说明 |
|--------|------|------|
| **Tool retry** | 工具重试 | Tool 执行失败后自动重试，支持退避策略 |
| **Model retry** | 模型重试 | LLM API 调用失败（超时/限流/5xx）时自动重试 |
| **Model fallback** | 模型降级 | 主模型不可用时切换备选模型（如 GPT-4 → Claude → 本地模型） |

### 5. 调用限制

| 中间件 | 功能 | 说明 |
|--------|------|------|
| **Tool call limit** | 工具调用限制 | 限制单次执行中 tool 调用总次数，防止无限循环 |
| **Model call limit** | 模型调用限制 | 限制 LLM 调用次数，控制成本和防止死循环 |

### 6. 工具与文件

| 中间件 | 功能 | 说明 |
|--------|------|------|
| **LLM tool selector** | 智能工具选择 | 根据当前上下文动态筛选相关 tool，减少 token 消耗 |
| **LLM tool emulator** | 工具模拟 | 用 LLM 模拟不可用的 tool 行为，提供降级体验 |
| **Shell tool** | Shell 执行 | 提供命令行执行能力，支持超时和沙箱隔离 |
| **File search** | 文件搜索 | 语义/关键词搜索文件内容 |
| **Filesystem** | 文件系统操作 | 完整文件 CRUD 能力 |

---

## 二、六大文件系统工具

Agent 通过以下六个原子工具覆盖文件操作的完整生命周期：

| 工具 | 功能 | 类比 |
|------|------|------|
| **ls** | 列出目录中的文件和元信息 | 打开文件夹看看有什么 |
| **read_file** | 读取文件内容，支持分片（offset + limit） | 翻开某份资料阅读 |
| **write_file** | 创建新文件 | 写一份新的备忘录 |
| **edit_file** | 精确字符串替换 | 用红笔修改文档 |
| **glob** | 按模式匹配查找文件 | 在文件柜中按标签找 |
| **grep** | 搜索文件内容，支持正则表达式 | 全文检索 |

### 设计原则

- **原子性**：每个工具职责单一，组合使用覆盖所有场景
- **安全性**：路径校验防止越权访问，写操作可配置白名单
- **高效性**：read_file 支持分片避免大文件一次性加载，grep 支持结果截断

### A2 映射

| 文件工具 | A2 对应实现 |
|----------|-------------|
| ls | `ShellCommand` 执行 `ls` / 内置 `list_dir` tool |
| read_file | 内置 `read_file` tool（SkillService 已使用） |
| write_file | 内置 `write_file` tool |
| edit_file | 内置 `edit_file` tool（字符串替换语义） |
| glob | 内置 `glob` tool 或 Shell `find` |
| grep | 内置 `grep` tool 或 Shell `rg` |

---

## 三、GA（Gemini Agent）9 工具 · 五大能力类

Google Gemini Agent 将 agent 内置工具精简为 **9 个工具**，覆盖 **5 大能力类**，形成最小完备工具集：

### 能力类总览

| # | 能力类 | 工具 | 说明 |
|---|--------|------|------|
| 1 | 文件操作 | `file_read` | 读取文件内容 |
| 2 | 文件操作 | `file_write` | 创建/覆写整个文件 |
| 3 | 文件操作 | `file_patch` | 对已有文件做局部补丁修改 |
| 4 | 代码执行 | `code_run` | 在沙箱中执行代码/命令 |
| 5 | 网页交互 | `web_scan` | 扫描/抓取网页内容 |
| 6 | 网页交互 | `web_execute_js` | 在浏览器环境中执行 JavaScript |
| 7 | 记忆管理 | `update_working_checkpoint` | 更新当前工作进度检查点（短期记忆） |
| 8 | 记忆管理 | `start_long_term_update` | 启动长期记忆更新（跨会话持久化） |
| 9 | 人机协作 | `ask_user` | 向用户提问获取澄清信息 |

### 设计哲学

- **最小完备**：9 个工具覆盖 agent 所需全部原子能力，不多不少
- **能力类分层**：每类工具解决一个维度的问题，互不重叠
- **file_write vs file_patch 分离**：全量写入和局部修改是不同语义，分开让模型决策更清晰（对应 A6 "一个工具一种意图"原则）
- **记忆双层**：working checkpoint（当前任务进度）和 long-term update（跨会话知识）分离，对应 Session vs Memory 的概念边界

### 各能力类详解

#### 文件操作（file_read / file_write / file_patch）

| 工具 | 语义 | 与 LangChain 工具对比 |
|------|------|----------------------|
| `file_read` | 读取指定文件内容 | ≈ `read_file` |
| `file_write` | 创建新文件或完整覆写 | ≈ `write_file` |
| `file_patch` | 对已有文件做精确局部修改 | ≈ `edit_file`（字符串替换） |

GA 省略了 `ls`/`glob`/`grep`，这些能力隐含在 `code_run` 中（通过执行 shell 命令实现）。

#### 代码执行（code_run）

统一的代码/命令执行入口，替代了独立的 Shell tool。agent 通过此工具：
- 执行 shell 命令（ls、grep、git 等）
- 运行代码片段（Python、Node 等）
- 构建和测试项目

#### 网页交互（web_scan / web_execute_js）

| 工具 | 场景 |
|------|------|
| `web_scan` | 获取网页内容（类似 fetch + 解析） |
| `web_execute_js` | 需要动态交互的场景（点击、填表、等待渲染） |

两者分离的原因：大多数场景只需静态抓取，`web_scan` 更轻量；需要交互时才升级到 `web_execute_js`。

#### 记忆管理（update_working_checkpoint / start_long_term_update）

| 工具 | 对应概念 | 生命周期 |
|------|----------|---------|
| `update_working_checkpoint` | 工作进度快照 | 当前任务内，类似 Session emitEvent |
| `start_long_term_update` | 长期知识沉淀 | 跨会话持久化，类似 Memory facts |

这对应 A4 中"Session ≠ Context Window"的设计：checkpoint 是任务级，long-term 是用户级。

#### 人机协作（ask_user）

专用的用户提问工具，对应 A6 中 AskUserQuestion 三次迭代的最终方案——给"提问"行为建独立具名工具。

### A2 工具覆盖度对比

| GA 能力类 | GA 工具 | A2 当前覆盖 | 状态 |
|-----------|---------|-------------|------|
| 文件操作 | `file_read` | 内置 `read_file` tool | ✅ 已覆盖 |
| 文件操作 | `file_write` | 内置 `write_file` tool | ✅ 已覆盖 |
| 文件操作 | `file_patch` | 内置 `edit_file` tool | ✅ 已覆盖 |
| 代码执行 | `code_run` | `ShellCommand` / MCP shell tool | ✅ 已覆盖 |
| 网页交互 | `web_scan` | `web_fetch`（AgentService 环境无关工具） | ✅ 已覆盖 |
| 网页交互 | `web_execute_js` | 无内置，需 Playwright MCP | ⚠️ 需 MCP 扩展 |
| 记忆管理 | `update_working_checkpoint` | 无独立工具，隐含在 ContextService 中 | ❌ 未覆盖 |
| 记忆管理 | `start_long_term_update` | MemoryService L1 insights 自动提取 | ⚠️ 隐式，非工具化 |
| 人机协作 | `ask_user` | 内置 `ask_user` tool | ✅ 已覆盖 |

### A2 待补齐项

1. **`web_execute_js` 能力**：当前依赖外部 Playwright MCP server。可考虑：
   - 内置轻量 browser tool（基于 Playwright）
   - 或确保 Playwright MCP 作为默认推荐配置

2. **`update_working_checkpoint` 工具化**：
   - 将 "保存当前工作进度" 暴露为显式 tool，让 agent 主动决定何时打检查点
   - 对应 Session `emitEvent()` 的 tool 化封装
   - 用途：长任务中间保存进度，harness 崩溃后可恢复

3. **`start_long_term_update` 工具化**：
   - 当前 Memory 更新是 middleware 自动触发（防抖批量）
   - 可增加显式工具让 agent 主动沉淀重要发现到长期记忆
   - 对应 MemoryService 的 `update_facts()` 的 tool 化封装

---

## 四、核心能力对比：Deep Agents vs Claude Agent SDK vs Codex SDK（含 GA）

### 各家独有优势

#### Deep Agents（LangChain）

| 独有能力 | 说明 |
|----------|------|
| **模型灵活性** | 支持任意 LLM provider（OpenAI/Anthropic/Google/本地模型），运行时切换 |
| **长期记忆** | 内置 Memory 系统（user context / history / facts），跨会话持久化 |
| **虚拟文件系统 + 可插拔后端** | 抽象文件系统层，后端可为本地/S3/Docker volume |
| **Sandbox-as-Tool** | 代码沙箱作为 tool 暴露，agent 自主决定何时使用隔离环境 |
| **LangSmith 可观测性** | 原生集成 LangSmith trace/eval，生产级监控和调试 |

#### Claude Agent SDK

| 独有能力 | 说明 |
|----------|------|
| **Claude 深度集成** | 针对 Claude 模型特性深度优化（extended thinking、tool_use 原生支持） |
| **Hooks 拦截系统** | 生命周期 hooks（before/after model/tool），支持阻断和修改 |
| **自定义 HTTP/WebSocket 层** | 完全可控的传输层，支持自定义协议和中间代理 |

#### Codex SDK（OpenAI）

| 独有能力 | 说明 |
|----------|------|
| **OS 级沙箱模式** | 操作系统级隔离（非容器），更轻量更安全 |
| **内置 MCP Server** | 原生 MCP 协议支持，无需额外配置即可对接 MCP 工具生态 |
| **云端执行环境** | 支持云端 agent 执行，无需本地资源 |

### 共同能力

三者均支持以下核心能力：

| 能力 | 说明 |
|------|------|
| **文件读写** | 完整文件系统操作（ls/read/write/edit/glob/grep） |
| **Shell 执行** | 命令行执行，支持超时和输出捕获 |
| **搜索** | 文件内容搜索 + 语义搜索 |
| **规划** | 任务分解和 todo 管理 |
| **子 Agent** | 动态生成子 agent 执行子任务 |
| **MCP** | Model Context Protocol 工具协议支持 |
| **人机协作** | Human-in-the-loop 审批/确认机制 |

### 对比总结

```
                Deep Agents       Claude Agent SDK       Codex SDK          GA (Gemini Agent)
                ─────────────     ────────────────       ─────────          ─────────────────
定位            通用框架            Claude 生态专用         OpenAI 生态专用     Google 生态专用
模型支持        多模型              Claude only            OpenAI only        Gemini only
内置工具数      16+ 中间件          hooks 定制             ~6                 9（五大能力类）
隔离方案        Docker/E2B         自定义                  OS 沙箱            云端沙箱
可观测性        LangSmith           自建                   自建               Vertex AI 集成
传输协议        LangGraph API       HTTP/WS 可定制         REST API           Vertex API
开源程度        完全开源            部分开源               部分开源            闭源
```

---

## 五、A2 中间件策略总结

基于以上分析，A2 的中间件策略采用三层设计，对应 LangChain 6 大类别的映射：

| LangChain 类别 | A2 实现层 | 具体机制 |
|---------------|-----------|----------|
| 规划与委派 | Command 内置 | `PlannerService` + `SpawnAgentCommand` |
| 上下文管理 | Service Hook | `ContextService.compact_messages()` 四级压缩 |
| 安全与审批 | Command 内置 | `confirm()` yield 暂停 + skill `allowed_tools` 白名单 |
| 弹性与重试 | Service Hook | `LLMService` 内置 retry + fallback chain |
| 调用限制 | Command 内置 | `ReActStepCommand` max_steps + LoopDetection |
| 工具与文件 | MCP + 内置 Tool | `MCPService` 动态发现 + 6 大文件工具内置 |

### 与三大 SDK 的差异化定位

A2 取各家之长：

- **来自 Deep Agents**：多模型支持、长期记忆、中间件可组合性
- **来自 Claude Agent SDK**：Hooks 拦截思想（映射为 Command yield 链）
- **来自 Codex SDK**：MCP 原生支持
- **来自 GA**：9 工具最小完备集思想、记忆管理工具化（checkpoint + long-term 分离）

**A2 独有**：
- bollydog Command yield 链天然中间件语义（无需额外抽象层）
- Skill 三级披露机制（元数据 → 关键词触发 → 全文注入）
- Exchange 事件总线解耦非阻塞逻辑
