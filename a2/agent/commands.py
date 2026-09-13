"""Agent commands — durable ReAct conversation turns."""

from __future__ import annotations

import json
import time

from bollydog.globals import app, hub, registry, session
from bollydog.models.base import BaseCommand, BaseEvent

from a2.kernel import chunk, relay, relay_gen, should_stop, uid


async def _write_progress(session_id: str, **changes) -> dict:
    """Persist where the current conversation turn has reached."""
    key = f'turn:{session_id}'
    progress = await session.get(key)
    progress.update(changes)
    progress['updated_at'] = time.time()
    await session.set(key, progress)
    return progress


async def _record_event(svc, type_: str, *, session_id: str, turn_id: str,
                        agent: str, payload: dict) -> dict:
    """Save a client-visible event before returning it to the stream."""
    event = await chunk(type_, session_id=session_id, turn_id=turn_id,
                        agent=agent, payload=payload)
    await relay(svc.resolve_ref(
        svc.session_ref, 'AppendEvent', session_id=session_id,
        turn_id=turn_id, chunk=event,
    ))
    await _write_progress(session_id, last_event_seq=event['seq'])
    return event


async def _append_context(svc, session_id: str, messages: list) -> int:
    return await relay(svc.resolve_ref(
        svc.context_ref, 'AppendContext', session_id=session_id, messages=messages,
    ))


async def _save_completed_turn(svc, *, session_id: str, turn_id: str,
                               agent: str, inputs: list, content: list,
                               usage: dict, finish_reason: str,
                               started_at: float) -> int:
    """Save one completed, interrupted, or failed conversation turn."""
    return await relay(svc.resolve_ref(
        svc.session_ref, 'SaveTurn', session_id=session_id, turn_id=turn_id,
        record={
            'agent': agent, 'inputs': inputs, 'output': content, 'usage': usage,
            'finish_reason': finish_reason,
            'ms': int((time.time() - started_at) * 1000),
            'created_at': started_at,
        },
    ))


def _last_result(value):
    if isinstance(value, list):
        return value[-1] if value else {}
    return value or {}


def _tool_message(call: dict, result: dict) -> dict:
    output = result.get('output', result)
    text = json.dumps(output, ensure_ascii=False) if isinstance(output, dict) else str(output)
    return {
        'role': 'tool', 'content': text,
        'metadata': {'tool_call_id': call.get('id', ''), 'name': call.get('name', '')},
    }


