"""Team domain commands."""

from __future__ import annotations

from bollydog.globals import app, hub, registry
from bollydog.models.base import BaseCommand, BaseEvent

from a2.kernel import chunk, relay_gen


class Sequential(BaseCommand):
    """Run agents sequentially on a topic."""

    topic: str = ''
    agents: list = []
    inputs: list = []

    async def __call__(self):
        current_inputs = self.inputs
        for agent in self.agents:
            dest = f'agent.{agent}.Reply'
            cmd = registry.resolve(dest)(
                session_id=self.topic,
                inputs=current_inputs,
                structured_schema=None,
                resume_state=None,
                max_iters=0,
            )
            async for evt in relay_gen(cmd):
                yield evt
            result = cmd.state.result()
            current_inputs = app.to_inputs([result])
        event = app.event(
            'RoundCompleted',
            topic=self.topic,
            agents=self.agents,
            rounds=1,
        )
        await hub.emit(topic=type(event).destination, source=event)


class Fanout(BaseCommand):
    """Run agents in parallel."""

    topic: str = ''
    agents: list = []
    inputs: list = []

    async def __call__(self):
        cmds = [
            registry.resolve(f'agent.{agent}.Reply')(
                session_id=self.topic,
                inputs=self.inputs,
                structured_schema=None,
                resume_state=None,
                max_iters=0,
            )
            for agent in self.agents
        ]
        results = yield cmds
        zipped = app.zip(self.agents, results)
        yield await chunk('fanout.completed', payload={'results': zipped})
        event = app.event(
            'RoundCompleted',
            topic=self.topic,
            agents=self.agents,
            rounds=1,
        )
        await hub.emit(topic=type(event).destination, source=event)


class Broadcast(BaseCommand):
    """Broadcast message to topic members."""

    topic: str = ''
    sender: str = ''
    content: list = []

    async def __call__(self) -> dict:
        members = await app.members(self.topic)
        event = app.event(
            'MessageBroadcast',
            topic=self.topic,
            sender=self.sender,
            content=self.content,
            members=members,
        )
        await hub.emit(topic=type(event).destination, source=event)
        return {'topic': self.topic, 'members': members}


class JoinTopic(BaseCommand):
    """Join a collaboration topic."""

    topic: str = ''
    agent: str = ''

    async def __call__(self) -> list:
        members = await app.members(self.topic)
        if self.agent not in members:
            members.append(self.agent)
        await app.protocol.set(f'topic:{self.topic}', members)
        return members


class LeaveTopic(BaseCommand):
    """Leave a collaboration topic."""

    topic: str = ''
    agent: str = ''

    async def __call__(self) -> list:
        members = [m for m in await app.members(self.topic) if m != self.agent]
        await app.protocol.set(f'topic:{self.topic}', members)
        return members


class MessageBroadcast(BaseEvent):
    topic: str = ''
    sender: str = ''
    content: list = []
    members: list = []


class RoundCompleted(BaseEvent):
    topic: str = ''
    agents: list = []
    rounds: int = 0
