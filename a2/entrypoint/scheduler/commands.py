"""Scheduler commands."""

from __future__ import annotations

import time
import uuid

from bollydog.globals import app
from bollydog.models.base import BaseCommand, BaseEvent


class RegisterJob(BaseCommand):
    """Register a scheduled job."""

    job_destination: str = ''
    args: dict = {}
    interval: int = 3600

    async def __call__(self) -> str:
        job_id = uuid.uuid4().hex[:12]
        jobs = await app.protocol.get('jobs') or []
        jobs.append({
            'id': job_id,
            'destination': self.job_destination,
            'args': self.args,
            'interval': self.interval,
            'next_run': time.time(),
        })
        await app.protocol.set('jobs', jobs)
        return job_id


class ListJobs(BaseCommand):
    """List scheduled jobs."""

    async def __call__(self) -> list:
        return await app.protocol.get('jobs') or []


class JobDispatched(BaseEvent):
    job_id: str = ''
    job_destination: str = ''
