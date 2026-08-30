# 模块：LLMService

> domain=`llm` | 单一实例 | Protocol: LiteLLMProvider

---

## 架构

```
LLMService(AppService) ← 单一实例, domain="llm"
└── protocol: LiteLLMProvider(Protocol) → adapter = litellm.Router
```

模型选择由调用方决定（`RoleDef.model` / `Tool.required_model`），LLMService 只负责转发。

## LiteLLMProvider(Protocol)

litellm.Router 是天然的"单 adapter 管多后端"，Router 本身就是 adapter。

```python
class LiteLLMProvider(Protocol):
    default_model: str = 'anthropic/claude-sonnet-4-20250514'
    models: dict = {}

    async def on_start(self):
        model_list = []
        for alias, conf in self.models.items():
            model_id = conf.get('model', alias)
            model_list.append({'model_name': model_id, 'litellm_params': {'model': model_id, **conf}})
        if not model_list:
            model_list = [{'model_name': self.default_model, 'litellm_params': {'model': self.default_model}}]
        self.adapter = litellm.Router(model_list=model_list)
        # NOTE: litellm.Router 原生支持 fallbacks 参数，后续可按需开启

    async def chat(self, messages, model=None, tools=None, **kw) -> 'LLMResponse':
        return await self.adapter.acompletion(model=model or self.default_model, messages=messages, tools=tools, **kw)

    async def count_tokens(self, model: str, messages: list[dict]) -> int:
        return litellm.token_counter(model=model, messages=messages)
```

## LLMService

```python
class LLMService(AppService):
    depends = []
    async def chat(self, messages, model=None, tools=None, **kw):
        return await self.protocol.chat(messages, model=model, tools=tools, **kw)
    async def count_tokens(self, messages, model=None) -> int:
        return await self.protocol.count_tokens(model or self.protocol.default_model, messages)
```

## 配置 → llm/config.py

```python
# a2/llm/config.py
LLM_DEFAULT = {
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
}
```

TOML 覆盖示例：`[llm.LLMService.protocol]` 下修改 `default_model` 或追加 `models`。

## Files

`llm/config.py`（LLM_DEFAULT）、`llm/service.py`（LLMService）、`llm/provider.py`（LiteLLMProvider）
