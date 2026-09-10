"""SchedulerService — cron-like entrypoint (D-08)."""

from __future__ import annotations

import time

import mode

from bollydog.globals import hub, registry
from bollydog.models.base import BaseCommand

from a2.kernel import A2Service


class SchedulerService(A2Service):
    domain = 'schedule'
    commands = ['commands']
    alias = 'runner'

    poll_interval: float = 30.0

    @mode.Service.task
    async def _poll_jobs(self):
        while not self.should_stop:
            await self._tick()
            await self.sleep(self.poll_interval)

    async def _tick(self):
        if not self.protocol:
            return
        jobs = await self.protocol.get('jobs') or []
        now = time.time()
        for job in jobs:
            if job.get('next_run', 0) <= now:
                cmd = registry.resolve(job['destination'])(**job.get('args', {}))
                await hub.dispatch(cmd)
                event = self.event(
                    'JobDispatched',
                    job_id=job.get('id', ''),
                    destination=job['destination'],
                )
                await hub.emit(topic=type(event).destination, source=event)
                job['next_run'] = now + job.get('interval', 3600)
        await self.protocol.set('jobs', jobs)
