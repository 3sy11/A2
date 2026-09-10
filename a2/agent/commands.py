"""Agent domain commands — ReAct loop."""

from __future__ import annotations

import json
import time
import uuid

from bollydog.globals import app, hub, registry, session
from bollydog.models.base import BaseCommand, BaseEvent

from a2.kernel import chunk, relay_gen, should_stop, uid


class Reply(BaseCommand):
    """Drive one complete agent turn (ReAct loop)."""

    session_id: str = ''
    inputs: list = []
    structured_schema: dict | None = None
    resume_state: dict | None = None
    max_iters: int = 0

    async def __call__(self):
        svc = app
        turn_id = uid('turn_')
        agent_name = svc.alias
        max_iters = self.max_iters or svc.max_iters
        start = time.time()

        await session.set(f'turn:{self.session_id}', {'interrupted': False, 'turn_id': turn_id})

        yield await chunk(
            'reply.started',
            session_id=self.session_id,
            turn_id=turn_id,
            agent=agent_name,
            payload={'inputs_digest': str(len(self.inputs))},
        )
        event = svc.event(
            'ReplyStarted',
            session_id=self.session_id,
            turn_id=turn_id,
            agent=agent_name,
        )
        await hub.emit(topic=type(event).destination, source=event)

        resume = self.resume_state
        iteration = resume.get('iter', 0) if resume else 0
        accumulated_content: list = []
        total_usage: dict = {}

        while iteration < max_iters:
            if await should_stop(self.session_id):
                yield await chunk(
                    'reply.interrupted',
                    session_id=self.session_id,
                    turn_id=turn_id,
                    agent=agent_name,
                    payload={'reason': 'user interrupt'},
                )
                event = svc.event(
                    'ReplyInterrupted',
                    session_id=self.session_id,
                    turn_id=turn_id,
                    agent=agent_name,
                    reason='user interrupt',
                )
                await hub.emit(topic=type(event).destination, source=event)
                return

            assembled = yield svc.resolve_ref(
                svc.context_ref, 'Assemble',
                session_id=self.session_id,
                agent=agent_name,
                inputs=self.inputs if iteration == 0 and not resume else [],
                system_prompt=svc.system_prompt_of(self.session_id),
                tool_names=[],
                budget=0,
                rag_query='',
            )

            if assembled.get('need_compress'):
                yield await chunk('context.compacting', session_id=self.session_id, payload={'tokens': assembled['tokens']})
                compressed = yield svc.resolve_ref(
                    svc.context_ref, 'Compress',
                    session_id=self.session_id,
                    agent=agent_name,
                    keep_ratio=0.0,
                    model_ref=svc.model_ref,
                )
                yield await chunk(
                    'context.compacted',
                    session_id=self.session_id,
                    payload=compressed,
                )
                assembled = yield svc.resolve_ref(
                    svc.context_ref, 'Assemble',
                    session_id=self.session_id,
                    agent=agent_name,
                    inputs=[],
                    system_prompt=svc.system_prompt_of(self.session_id),
                    tool_names=[],
                    budget=0,
                    rag_query='',
                )

            tools = yield svc.resolve_ref(
                svc.tool_ref, 'ListTools',
                session_id=self.session_id,
                agent=agent_name,
                groups=svc.tool_groups,
            )

            tool_schemas = [
                {
                    'type': 'function',
                    'function': {
                        'name': t['name'],
                        'description': t.get('description', ''),
                        'parameters': t.get('parameters', {}),
                    },
                }
                for t in tools
            ]

            gen_cmd = svc.resolve_ref(
                svc.model_ref, 'Generate',
                messages=assembled['messages'],
                tools=tool_schemas,
                tool_choice='auto',
                stream=True,
                model='',
                params={},
            )

            completed = None
            async for evt in relay_gen(gen_cmd):
                evt_type = evt.get('type', '')
                if evt_type == 'model.delta':
                    yield await chunk('model.delta', session_id=self.session_id, turn_id=turn_id, agent=agent_name, payload=evt.get('payload', evt))
                elif evt_type == 'model.completed':
                    completed = evt.get('payload', evt)
                    yield await chunk('model.completed', session_id=self.session_id, turn_id=turn_id, agent=agent_name, payload=completed)
                elif evt_type == 'model.failed':
                    yield await chunk('error', session_id=self.session_id, payload={'code': 'model_failed', 'message': evt.get('payload', {}).get('error', ''), 'retryable': True})
                    ms = int((time.time() - start) * 1000)
                    event = svc.event(
                        'ReplyFinished',
                        session_id=self.session_id,
                        turn_id=turn_id,
                        agent=agent_name,
                        content=accumulated_content,
                        usage=total_usage,
                        finish_reason='error',
                        ms=ms,
                        inputs=self.inputs,
                    )
                    await hub.emit(topic=type(event).destination, source=event)
                    return

            if not completed:
                completed = {'content': [], 'finish_reason': 'stop', 'usage': {}}

            usage = completed.get('usage', {})
            for k, v in usage.items():
                if isinstance(v, (int, float)):
                    total_usage[k] = total_usage.get(k, 0) + v

            action = svc.next_action(completed, iteration)
            if action == 'exit':
                accumulated_content = completed.get('content', [])
                break

            tool_calls = svc.extract_tool_calls(completed)
            assistant_msg = svc.to_msg(completed.get('content', []))
            yield svc.resolve_ref(
                svc.context_ref, 'AppendContext',
                session_id=self.session_id,
                messages=[assistant_msg],
            )

            for batch in svc.batch_calls(tool_calls):
                if await should_stop(self.session_id):
                    break
                if len(batch) > 1:
                    results = []
                    cmds = []
                    for call in batch:
                        call_id = call.get('id', uid('call_'))
                        yield await chunk(
                            'tool.call',
                            session_id=self.session_id,
                            turn_id=turn_id,
                            agent=agent_name,
                            payload={'id': call_id, 'name': call.get('name', ''), 'input': call.get('input', {})},
                        )
                        cmds.append(
                            svc.resolve_ref(
                                svc.tool_ref, 'Invoke',
                                call_id=call_id,
                                tool=call.get('name', ''),
                                args=call.get('input', {}),
                                session_id=self.session_id,
                                agent=agent_name,
                            )
                        )
                    parallel_results = yield cmds
                    for call, result in zip(batch, parallel_results):
                        results.append((call, result))
                    for call, result in results:
                        if isinstance(result, dict) and result.get('type') == 'tool.result':
                            yield await chunk('tool.result', session_id=self.session_id, payload=result.get('payload', result))
                            tool_msg = _tool_result_msg(call, result.get('payload', result))
                            yield svc.resolve_ref(
                                svc.context_ref, 'AppendContext',
                                session_id=self.session_id,
                                messages=[tool_msg],
                            )
                else:
                    call = batch[0]
                    call_id = call.get('id', uid('call_'))
                    yield await chunk(
                        'tool.call',
                        session_id=self.session_id,
                        turn_id=turn_id,
                        agent=agent_name,
                        payload={'id': call_id, 'name': call.get('name', ''), 'input': call.get('input', {})},
                    )
                    invoke = svc.resolve_ref(
                        svc.tool_ref, 'Invoke',
                        call_id=call_id,
                        tool=call.get('name', ''),
                        args=call.get('input', {}),
                        session_id=self.session_id,
                        agent=agent_name,
                    )
                    invoke.data['turn_id'] = turn_id
                    async for evt in relay_gen(invoke):
                        if evt.get('type', '').startswith('tool.'):
                            yield await chunk(evt.get('type', 'tool.chunk'), session_id=self.session_id, turn_id=turn_id, agent=agent_name, payload=evt.get('payload', evt))
                    result_payload = invoke.state.result()
                    tool_msg = _tool_result_msg(call, result_payload.get('payload', result_payload) if isinstance(result_payload, dict) else {'output': result_payload})
                    yield svc.resolve_ref(
                        svc.context_ref, 'AppendContext',
                        session_id=self.session_id,
                        messages=[tool_msg],
                    )

            iteration += 1
            event = svc.event(
                'IterationCompleted',
                session_id=self.session_id,
                turn_id=turn_id,
                agent=agent_name,
                iter=iteration,
            )
            await hub.emit(topic=type(event).destination, source=event)
            yield await chunk(
                'iteration.completed',
                session_id=self.session_id,
                turn_id=turn_id,
                agent=agent_name,
                payload={'iter': iteration},
            )
            resume = None

        finish_reason = 'stop' if accumulated_content else 'max_iters'
        ms = int((time.time() - start) * 1000)
        yield await chunk(
            'reply.finished',
            session_id=self.session_id,
            turn_id=turn_id,
            agent=agent_name,
            payload={
                'content': accumulated_content,
                'finish_reason': finish_reason,
                'usage': total_usage,
            },
        )
        event = svc.event(
            'ReplyFinished',
            session_id=self.session_id,
            turn_id=turn_id,
            agent=agent_name,
            content=accumulated_content,
            usage=total_usage,
            finish_reason=finish_reason,
            ms=ms,
            inputs=self.inputs,
        )
        await hub.emit(topic=type(event).destination, source=event)


