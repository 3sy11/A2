"""Plan domain commands."""

from __future__ import annotations

import uuid

from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand, BaseEvent


class CreatePlan(BaseCommand):
    """Create a task plan."""

    session_id: str = ''
    goal: str = ''
    tasks: list = []

    async def __call__(self) -> dict:
        plan_id = uuid.uuid4().hex[:12]
        plan = {
            'plan_id': plan_id,
            'session_id': self.session_id,
            'goal': self.goal,
            'tasks': app.normalize(self.tasks),
        }
        await app.protocol.set(f'plan:{self.session_id}', plan)
        event = app.event(
            'PlanCreated',
            session_id=self.session_id,
            plan_id=plan_id,
            count=len(plan['tasks']),
        )
        await hub.emit(topic=type(event).destination, source=event)
        return plan


class UpdateTask(BaseCommand):
    """Update task state."""

    session_id: str = ''
    task_id: str = ''
    state: str = ''
    note: str = ''

    async def __call__(self) -> dict:
        plan = await app.protocol.get(f'plan:{self.session_id}') or {'tasks': []}
        plan = app.apply(plan, self.task_id, self.state, self.note)
        await app.protocol.set(f'plan:{self.session_id}', plan)
        event = app.event(
            'TaskUpdated',
            session_id=self.session_id,
            task_id=self.task_id,
            state=self.state,
        )
        await hub.emit(topic=type(event).destination, source=event)
        progress = app.progress(plan)
        if progress['pending'] == 0 and progress['total'] > 0:
            event = app.event(
                'PlanCompleted',
                session_id=self.session_id,
                plan_id=plan.get('plan_id', ''),
            )
            await hub.emit(topic=type(event).destination, source=event)
        return plan


class ListTasks(BaseCommand):
    """List current plan tasks."""

    session_id: str = ''

    async def __call__(self) -> dict:
        plan = await app.protocol.get(f'plan:{self.session_id}') or {'tasks': []}
        return {**plan, 'progress': app.progress(plan), 'rendered': app.render(plan.get('tasks', []))}


class PlanCreated(BaseEvent):
    session_id: str = ''
    plan_id: str = ''
    count: int = 0


class TaskUpdated(BaseEvent):
    session_id: str = ''
    task_id: str = ''
    state: str = ''


class PlanCompleted(BaseEvent):
    session_id: str = ''
    plan_id: str = ''