class Reply(BaseCommand):
    """Run one durable agent conversation turn."""

    session_id: str = ''
    inputs: list = []
    structured_schema: dict | None = None
    resume_state: dict | None = None
    max_iters: int = 0

    async def __call__(self):
        svc = app
        agent_name = svc.alias
        resume = self.resume_state or {}
        pending = resume.get('pending', {})
        turn_id = resume.get('turn_id') or uid('turn_')
        iteration = int(pending.get('iteration', resume.get('iter', 0)))
        max_iters = self.max_iters or svc.max_iters
        started_at = float(resume.get('started_at', time.time()))
        total_usage = dict(pending.get('usage', {}))
        accumulated_content: list = []

        if not resume:
            await relay(svc.resolve_ref(
                svc.session_ref, 'OpenSession', session_id=self.session_id,
                user_id='', agent=agent_name,
            ))
            loaded = await relay(svc.resolve_ref(
                svc.session_ref, 'LoadSession', session_id=self.session_id, last_n=50,
            ))
            current_context = await session.get(f'context:{self.session_id}')
            if not current_context.get('items') and loaded.get('turns'):
                archive = svc.get_dependency(svc.session_ref)
                await _append_context(svc, self.session_id,
                                      archive.context_from_turns(loaded['turns']))
            if self.inputs:
                await _append_context(svc, self.session_id, self.inputs)
        else:
            saved_messages = pending.get('messages', [])
            if saved_messages:
                await session.set(f'context:{self.session_id}', {'items': saved_messages})
            if resume.get('kind') == 'question' and resume.get('answer'):
                await _append_context(svc, self.session_id, [{
                    'role': 'user', 'name': 'user', 'content': resume['answer'],
                }])
            elif resume.get('kind') == 'confirm':
                call = pending.get('tool_call', {})
                if resume.get('decision') == 'approve' and call:
                    async for event in self._invoke_call(
                        svc, call, turn_id, iteration, agent_name, 'allow',
                    ):
                        yield event
                elif call:
                    result = {
                        'id': call.get('id', ''), 'status': 'rejected',
                        'output': {'reason': 'user rejected the requested operation'},
                        'artifact': None, 'truncated': False,
                    }
                    await _append_context(svc, self.session_id, [_tool_message(call, result)])
                    yield await _record_event(
                        svc, 'tool.result', session_id=self.session_id, turn_id=turn_id,
                        agent=agent_name, payload=result,
                    )
            elif resume.get('kind') == 'crash' and pending.get('phase') == 'tool':
                call = pending.get('pending_tool_call', {})
                if call:
                    result = {
                        'id': call.get('id', ''), 'status': 'error',
                        'output': {'error': 'service restarted; tool completion is unknown'},
                        'artifact': None, 'truncated': False,
                    }
                    await _append_context(svc, self.session_id, [_tool_message(call, result)])
                    yield await _record_event(
                        svc, 'tool.result', session_id=self.session_id, turn_id=turn_id,
                        agent=agent_name, payload=result,
                    )

        await _write_progress(
            self.session_id, status='running', phase='before_model', turn_id=turn_id,
            iteration=iteration, inputs=self.inputs, total_usage=total_usage,
        )
        yield await _record_event(
            svc, 'reply.started', session_id=self.session_id, turn_id=turn_id,
            agent=agent_name,
            payload={'inputs_digest': str(len(self.inputs)), 'resumed': bool(resume)},
        )
        event = svc.event('ReplyStarted', session_id=self.session_id,
                          turn_id=turn_id, agent=agent_name)
        await hub.emit(topic=type(event).destination, source=event)

        while iteration < max_iters:
            if await should_stop(self.session_id):
                async for event in self._interrupt(svc, turn_id, agent_name,
                                                   total_usage, started_at):
                    yield event
                return

            assembled = await relay(svc.resolve_ref(
                svc.context_ref, 'Assemble', session_id=self.session_id,
                agent=agent_name, inputs=[], system_prompt=svc.system_prompt_of(self.session_id),
                tool_names=[], budget=0, rag_query='',
            ))
            if assembled.get('need_compress'):
                yield await _record_event(
                    svc, 'context.compacting', session_id=self.session_id,
                    turn_id=turn_id, agent=agent_name,
                    payload={'tokens': assembled['tokens']},
                )
                compressed = await relay(svc.resolve_ref(
                    svc.context_ref, 'Compress', session_id=self.session_id,
                    agent=agent_name, keep_ratio=0.0, model_ref=svc.model_ref,
                ))
                yield await _record_event(
                    svc, 'context.compacted', session_id=self.session_id,
                    turn_id=turn_id, agent=agent_name, payload=compressed,
                )
                assembled = await relay(svc.resolve_ref(
                    svc.context_ref, 'Assemble', session_id=self.session_id,
                    agent=agent_name, inputs=[], system_prompt=svc.system_prompt_of(self.session_id),
                    tool_names=[], budget=0, rag_query='',
                ))

            tools = await relay(svc.resolve_ref(
                svc.tool_ref, 'ListTools', session_id=self.session_id,
                agent=agent_name, groups=svc.tool_groups,
            ))
            tool_schemas = [{
                'type': 'function',
                'function': {
                    'name': tool['name'], 'description': tool.get('description', ''),
                    'parameters': tool.get('parameters', {}),
                },
            } for tool in tools]

            await _write_progress(
                self.session_id, status='running', phase='model', turn_id=turn_id,
                iteration=iteration, total_usage=total_usage,
            )
            generated = svc.resolve_ref(
                svc.model_ref, 'Generate', messages=assembled['messages'], tools=tool_schemas,
                tool_choice='auto', stream=True, model='', params={},
            )
            completed = None
            async for item in relay_gen(generated):
                item_type = item.get('type', '')
                if item_type == 'model.delta':
                    yield await _record_event(
                        svc, 'model.delta', session_id=self.session_id, turn_id=turn_id,
                        agent=agent_name, payload=item.get('payload', item),
                    )
                elif item_type == 'model.completed':
                    completed = item.get('payload', item)
                    yield await _record_event(
                        svc, 'model.completed', session_id=self.session_id, turn_id=turn_id,
                        agent=agent_name, payload=completed,
                    )
                elif item_type == 'model.failed':
                    async for event in self._park_for_retry(
                        svc, turn_id, agent_name, iteration, total_usage,
                        item.get('payload', item),
                    ):
                        yield event
                    return

            completed = completed or {'content': [], 'finish_reason': 'stop', 'usage': {}}
            for name, value in completed.get('usage', {}).items():
                if isinstance(value, (int, float)):
                    total_usage[name] = total_usage.get(name, 0) + value
            await _write_progress(
                self.session_id, status='running', phase='after_model',
                iteration=iteration, total_usage=total_usage,
            )

            if svc.next_action(completed, iteration) == 'exit':
                accumulated_content = completed.get('content', [])
                break

            calls = svc.extract_tool_calls(completed)
            await _append_context(svc, self.session_id,
                                  [svc.to_msg(completed.get('content', []))])
            allowed = []
            for call in calls:
                permission = await relay(svc.resolve_ref(
                    svc.tool_ref, 'CheckPermission', session_id=self.session_id,
                    agent=agent_name, tool=call.get('name', ''), args=call.get('input', {}),
                ))
                action = permission.get('action', 'allow')
                if action == 'ask':
                    async for event in self._park_for_confirmation(
                        svc, turn_id, agent_name, iteration, total_usage, call, permission,
                    ):
                        yield event
                    return
                if action == 'deny':
                    result = {
                        'id': call.get('id', ''), 'status': 'denied',
                        'output': {'error': permission.get('reason', 'denied')},
                        'artifact': None, 'truncated': False,
                    }
                    await _append_context(svc, self.session_id, [_tool_message(call, result)])
                    yield await _record_event(
                        svc, 'tool.result', session_id=self.session_id, turn_id=turn_id,
                        agent=agent_name, payload=result,
                    )
                else:
                    allowed.append(call)

            for call in allowed:
                if await should_stop(self.session_id):
                    async for event in self._interrupt(svc, turn_id, agent_name,
                                                       total_usage, started_at):
                        yield event
                    return
                async for event in self._invoke_call(svc, call, turn_id, iteration,
                                                     agent_name, 'allow'):
                    yield event
                result = getattr(self, '_last_invoke_result', {})
                if result.get('status') == 'parked':
                    async for event in self._park_from_tool_result(
                        svc, turn_id, agent_name, result,
                    ):
                        yield event
                    return

            iteration += 1
            event = svc.event('IterationCompleted', session_id=self.session_id,
                              turn_id=turn_id, agent=agent_name, iter=iteration)
            await hub.emit(topic=type(event).destination, source=event)
            yield await _record_event(
                svc, 'iteration.completed', session_id=self.session_id, turn_id=turn_id,
                agent=agent_name, payload={'iter': iteration},
            )

        finish_reason = 'stop' if accumulated_content else 'max_iters'
        await _append_context(svc, self.session_id, [svc.to_msg(accumulated_content)])
        await _save_completed_turn(
            svc, session_id=self.session_id, turn_id=turn_id, agent=agent_name,
            inputs=self.inputs, content=accumulated_content, usage=total_usage,
            finish_reason=finish_reason, started_at=started_at,
        )
        await _write_progress(self.session_id, status='finished', phase='finished',
                              pending_tool_call={})
        yield await _record_event(
            svc, 'reply.finished', session_id=self.session_id, turn_id=turn_id,
            agent=agent_name,
            payload={'content': accumulated_content, 'finish_reason': finish_reason,
                     'usage': total_usage},
        )
        event = svc.event(
            'ReplyFinished', session_id=self.session_id, turn_id=turn_id,
            agent=agent_name, content=accumulated_content, usage=total_usage,
            finish_reason=finish_reason, ms=int((time.time() - started_at) * 1000),
            inputs=self.inputs,
        )
        await hub.emit(topic=type(event).destination, source=event)

    async def _invoke_call(self, svc, call: dict, turn_id: str, iteration: int,
                           agent_name: str, permission_action: str):
        call_id = call.get('id', uid('call_'))
        yield await _record_event(
            svc, 'tool.call', session_id=self.session_id, turn_id=turn_id,
            agent=agent_name,
            payload={'id': call_id, 'name': call.get('name', ''),
                     'input': call.get('input', {})},
        )
        context = await session.get(f'context:{self.session_id}')
        await _write_progress(
            self.session_id, status='running', phase='tool', turn_id=turn_id,
            iteration=iteration, pending_tool_call=call,
        )
        invoke = svc.resolve_ref(
            svc.tool_ref, 'Invoke', call_id=call_id, tool=call.get('name', ''),
            args=call.get('input', {}), session_id=self.session_id, agent=agent_name,
        )
        invoke.data.update({
            'turn_id': turn_id, 'iteration': iteration,
            'messages': context.get('items', []),
            'permission_action': permission_action,
        })
        async for event in relay_gen(invoke):
            if event.get('type', '').startswith('tool.'):
                yield await _record_event(
                    svc, event.get('type', 'tool.chunk'), session_id=self.session_id,
                    turn_id=turn_id, agent=agent_name,
                    payload=event.get('payload', event),
                )
        result = _last_result(invoke.state.result())
        payload = result.get('payload', result) if isinstance(result, dict) else {'output': result}
        self._last_invoke_result = payload
        if payload.get('status') != 'parked':
            await _append_context(svc, self.session_id, [_tool_message(call, payload)])
            await _write_progress(self.session_id, status='running', phase='after_tool',
                                  pending_tool_call={})

    async def _park_for_confirmation(self, svc, turn_id, agent_name, iteration, usage,
                                     call, permission):
        context = await session.get(f'context:{self.session_id}')
        park_id = await relay(svc.resolve_ref(
            svc.session_ref, 'Park', session_id=self.session_id, turn_id=turn_id,
            kind='confirm', pending={
                'iteration': iteration, 'messages': context.get('items', []),
                'tool_call': call, 'reason': permission.get('reason', ''), 'usage': usage,
            },
        ))
        await _write_progress(self.session_id, status='parked', phase='confirm',
                              pending_tool_call=call)
        yield await _record_event(
            svc, 'require_user_confirm', session_id=self.session_id, turn_id=turn_id,
            agent=agent_name,
            payload={'park_id': park_id, 'tool': call.get('name', ''),
                     'args': call.get('input', {}), 'reason': permission.get('reason', '')},
        )
        event = svc.event('ReplyParked', session_id=self.session_id, turn_id=turn_id,
                          agent=agent_name, park_id=park_id, kind='confirm')
        await hub.emit(topic=type(event).destination, source=event)

    async def _park_from_tool_result(self, svc, turn_id, agent_name, result):
        data = result.get('output', {})
        kind = data.get('kind', 'question')
        await _write_progress(self.session_id, status='parked', phase=kind)
        yield await _record_event(
            svc, 'require_user_answer' if kind == 'question' else 'require_user_confirm',
            session_id=self.session_id, turn_id=turn_id, agent=agent_name, payload=data,
        )
        event = svc.event('ReplyParked', session_id=self.session_id, turn_id=turn_id,
                          agent=agent_name, park_id=data.get('park_id', ''), kind=kind)
        await hub.emit(topic=type(event).destination, source=event)

    async def _park_for_retry(self, svc, turn_id, agent_name, iteration, usage, error):
        context = await session.get(f'context:{self.session_id}')
        park_id = await relay(svc.resolve_ref(
            svc.session_ref, 'Park', session_id=self.session_id, turn_id=turn_id,
            kind='retry', pending={
                'iteration': iteration, 'messages': context.get('items', []), 'usage': usage,
            },
        ))
        await _write_progress(self.session_id, status='parked', phase='retry')
        yield await _record_event(
            svc, 'error', session_id=self.session_id, turn_id=turn_id, agent=agent_name,
            payload={'code': 'model_failed', 'message': error.get('error', ''),
                     'retryable': True},
        )
        yield await _record_event(
            svc, 'reply.parked', session_id=self.session_id, turn_id=turn_id,
            agent=agent_name, payload={'park_id': park_id, 'kind': 'retry'},
        )

    async def _interrupt(self, svc, turn_id, agent_name, usage, started_at):
        await _save_completed_turn(
            svc, session_id=self.session_id, turn_id=turn_id, agent=agent_name,
            inputs=self.inputs, content=[], usage=usage, finish_reason='interrupted',
            started_at=started_at,
        )
        await _write_progress(self.session_id, status='interrupted', phase='interrupted')
        yield await _record_event(
            svc, 'reply.interrupted', session_id=self.session_id, turn_id=turn_id,
            agent=agent_name, payload={'reason': 'user interrupt'},
        )
        event = svc.event('ReplyInterrupted', session_id=self.session_id, turn_id=turn_id,
                          agent=agent_name, reason='user interrupt')
        await hub.emit(topic=type(event).destination, source=event)


