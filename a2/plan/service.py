"""PlanService — task notebook."""

from __future__ import annotations

from typing import ClassVar

from a2.kernel import A2Service


class PlanService(A2Service):
    domain = 'plan'
    commands = ['commands']
    emits: ClassVar[list[str]] = ['PlanCreated', 'TaskUpdated', 'PlanCompleted']

    def normalize(self, tasks: list) -> list:
        result = []
        for i, task in enumerate(tasks):
            if isinstance(task, dict):
                task.setdefault('task_id', f'task_{i + 1}')
                task.setdefault('state', 'pending')
            result.append(task)
        return result

    def apply(self, plan: dict, task_id: str, state: str, note: str) -> dict:
        tasks = plan.get('tasks', [])
        for task in tasks:
            if task.get('task_id') == task_id:
                task['state'] = state
                if note:
                    task['note'] = note
        return plan

    def render(self, tasks: list) -> str:
        lines = ['Task list:']
        for task in tasks:
            mark = {'pending': '[ ]', 'in_progress': '[~]', 'completed': '[x]'}.get(
                task.get('state', 'pending'), '[ ]'
            )
            lines.append(f'{mark} {task.get("subject", task.get("task_id", ""))}')
        return '\n'.join(lines)

    def progress(self, plan: dict) -> dict:
        tasks = plan.get('tasks', [])
        total = len(tasks)
        done = sum(1 for t in tasks if t.get('state') == 'completed')
        return {'total': total, 'done': done, 'pending': total - done}

    def next_pending(self, tasks: list) -> dict | None:
        for task in tasks:
            if task.get('state') == 'pending':
                return task
        return None
