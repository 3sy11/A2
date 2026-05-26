# 附件四 — Session 架构与脑手分离设计

> 来源于「慢学AI」对 Agent 架构的拆解分析（BV1cBQhBJEMx），核心主张：通过"两刀结构改造"实现容错、安全和可扩展。

---

## 一、两刀结构改造（The Solution）

核心思想：先把每个部分的职责和接口定死，立成稳定的边界。边界立住以后，里面每一层的实现才可以放心继续变。

### 第一刀：脑手分离

把 Claude + Harness（思考编排侧）和真正执行动作的那一侧分开。

```
┌─────────────────────┐         ┌─────────────────────┐
│  Claude + Harness   │  ←───→  │  Sandbox + Tools    │
│  平台侧，负责思考    │         │  按需调用，负责执行   │
└─────────────────────┘         └─────────────────────┘
```

**接口极薄**，只有两个方法：

| 接口 | 职责 |
|------|------|
| `provision()` | 把这次任务要用的执行环境和资源先准备好 |
| `execute(name, input)` | 调用某个动作，给名称和输入，返回结果 |

### 第二刀：Session 独立

把任务真相从某个活着的进程里拿出来，放到外面的持久层。

```
┌──────────────┐         ┌──────────────┐
│    进程内     │   ──→   │    持久层     │
│ 进程挂了就没了 │         │ 任务真相独立存活│
└──────────────┘         └──────────────┘
```

**四个核心接口**：

| 接口 | 职责 |
|------|------|
| `wake()` | 把任务叫醒 |
| `getSession()` | 把历史记录读回来 |
| `getEvents()` | 按需切片读某一段 |
| `emitEvent()` | 把新发生的事写进去 |

底下用 PostgreSQL、SQLite 还是别的 append-only 存储，都只是实现细节。

---

## 二、Session 设计原则

### SESSION = 持续追加的任务事件流

不是普通聊天记录。每往前走一步，都会追加写入。任务真相不依赖任何活着的进程。

### 档案室 vs 会议桌

| 概念 | 对应 | 特性 |
|------|------|------|
| **档案室** | Session（持久层） | 保存原始记录，细节不丢 |
| **会议桌** | Context Window（运行时） | harness 每一轮可以继续调整桌上摆什么材料 |

> 工头换了，工程还在。

### Session ≠ Context Window

| 维度 | Context Window | Session |
|------|---------------|---------|
| 生命周期 | 这一轮临时能看到的 | 外部持久化真实历史 |
| 容量限制 | 有上限 | 无上限（append-only） |
| 可压缩 | 是（summarization） | 否（原始细节不丢） |
| 丢失风险 | 进程挂了就没 | 独立存活 |

---

## 三、脑手分离之后的效果

### 执行层接口压得很薄

只暴露 `provision()` + `execute(name, input)`，上面不需要知道下面是什么实现。

**容错语义**：容器出错 → 普通 tool-call error → 重新 provision 再试。

### Resources & Tools 也一起稳定

| 组件 | 设计原则 |
|------|----------|
| **Resources** | 按引用接进来，不再长在容器里 |
| **Tools** | 上面看名称+格式，下面实现可以换 |

### 实测效果：首响应时间 TTFT

| 指标 | 改善 |
|------|------|
| p50 中位数 | **↓ 60%** |
| p95 长尾 | **↓ 90%+** |

原因：不需要 sandbox 的 session，思考层直接开始推理，不用先起完整执行环境。

### 容器重新变回 Cattle

下面某个执行单元挂了，上面看到的只是一次执行失败。系统换一个新的执行单元上来继续跑。

---

## 四、这套拆法带来的四个结果（Outcomes）

### 1. 故障被重新定义

| 故障类型 | 处理方式 |
|----------|----------|
| 执行单元挂了 | → 普通执行失败，换新单元继续 |
| Harness 挂了 | → 任务真相在 session 里，新 harness 读回来继续跑 |

> 故障从"任务拖死"变成"可处理的失败"。

### 2. 安全边界清楚了

核心原则：**能力可以给，但秘密不要让它看见**。

| 场景 | 安全设计 |
|------|----------|
| Git | 外层完成 clone/remote 配置，agent 可 push/pull 但看不到 token |
| MCP/自定义工具 | token 放安全保险库，Claude 调用经代理代为鉴权 |

> "结构上拿不到"比"希望模型克制"稳得多。

### 3. Session ≠ Context Window（已述）

档案室保原始记录，会议桌材料每轮可调。

### 4. 更快，也更容易扩

- 思考层不再和固定容器绑死，不用每次先起完整执行环境
- 上面调度层可以单独扩，下面执行层可以按需扩
- 下面抽成统一执行能力：容器 / MCP server / 别的执行目标

**Hands 不绑定 Brain**：多个 brain 可共享同一批执行能力，brain 之间可转交 hands。

---

## 五、完整请求流程（Full Picture）

```
  ①              ②              ③                    ④              ⑤
用户请求 ──→  调度层 wake  ──→  Claude+Harness  ──→  执行动作  ──→  emitEvent
创建/更新       把 session      读历史，做判断       provision →     结果写回
session        叫起来                               execute        session
                                    │                              进入下一轮
                                    ├── 只是分析规划
                                    │   继续在平台侧推进
                                    │   不起执行环境
                                    │
                                    └── 需要动手
                                        provision → execute
                                        结果带回来
```

**三方职责**：

| 角色 | 职责 |
|------|------|
| Claude + Harness | 负责思考和编排 |
| 执行环境 + 工具 | 负责真的动手 |
| Session | 负责保存任务真相 |

---

## 六、A2 适配分析

### 当前 A2 架构对应

| 本文概念 | A2 对应 |
|----------|---------|
| Claude + Harness | `AgentService` + `ReActStepCommand` |
| provision() + execute() | `MCPService.call_tool()` / `ShellCommand` |
| Session 持久层 | 当前为进程内 `ContextService` 管理的 message list |
| wake() | CLI 入口 `a2 chat` 启动 |
| emitEvent() | Command yield 链写入 context |

### 待增强方向

1. **Session 外部化**：当前任务历史在进程内，进程退出即丢失。需引入 append-only 持久层（SQLite / 文件），实现 `wake/getSession/getEvents/emitEvent` 四接口。

2. **脑手分离明确化**：当前 tool 执行与思考在同一进程内。可将执行层抽象为 `ExecutionBackend` 协议：
   ```python
   class ExecutionBackend(Protocol):
       async def provision(self, requirements: dict) -> None: ...
       async def execute(self, name: str, input: dict) -> dict: ...
   ```

3. **安全边界落地**：
   - Git token 不注入 agent 可见的环境变量，改为代理层代为鉴权
   - MCP tool 的 credential 放在 harness 侧，agent 只看到工具名称和格式

4. **容错恢复**：
   - 执行单元失败 → 自动重新 provision + retry
   - Harness 崩溃 → 从 session 持久层恢复，继续执行