class Resume(BaseCommand):
    """Continue a parked conversation turn or a recoverable interrupted turn."""

    session_id: str = ''
    park_id: str = ''
    decision: str = ''
    answer: str = ''

    async def __call__(self):
        if self.park_id:
            # Validate against the stored record before removing it.  A client
            # typo must not make the pending confirmation/question disappear.
            session_store = app.get_dependency(app.session_ref)
            parks = await session_store.protocol.get(session_store.park_key(self.session_id)) or {}
            state = parks.get(self.park_id, {})
            if state.get('kind') == 'confirm' and self.decision not in {'approve', 'reject'}:
                yield await chunk('error', session_id=self.session_id, payload={
                    'code': 'invalid_decision', 'message': 'confirmation requires approve or reject',
                    'retryable': False,
                })
                return
            if state.get('kind') == 'question' and not self.answer:
                yield await chunk('error', session_id=self.session_id, payload={
                    'code': 'answer_required', 'message': 'an answer is required to continue',
                    'retryable': False,
                })
                return
            state = yield app.resolve_ref(
                app.session_ref, 'Unpark', session_id=self.session_id, park_id=self.park_id,
            )
        else:
            progress = await session.get(f'turn:{self.session_id}')
            state = {'turn_id': progress.get('turn_id', ''), 'kind': 'crash', 'pending': progress}
        if not state.get('turn_id'):
            yield await chunk('error', session_id=self.session_id, payload={
                'code': 'resume_not_found', 'message': 'no unfinished conversation was found',
                'retryable': False,
            })
            return
        reply = registry.resolve(f'agent.{app.alias}.Reply')(
            session_id=self.session_id, inputs=[], structured_schema=None,
            resume_state={
                'turn_id': state.get('turn_id', ''), 'kind': state.get('kind', ''),
                'pending': state.get('pending', {}), 'decision': self.decision,
                'answer': self.answer,
            }, max_iters=0,
        )
        async for event in relay_gen(reply):
            yield event


