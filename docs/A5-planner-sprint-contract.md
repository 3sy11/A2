# 附件五 — 规划器与 Sprint Contract 设计

> 来源于「慢学AI」Full-Stack Harness 架构分析（BV1moXHBME5v），描述三 Agent 协作模式和冲刺契约机制。

---

## 一、三个 Agent 各司其职

规划器接受一句话需求，通过三个角色分工协作完成全栈交付：

### Planner（规划者）

| 维度 | 说明 |
|------|------|
| 输入 | 一句话 prompt |
| 输出 | 完整产品 spec |
| 原则 | **只定义交付什么，不预先规定怎么做** |

Planner 负责将模糊需求扩展为结构化的产品规格，定义每个 sprint 的交付物边界，但不干预具体实现方式。

### Generator（生成者）

| 维度 | 说明 |
|------|------|
| 输入 | Sprint spec |
| 输出 | 可运行的功能代码 |
| 节奏 | 按 sprint 逐功能实现 |
| 质量关 | **每个 sprint 结束后自我评估再交 QA** |

Generator 按 sprint 节奏逐步实现功能，完成后先自检，再提交给 Evaluator 验收。

### Evaluator（评估者）

| 维度 | 说明 |
|------|------|
| 工具 | Playwright MCP 像真实用户点击测试 |
| 标准 | 每条标准有硬性阈值 |
| 判定 | **低于即 sprint 失败** |

Evaluator 不做主观判断，用自动化工具模拟真实用户操作，按硬性阈值验收。

---

## 二、Sprint Contract — 冲刺契约

### 核心机制

每个 sprint 开始前，Generator 和 Evaluator 先协商 **done 是什么**，写下来再开始写代码。

### 解决的问题

> "我以为做完了，但你以为应该做的是另一件事。"

Sprint Contract 消除了交付标准的歧义，让 Generator 和 Evaluator 在开工前就对齐验收条件。

### 契约结构（推测）

```
Sprint Contract #N
─────────────────
目标：[本 sprint 要交付的功能描述]

验收标准：
  ✓ [具体可测试的条件 1]  →  阈值: ...
  ✓ [具体可测试的条件 2]  →  阈值: ...
  ✓ [具体可测试的条件 3]  →  阈值: ...

约束：
  - [技术约束或不做的事]
```

---

## 三、流程总结

```
用户一句话需求
     │
     ▼
┌──────────┐
│  Planner  │  扩展为完整 spec，拆分 sprint
└────┬─────┘
     │ Sprint Spec
     ▼
┌──────────────────────────────────┐
│  Sprint Contract 协商             │  Generator + Evaluator 对齐 done 定义
└────┬─────────────────────────────┘
     │
     ▼
┌──────────┐     自我评估      ┌───────────┐
│ Generator │  ──────────────→  │ Evaluator  │
│ 逐功能实现 │                   │ Playwright │
└──────────┘                   │ 硬性阈值验收 │
     ▲                         └─────┬─────┘
     │                               │
     │  sprint 失败，重新迭代          │ pass/fail
     └────────────────────────────────┘
```

---

## 四、A2 适配分析

### 当前 A2 架构映射

| 本文概念 | A2 对应 |
|----------|---------|
| Planner | `PlannerService` — 接受 goal 生成 TaskList |
| Generator | `AgentService` + `ReActStepCommand` — 执行具体任务 |
| Evaluator | 当前缺失，无自动验收机制 |
| Sprint Contract | 当前缺失，TaskList 只有任务描述无验收标准 |

### 待增强方向

1. **TaskList 增加验收标准字段**：每个 Task 除了 `description` 外增加 `acceptance_criteria: list[str]`，明确 done 的定义。

2. **引入 Evaluator 角色**：
   - 可作为专用 RoleDef，使用 Playwright MCP 或测试框架验证交付物
   - 每个 task 完成后自动触发 evaluation step

3. **Sprint Contract 协商步骤**：
   - Planner 生成 task 后，在 agent 开始执行前插入 contract 协商环节
   - 将验收标准固化到 task metadata 中，evaluation 时逐条核对

4. **自我评估 + 外部评估双重质量关**：
   - Generator 完成后先自检（self-review prompt）
   - 通过后再提交 Evaluator 硬性验收