class Resume(BaseCommand):
    """Resume a parked turn."""

    session_id: str = ''
    park_id: str = ''
    decision: str = ''
    answer: str = ''

    async def __call__(self):
        state = yield app.resolve_ref(
            app.session_ref, 'Unpark',
            session_id=self.session_id,
            park_id=self.park_id,
        )
        resume_state = {
            'iter': state.get('pending', {}).get('iter', 0),
            'park_kind': state.get('kind', ''),
            'decision': self.decision,
            'answer': self.answer,
        }
        agent_ref = f'agent.{app.alias}'
        reply = registry.resolve(f'{agent_ref}.Reply')(
            session_id=self.session_id,
            inputs=[{'role': 'user', 'content': self.answer or self.decision}],
            structured_schema=None,
            resume_state=resume_state,
            max_iters=0,
        )
        async for chunk_evt in relay_gen(reply):
            yield chunk_evt


class Interrupt(BaseCommand):
    """Cooperative interrupt — mark turn as stopped."""

    session_id: str = ''
    reason: str = 'user'

    async def __call__(self) -> dict:
        await session.set(f'turn:{self.session_id}', {'interrupted': True, 'reason': self.reason})
        return {'interrupted': True, 'reason': self.reason}


class Observe(BaseCommand):
    """Append messages to context without replying."""

    session_id: str = ''
    inputs: list = []
    sender: str = ''

    async def __call__(self) -> int:
        cmd = app.resolve_ref(
            app.context_ref, 'AppendContext',
            session_id=self.session_id,
            messages=self.inputs,
        )
        await hub.dispatch(cmd)
        return await cmd.state