class Interrupt(BaseCommand):
    """Request that the current turn stops at its next safe boundary."""

    session_id: str = ''
    reason: str = 'user'

    async def __call__(self) -> dict:
        progress = await session.get(f'turn:{self.session_id}')
        progress.update({'interrupted': True, 'reason': self.reason,
                         'status': 'interrupt_requested'})
        await session.set(f'turn:{self.session_id}', progress)
        return {'session_id': self.session_id, 'turn_id': progress.get('turn_id', ''),
                'accepted': True, 'reason': self.reason}


class Observe(BaseCommand):
    """Append messages to context without replying."""

    session_id: str = ''
    inputs: list = []
    sender: str = ''

    async def __call__(self) -> int:
        return await _append_context(app, self.session_id, self.inputs)


class Spawn(BaseCommand):
    """Delegate to a sub-agent."""

    agent: str = ''
    task: str = ''
    session_id: str = ''
    inherit_context: bool = False

    async def __call__(self):
        sub = registry.resolve(f'agent.{self.agent}.Reply')(
            session_id=self.session_id,
            inputs=[{'role': 'user', 'content': self.task}],
            structured_schema=None, resume_state=None, max_iters=0,
        )
        async for event in relay_gen(sub):
            yield await chunk('subagent.chunk', session_id=self.session_id,
                              payload={'agent': self.agent, 'data': event})
        yield await chunk('subagent.result', session_id=self.session_id,
                          payload={'agent': self.agent, 'content': sub.state.result()})


class OnMessageBroadcast(BaseEvent):
    async def __call__(self) -> dict:
        source = self.data.get('events', [{}])[-1]
        ctx_key = f'context:{source.get("topic", "")}'
        data = await session.get(ctx_key)
        history = data.get('items', []) if isinstance(data, dict) else []
        history.append({'role': 'user', 'content': json.dumps(source.get('content', [])),
                        'name': source.get('sender', 'team')})
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
