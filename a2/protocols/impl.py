"""Test/production protocol implementations."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import AsyncIterator

from a2.protocols.model import ChatModelProtocol, EmbeddingProtocol
from a2.protocols.permission import PermissionProtocol
from a2.protocols.sandbox import SandboxProtocol
from a2.protocols.vector import VectorStoreProtocol


class ScriptedChatProtocol(ChatModelProtocol):
    """Deterministic chat model for tests — replays scripted responses."""

    scripts: dict = {}
    default_response: str = 'Hello from A2.'

    async def stream(self, payload: dict, model: str) -> AsyncIterator[dict]:
        result = await self.complete(payload, model)
        text = result.get('content', [{}])[0].get('text', '')
        for i, ch in enumerate(text):
            yield {
                'type': 'model.delta',
                'payload': {'block': 'text', 'index': 0, 'text': ch},
            }
        yield {'type': 'model.completed', 'payload': result}

    async def complete(self, payload: dict, model: str) -> dict:
        messages = payload.get('messages', [])
        tools = payload.get('tools', [])
        key = self._script_key(messages)
        script = self.scripts.get(key) or self.scripts.get('*')
        if script:
            return script(messages, tools)
        last = messages[-1] if messages else {}
        user_text = last.get('content', '') if isinstance(last, dict) else ''
        if tools and 'read' in user_text.lower():
            return {
                'content': [
                    {
                        'type': 'tool_call',
                        'id': 'call_1',
                        'name': 'ReadPath',
                        'input': {'path': 'README.md'},
                    }
                ],
                'finish_reason': 'tool_calls',
                'usage': {'input_tokens': 10, 'output_tokens': 5},
            }
        return {
            'content': [{'type': 'text', 'text': self.default_response}],
            'finish_reason': 'stop',
            'usage': {'input_tokens': 10, 'output_tokens': len(self.default_response) // 4},
        }

    def _script_key(self, messages: list) -> str:
        if not messages:
            return '*'
        last = messages[-1]
        return str(last.get('content', ''))[:200]

    async def count_tokens(self, messages: list, model: str) -> int:
        return sum(max(1, len(str(m.get('content', ''))) // 4) for m in messages)


class HashEmbeddingProtocol(EmbeddingProtocol):
    """Deterministic pseudo-embeddings for tests."""

    dimension: int = 16

    async def embed(self, texts: list, model: str) -> list:
        result = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            vec = []
            for i in range(self.dimension):
                vec.append((digest[i % len(digest)] - 128) / 128.0)
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            result.append([v / norm for v in vec])
        return result


class InMemoryVectorProtocol(VectorStoreProtocol):
    """In-memory vector store for tests."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._store: dict[str, list] = {}

    async def upsert(self, collection: str, items: list) -> int:
        bucket = self._store.setdefault(collection, [])
        bucket.extend(items)
        return len(items)

    async def search(
        self, collection: str, vector: list, top_k: int, flt: dict | None = None
    ) -> list:
        bucket = self._store.get(collection, [])
        scored = []
        for item in bucket:
            iv = item.get('vector', [])
            score = _cosine(vector, iv) if iv else 0.0
            scored.append({**item, 'score': score})
        scored.sort(key=lambda x: x['score'], reverse=True)
        return scored[:top_k]

    async def delete(self, collection: str, doc_id: str) -> int:
        bucket = self._store.get(collection, [])
        before = len(bucket)
        self._store[collection] = [x for x in bucket if x.get('doc_id') != doc_id]
        return before - len(self._store[collection])


def _cosine(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


class LocalSandboxProtocol(SandboxProtocol):
    """Local filesystem sandbox."""

    root: str = '.a2/workspace'

    def __init__(self, root: str = '', **kwargs):
        super().__init__(**kwargs)
        if root:
            self.root = root

    def _resolve(self, path: str) -> Path:
        base = Path(self.root).resolve()
        target = (base / path).resolve()
        if not str(target).startswith(str(base)):
            raise PermissionError(f'path escapes sandbox: {path}')
        return target

    async def exec_stream(
        self, command: str, cwd: str, env: dict, timeout: int
    ) -> AsyncIterator[dict]:
        workdir = str(self._resolve(cwd or '.'))
        proc = await __import__('asyncio').create_subprocess_shell(
            command,
            stdout=__import__('asyncio').subprocess.PIPE,
            stderr=__import__('asyncio').subprocess.PIPE,
            cwd=workdir,
            env={**os.environ, **env},
        )
        stdout, stderr = await __import__('asyncio').wait_for(
            proc.communicate(), timeout=timeout
        )
        if stdout:
            yield {'stream': 'stdout', 'text': stdout.decode(errors='replace')}
        if stderr:
            yield {'stream': 'stderr', 'text': stderr.decode(errors='replace')}
        yield {'exit_code': proc.returncode or 0}

    async def put(self, path: str, content: bytes) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    async def fetch(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()


class RulePermissionProtocol(PermissionProtocol):
    """Rule-based permission engine."""

    rules: list = []

    def __init__(self, rules: list | None = None, **kwargs):
        super().__init__(**kwargs)
        self.rules = rules or []

    async def match(self, tool: str, args: dict, ctx: dict) -> dict:
        for rule in self.rules:
            pattern = rule.get('pattern', '*')
            if not _match_pattern(pattern, tool):
                continue
            when = rule.get('when', {})
            if when and not _match_when(when, args):
                continue
            return {
                'action': rule.get('action', 'allow'),
                'reason': rule.get('reason', ''),
            }
        return {'action': 'allow', 'reason': ''}

    async def add_rule(self, rule: dict) -> int:
        self.rules.append(rule)
        return len(self.rules)

    async def rules(self) -> list:
        return list(self.rules)


def _match_pattern(pattern: str, tool: str) -> bool:
    regex = '^' + pattern.replace('.', r'\.').replace('*', '.*') + '$'
    return bool(re.match(regex, tool))


def _match_when(when: dict, args: dict) -> bool:
    for key, pattern in when.items():
        value = str(args.get(key, ''))
        if not re.search(pattern, value):
            return False
    return True