class Spawn(BaseCommand):
    """Delegate to a sub-agent."""

    agent: str = ''
    task: str = ''
    session_id: str = ''
    inherit_context: bool = False

    async def __call__(self):
        dest = f'agent.{self.agent}.Reply'
        sub = registry.resolve(dest)(
            session_id=self.session_id,
            inputs=[{'role': 'user', 'content': self.task}],
            structured_schema=None,
            resume_state=None,
            max_iters=0,
        )
        async for evt in relay_gen(sub):
            yield await chunk('subagent.chunk', session_id=self.session_id, payload={'agent': self.agent, 'data': evt})
        result = sub.state.result()
        yield await chunk('subagent.result', session_id=self.session_id, payload={'agent': self.agent, 'content': result})


class OnMessageBroadcast(BaseEvent):
    """Append a team broadcast to this agent's context."""

    async def __call__(self) -> dict:
        source = self.data.get('events', [{}])[-1]
        ctx_key = f'context:{source.get("topic", "")}'
        data = await session.get(ctx_key)
        history = data.get('items', []) if isinstance(data, dict) else []
        history.append({
            'role': 'user',
            'content': json.dumps(source.get('content', [])),
            'name': source.get('sender', 'team'),
        })
        await session.set(ctx_key, {'items': history})
        return {'ok': True}


class ReplyStarted(BaseEvent):
    session_id: str = ''
    turn_id: str = ''
    agent: str = ''


class IterationCompleted(BaseEvent):
    session_id: str = ''
    turn_id: str = ''
    agent: str = ''
    iter: int = 0


class ReplyFinished(BaseEvent):
    session_id: str = ''
    turn_id: str = ''
    agent: str = ''
    content: list = []
    usage: dict = {}
    finish_reason: str = ''
    ms: int = 0
    inputs: list = []


class ReplyInterrupted(BaseEvent):
    session_id: str = ''
    turn_id: str = ''
    agent: str = ''
    reason: str = ''


class ReplyParked(BaseEvent):
    session_id: str = ''
    turn_id: str = ''
    agent: str = ''
    park_id: str = ''
    kind: str = ''


def _tool_result_msg(call: dict, result: dict) -> dict:
    output = result.get('output', result)
    text = json.dumps(output, ensure_ascii=False) if isinstance(output, dict) else str(output)
    return {
        'role': 'tool',
        'content': text,
        'metadata': {'tool_call_id': call.get('id', ''), 'name': call.get('name', '')},
    }
