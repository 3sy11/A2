"""AgentService — ReAct loop orchestrator."""

from __future__ import annotations

import time

from a2.kernel import A2Service


class AgentService(A2Service):
    domain = 'agent'
    commands = ['commands', 'tools']

    model_ref: str = 'model.chat'
    context_ref: str = 'context.default'
    tool_ref: str = 'tool.toolkit'
    session_ref: str = 'session.store'
    plan_ref: str = 'plan.notebook'
    knowledge_ref: str = ''
    system_prompt: str = 'You are a helpful AI assistant.'
    tool_groups: list = ['basic']
    max_iters: int = 20
    max_depth: int = 3
    rag_mode: str = 'off'
    inject_runtime_state: bool = True

    def system_prompt_of(self, session_id: str) -> str:
        return self.system_prompt

    def next_action(self, completed: dict, iter_: int) -> str:
        content = completed.get('content', [])
        for block in content:
            if block.get('type') == 'tool_call':
                return 'act'
        if content:
            return 'exit'
        return 'exit'

    def extract_tool_calls(self, completed: dict) -> list:
        calls = []
        for block in completed.get('content', []):
            if block.get('type') == 'tool_call':
                calls.append(block)
        return calls

    def batch_calls(self, calls: list) -> list:
        safe = []
        unsafe = []
        for call in calls:
            name = call.get('name', '')
            try:
                from bollydog.globals import registry
                tool_svc = self.get_dependency(self.tool_ref)
                if tool_svc.is_concurrency_safe(name):
                    safe.append(call)
                else:
                    unsafe.append(call)
            except Exception:
                safe.append(call)
        batches = []
        if safe:
            batches.append(safe)
        for call in unsafe:
            batches.append([call])
        return batches or [calls]

    def build_hint(self, state: dict) -> str:
        parts = []
        if state.get('iter'):
            parts.append(f'Iteration {state["iter"]}/{self.max_iters}')
        if state.get('tokens'):
            parts.append(f'Context: {state["tokens"]} tokens')
        return '\n'.join(parts)

    def pick_final(self, chunks: list) -> list:
        for block in reversed(chunks):
            if block.get('type') == 'text':
                return [block]
        return chunks

    def to_msg(self, content: list, role: str = 'assistant') -> dict:
        return {
            'name': self.alias,
            'role': role,
            'content': content,
        }
