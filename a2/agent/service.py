"""AgentService — ReAct loop orchestrator."""

from __future__ import annotations

import logging
import time

from a2.kernel import A2Service, relay

logger = logging.getLogger(__name__)


class AgentService(A2Service):
    domain = 'agent'
    commands = ['commands', 'tools']

    model_ref: str = 'model.chat'
    context_ref: str = 'context.default'
    tool_ref: str = 'tool.toolkit'
    session_ref: str = 'session.store'
    plan_ref: str = 'plan.notebook'
    knowledge_ref: str = ''
    skill_ref: str = ''
    memory_ref: str = ''
    system_prompt: str = 'You are a helpful AI assistant.'
    tool_groups: list = ['basic']
    max_iters: int = 20
    max_depth: int = 3
    rag_mode: str = 'off'
    rag_top_k: int = 5
    rag_collection: str = 'default'
    rag_score_threshold: float = 0.0
    skill_mode: str = 'off'
    max_skills: int = 8
    memory_mode: str = 'off'
    memory_top_k: int = 5
    plan_mode: str = 'off'
    inject_runtime_state: bool = True

    def system_prompt_of(self, session_id: str) -> str:
        return self.system_prompt

    def last_user_text(self, history: list, inputs: list) -> str:
        """The user message a retrieval step should key on."""
        for message in reversed([*(history or []), *(inputs or [])]):
            if not isinstance(message, dict) or message.get('role') != 'user':
                continue
            content = message.get('content', '')
            if isinstance(content, list):
                content = ' '.join(
                    block.get('text', '') for block in content if isinstance(block, dict)
                )
            if content:
                return str(content)
        return ''

    async def collect_sources(self, *, session_id: str, user_id: str,
                              query: str) -> dict:
        """Gather RAG hits, long-term memories, skill catalog and plan state.

        Each source is opt-in through its own mode flag, so a service that
        configures none of them assembles exactly the messages it did before.
        A source that fails degrades to empty rather than failing the turn.
        """
        return {
            'rag_hits': await self._rag_hits(query),
            'memories': await self._memories(session_id, user_id, query),
            'skills': await self._skills(),
            'plan': await self._plan(session_id),
        }

    async def _rag_hits(self, query: str) -> list:
        if self.rag_mode != 'on' or not self.knowledge_ref or not query:
            return []
        try:
            hits = await relay(self.resolve_ref(
                self.knowledge_ref, 'Search', collection=self.rag_collection,
                query=query, top_k=self.rag_top_k,
                score_threshold=self.rag_score_threshold,
            ))
        except Exception:
            logger.exception('knowledge search failed')
            return []
        return [{
            **hit,
            'source': hit.get('source')
            or (hit.get('metadata') or {}).get('title')
            or hit.get('doc_id', ''),
        } for hit in hits]

    async def _memories(self, session_id: str, user_id: str, query: str) -> list:
        if self.memory_mode == 'off' or not self.memory_ref:
            return []
        scope, subject = ('user', user_id) if user_id else ('session', session_id)
        try:
            return await relay(self.resolve_ref(
                self.memory_ref, 'Recall', scope=scope, subject=subject,
                query=query, top_k=self.memory_top_k,
            ))
        except Exception:
            logger.exception('memory recall failed')
            return []

    async def _skills(self) -> list:
        if self.skill_mode == 'off' or not self.skill_ref:
            return []
        try:
            catalog = await relay(self.resolve_ref(
                self.skill_ref, 'ListSkills', enabled_only=True,
            ))
        except Exception:
            logger.exception('skill catalog failed')
            return []
        return catalog[: self.max_skills]

    async def _plan(self, session_id: str) -> dict:
        if self.plan_mode == 'off' or not self.plan_ref:
            return {}
        try:
            plan = await relay(self.resolve_ref(
                self.plan_ref, 'ListTasks', session_id=session_id,
            ))
        except Exception:
            logger.exception('plan lookup failed')
            return {}
        return plan if plan.get('tasks') else {}

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
