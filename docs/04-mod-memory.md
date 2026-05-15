# 模块：Memory（MemoryLayerService + GlobalFactsService）

> domain=`memory` | 由 ContextService 动态创建管理 | Protocol: CacheLayer → SQLiteProtocol

---

## 一、MemoryLayerService（L0/L1/L4）

### 基类

```python
class MemoryLayerService(AppService):
    depends = []; load_on_start: bool = False

    async def on_start(self):
        self.load_on_start = self.config.get('load_on_start', False)
        if self.load_on_start: await self._preload()

    async def get(self, key: str) -> str | None: return await self.protocol.get(key)
    async def set(self, key: str, value: str): await self.protocol.set(key, value)
    async def get_all(self) -> dict[str, str]:
        return await self.protocol.get_all() if hasattr(self.protocol, 'get_all') else {}
```

L0/L1/L4 **不在 TOML 中配置**，由 ContextService 在 `on_start` 中通过 ServiceSpec 创建。每个角色拥有独立 DB 目录（`.agent/roles/{namespace}/`），底层统一使用 `CacheLayer → SQLiteProtocol`。

### 配置 → config.py `ROLE_DEFAULT`

```python
ServiceSpec(key='l0', module='a2.memory.service.MemoryLayerService', db='memory.db', table='rules',    load_on_start=True)
ServiceSpec(key='l1', module='a2.memory.service.MemoryLayerService', db='memory.db', table='insights')
ServiceSpec(key='l4', module='a2.memory.service.MemoryLayerService', db='memory.db', table='sessions')
```

---

## 二、GlobalFactsService（L2）

独立 Service 类（有 BM25 检索逻辑），放在 `memory/facts.py`：

```python
class GlobalFactsService(AppService):
    depends = []
    _index: 'bm25s.BM25' = None; _facts: dict[str, str] = {}

    def _rebuild_index(self):
        import bm25s
        corpus = list(self._facts.values())
        if corpus:
            self._index = bm25s.BM25()
            self._index.index(bm25s.tokenize(corpus))

    async def add_fact(self, key: str, value: str):
        self._facts[key] = value
        await self.protocol.set(key, value)
        self._rebuild_index()

    async def search(self, query: str, top_k: int = 5) -> list[str]:
        if not self._index: return []
        import bm25s
        tokens = bm25s.tokenize([query])
        results, scores = self._index.retrieve(tokens, k=min(top_k, len(self._facts)))
        keys = list(self._facts.keys())
        return [self._facts[keys[i]] for i in results[0] if scores[0][i] > 0]

    async def get_relevant(self, query: str, budget: int = 2000) -> str:
        facts = await self.search(query)
        result, total = [], 0
        for f in facts:
            est = len(f) // 4
            if total + est > budget: break
            result.append(f); total += est
        return '\n'.join(result)
```

### 配置

同 L0/L1/L4，由 ContextService 创建管理，每角色独立 `facts.db`：

```python
ServiceSpec(key='facts', module='a2.memory.facts.GlobalFactsService', db='facts.db', table='facts')
```

---

## 三、Session 与角色绑定

会话只与角色绑定，用 namespace 隔离。角色服务实例生命周期**长于**单次会话。

```
namespace = {role_namespace}/{trace_id}
  default/abc123          ← 主会话
  explorer/abc123-sub1    ← SpawnAgent 子会话
```

| 事件 | ContextService 实例 | session |
|------|-------------------|---------|
| 首次 Spawn("explorer") | 触发 activate_role → 建 DB + 创建 ContextService | 创建子 namespace |
| 再次 Spawn("explorer") | 幂等 → 返回已有实例 | 新子 namespace |
| 会话结束 | **不 deactivate**（保持热缓存） | 数据持久化到 L4 |
| 进程退出 / 手动停用 | 停止 ContextService + owned memory | 不影响已持久化数据 |

---

## 四、Memory Flush（压缩前保护）

执行 FullLLMCompact 前，将即将压缩的对话推给 L1 提取 insights / L2 提取 facts，防信息丢失。参考 DeerFlow 的 `MemoryMiddleware` 实现。

详见 [A2-feature-reference.md](A2-feature-reference.md) §四。

## Files

`memory/service.py`（MemoryLayerService 基类）、`memory/facts.py`（GlobalFactsService）、`memory/commands.py`（NotifyIndexUpdateCommand）
