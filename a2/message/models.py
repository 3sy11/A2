"""Message and content block domain models."""

from __future__ import annotations

from typing import Any

from bollydog.models.base import BaseDomain


class Block(BaseDomain):
    type: str


class TextBlock(Block):
    type: str = 'text'
    text: str = ''


class ThinkingBlock(Block):
    type: str = 'thinking'
    text: str = ''
    signature: str = ''


class DataBlock(Block):
    type: str = 'data'
    media_type: str = 'text/plain'
    url: str = ''
    base64: str = ''


class ToolCallBlock(Block):
    type: str = 'tool_call'
    id: str = ''
    name: str = ''
    input: dict = {}
    state: str = 'pending'


class ToolResultBlock(Block):
    type: str = 'tool_result'
    id: str = ''
    name: str = ''
    output: list = []
    status: str = 'ok'
    artifact: str = ''


class HintBlock(Block):
    type: str = 'hint'
    text: str = ''
    source: str = 'runtime'


class Usage(BaseDomain):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0


class Msg(BaseDomain):
    name: str = ''
    role: str = 'user'
    content: list = []
    metadata: dict = {}
    usage: dict = {}
    finish_reason: str = ''
    structured_output: dict = {}
    error: dict = {}

    def text(self) -> str:
        parts = []
        for block in self.content:
            if isinstance(block, dict):
                if block.get('type') == 'text':
                    parts.append(block.get('text', ''))
            elif getattr(block, 'type', None) == 'text':
                parts.append(getattr(block, 'text', ''))
        return ''.join(parts)

    def blocks(self, type_: str) -> list[dict]:
        result = []
        for block in self.content:
            data = block if isinstance(block, dict) else block.model_dump()
            if data.get('type') == type_:
                result.append(data)
        return result

    @classmethod
    def user(cls, text: str, name: str = 'user') -> Msg:
        return cls(name=name, role='user', content=[TextBlock(text=text).model_dump()])

    @classmethod
    def assistant(cls, text: str, name: str = 'assistant') -> Msg:
        return cls(name=name, role='assistant', content=[TextBlock(text=text).model_dump()])

    @classmethod
    def system(cls, text: str) -> Msg:
        return cls(name='system', role='system', content=[TextBlock(text=text).model_dump()])

    def to_openai(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible message dict."""
        if self.role == 'tool':
            return {
                'role': 'tool',
                'tool_call_id': self.metadata.get('tool_call_id', ''),
                'content': self.text(),
            }
        content = self.text()
        tool_calls = self.blocks('tool_call')
        msg: dict[str, Any] = {'role': self.role, 'content': content or None}
        if self.name and self.role == 'assistant':
            msg['name'] = self.name
        if tool_calls:
            msg['tool_calls'] = [
                {
                    'id': tc['id'],
                    'type': 'function',
                    'function': {
                        'name': tc['name'],
                        'arguments': __import__('json').dumps(tc.get('input', {})),
                    },
                }
                for tc in tool_calls
            ]
        return msg
