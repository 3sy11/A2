"""Plan domain models."""

from bollydog.models.base import BaseDomain


class Task(BaseDomain):
    task_id: str = ''
    subject: str = ''
    description: str = ''
    state: str = 'pending'
    owner: str = ''
    blocked_by: list = []
    note: str = ''


class Plan(BaseDomain):
    plan_id: str = ''
    session_id: str = ''
    goal: str = ''
    tasks: list = []
