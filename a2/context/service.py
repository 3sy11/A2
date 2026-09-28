"""ContextService — message assembly and compression."""

from __future__ import annotations

from a2.kernel import A2Service


class ContextService(A2Service):
    domain = 'context'
    commands = ['commands']

    budget: int = 128000
    trigger_ratio: float = 0.8
    keep_ratio: float = 0.3
    tool_result_limit: int = 8000
    max_images: int = 5
    compression_prompt_template: str = (
        'Summarize the following conversation history concisely, '
        'preserving key facts, decisions, and tool results.'
    )

    def assemble(self, parts: dict) -> list:
        messages = []
        system = parts.get('system_prompt', '')
        if system:
            messages.append({'role': 'system', 'content': system})
        summary = parts.get('summary', '')
        if summary:
            messages.append({'role': 'system', 'content': f'Previous summary:\n{summary}'})
        for render, key in (
            (self.render_skills, 'skills'),
            (self.render_memories, 'memories'),
            (self.render_plan, 'plan'),
            (self.render_hits, 'rag_hits'),
        ):
            text = render(parts.get(key))
            if text:
                messages.append({'role': 'system', 'content': text})
        for hint in parts.get('hints', []):
            messages.append({'role': 'system', 'content': hint})
        messages.extend(parts.get('history', []))
        messages.extend(parts.get('inputs', []))
        return messages

    def need_compress(self, tokens: int) -> bool:
        return tokens > int(self.budget * self.trigger_ratio)

    def split_window(self, msgs: list, keep_ratio: float | None = None) -> tuple:
        ratio = keep_ratio if keep_ratio is not None else self.keep_ratio
        keep = max(2, int(len(msgs) * ratio))
        return msgs[:-keep], msgs[-keep:]

    def compression_prompt(self, msgs: list) -> list:
        text = '\n'.join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in msgs
        )
        return [
            {'role': 'system', 'content': self.compression_prompt_template},
            {'role': 'user', 'content': text},
        ]

    def pick_text(self, chunks: list) -> str:
        return '\n'.join(
            c.get('text', '') for c in chunks if isinstance(c, dict)
        )

    def render_hint(self, state: dict) -> str:
        parts = []
        if state.get('time'):
            parts.append(f'Current time: {state["time"]}')
        if state.get('tokens'):
            parts.append(f'Context tokens: {state["tokens"]}/{self.budget}')
        if state.get('tasks'):
            parts.append(f'Active tasks: {state["tasks"]}')
        return '\n'.join(parts)

    def render_hits(self, hits: list) -> str:
        if not hits:
            return ''
        lines = ['Relevant documents:']
        for hit in hits:
            source = hit.get('source', hit.get('doc_id', ''))
            text = hit.get('text', '')[:500]
            lines.append(f'- [{source}] {text}')
        return '\n'.join(lines)

    def render_skills(self, skills: list) -> str:
        """Level-1 disclosure: names and descriptions only, never the body."""
        if not skills:
            return ''
        lines = ['Available skills (call LoadSkill to read one before using it):']
        for skill in skills:
            lines.append(f'- {skill.get("name", "")}: {skill.get("description", "")}')
        return '\n'.join(lines)

    def render_memories(self, memories: list) -> str:
        if not memories:
            return ''
        lines = ['Known facts about the user:']
        for entry in memories:
            text = entry.get('text', '') if isinstance(entry, dict) else str(entry)
            if text:
                lines.append(f'- {text[:500]}')
        return '\n'.join(lines)

    def render_plan(self, plan: dict) -> str:
        if not plan:
            return ''
        rendered = plan.get('rendered', '') if isinstance(plan, dict) else ''
        return f'Current task list:\n{rendered}' if rendered else ''

    def estimate(self, msgs: list) -> int:
        return sum(max(1, len(str(m.get('content', ''))) // 4) for m in msgs)

    def truncate_tool_result(self, text: str) -> tuple:
        if len(text) <= self.tool_result_limit:
            return text, False
        return text[: self.tool_result_limit] + '\n...[truncated]', True
