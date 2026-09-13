"""P0: durable conversation lifecycle tests."""

from __future__ import annotations

from contextlib import ExitStack

import pytest

from bollydog.bootstrap import Bootstrap
from bollydog.globals import (
    _registry_ctx_stack,
    _services_ctx_stack,
    _session_ctx_stack,
    registry,
    services,
)

from tests.conftest import CONFIG, _close_protocols


async def _start(bootstrap):
    for key in (
        'bollydog.Session', 'session.store', 'agent.assistant', 'model.chat',
        'context.default', 'tool.toolkit', 'workspace.local', 'plan.notebook',
        'credential.vault', 'observe.tracer',
    ):
        svc = bootstrap.services.get(key)
        if svc:
            await svc.maybe_start()


@pytest.mark.asyncio
async def test_reply_writes_context_turn_and_events(persistent_execute):
    execute = persistent_execute.services.executor
    Reply = registry.resolve('agent.assistant.Reply')
    reply = Reply(session_id='durable', inputs=[{'role': 'user', 'content': 'Hello'}])
    await execute.execute(reply)
    chunks = [item async for item in reply.state]

    context = await services.session.get('context:durable')
    assert context['items'][0]['content'] == 'Hello'
    assert context['items'][-1]['role'] == 'assistant'
    assert all(chunk['seq'] > 0 for chunk in chunks)

    session_store = services['session.store']
    saved = await session_store.protocol.get('turn:durable:' + chunks[0]['turn_id'])
    assert saved['finish_reason'] == 'stop'
    events = await session_store.protocol.keys('evt:durable:*')
    assert len(events) == len(chunks)


@pytest.mark.asyncio
async def test_session_survives_rebuild(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = Bootstrap(config=CONFIG)
    with ExitStack() as stack:
        stack.enter_context(_services_ctx_stack.push(first.services))
        stack.enter_context(_registry_ctx_stack.push(first.services.registry))
        stack.enter_context(_session_ctx_stack.push(first.services.session))
        async with first.services.executor:
            try:
                await _start(first)
                Reply = registry.resolve('agent.assistant.Reply')
                reply = Reply(session_id='restart', inputs=[{'role': 'user', 'content': 'remember this'}])
                await first.services.executor.execute(reply)
                [item async for item in reply.state]
            finally:
                await _close_protocols(first)

    second = Bootstrap(config=CONFIG)
    with ExitStack() as stack:
        stack.enter_context(_services_ctx_stack.push(second.services))
        stack.enter_context(_registry_ctx_stack.push(second.services.registry))
        stack.enter_context(_session_ctx_stack.push(second.services.session))
        async with second.services.executor:
            try:
                await _start(second)
                OpenSession = registry.resolve('session.store.OpenSession')
                opened = await second.services.executor.execute(OpenSession(session_id='restart'))
                assert opened['turn_count'] == 1
                LoadSession = registry.resolve('session.store.LoadSession')
                loaded = await second.services.executor.execute(LoadSession(session_id='restart'))
                assert loaded['turns'][0]['inputs'][0]['content'] == 'remember this'
            finally:
                await _close_protocols(second)


@pytest.mark.asyncio
async def test_permission_confirmation_parks_then_resumes(persistent_execute):
    execute = persistent_execute.services.executor
    def scripted_reply(messages, tools):
        if any(message.get('role') == 'tool' for message in messages):
            return {
                'content': [{'type': 'text', 'text': 'completed after confirmation'}],
                'finish_reason': 'stop',
                'usage': {},
            }
        return {
            'content': [{
                'type': 'tool_call', 'id': 'confirm_call', 'name': 'ReadPath',
                'input': {'path': 'README.md'},
            }],
            'finish_reason': 'tool_calls', 'usage': {},
        }

    services['model.chat'].protocol.scripts = {'*': scripted_reply}
    services['tool.toolkit'].protocol.rules = [
        {'pattern': 'ReadPath', 'action': 'ask', 'reason': 'need confirmation'},
    ]
    Reply = registry.resolve('agent.assistant.Reply')
    reply = Reply(session_id='confirm', inputs=[{'role': 'user', 'content': 'read a file'}])
    await execute.execute(reply)
    chunks = [item async for item in reply.state]
    confirm = next(item for item in chunks if item['type'] == 'require_user_confirm')

    OpenSession = registry.resolve('session.store.OpenSession')
    opened = await execute.execute(OpenSession(session_id='confirm'))
    assert opened['resumable'] is True
    assert opened['pending_actions'][0]['park_id'] == confirm['payload']['park_id']

    Resume = registry.resolve('agent.assistant.Resume')
    resume = Resume(session_id='confirm', park_id=confirm['payload']['park_id'], decision='approve')
    await execute.execute(resume)
    resumed = [item async for item in resume.state]
    assert any(item['type'] == 'tool.call' for item in resumed)
    assert any(item['type'] == 'reply.finished' for item in resumed)


@pytest.mark.asyncio
async def test_invalid_confirmation_does_not_remove_pending_action(persistent_execute):
    execute = persistent_execute.services.executor
    Park = registry.resolve('session.store.Park')
    park_id = await execute.execute(Park(
        session_id='invalid-confirmation', turn_id='turn_pending', kind='confirm',
        pending={'tool_call': {'name': 'ReadPath', 'input': {'path': 'README.md'}}},
    ))

    Resume = registry.resolve('agent.assistant.Resume')
    resume = Resume(session_id='invalid-confirmation', park_id=park_id, decision='later')
    await execute.execute(resume)
    chunks = [item async for item in resume.state]
    assert chunks[0]['payload']['code'] == 'invalid_decision'

    session_store = services['session.store']
    parked = await session_store.protocol.get(session_store.park_key('invalid-confirmation'))
    assert park_id in parked


@pytest.mark.asyncio
async def test_question_parks_then_uses_the_user_answer(persistent_execute):
    execute = persistent_execute.services.executor

    def scripted_reply(messages, tools):
        if any(message.get('role') == 'user' and message.get('content') == 'Blue' for message in messages):
            return {
                'content': [{'type': 'text', 'text': 'recorded the answer'}],
                'finish_reason': 'stop', 'usage': {},
            }
        return {
            'content': [{
                'type': 'tool_call', 'id': 'question_call', 'name': 'AskHuman',
                'input': {'question': 'What is your favorite color?', 'options': ['Blue', 'Green']},
            }],
            'finish_reason': 'tool_calls', 'usage': {},
        }

    services['model.chat'].protocol.scripts = {'*': scripted_reply}
    Reply = registry.resolve('agent.assistant.Reply')
    reply = Reply(session_id='question', inputs=[{'role': 'user', 'content': 'ask me'}])
    await execute.execute(reply)
    chunks = [item async for item in reply.state]
    question = next(item for item in chunks if item['type'] == 'require_user_answer')

    Resume = registry.resolve('agent.assistant.Resume')
    resume = Resume(session_id='question', park_id=question['payload']['park_id'], answer='Blue')
    await execute.execute(resume)
    resumed = [item async for item in resume.state]
    assert any(item['type'] == 'reply.finished' for item in resumed)

    context = await services.session.get('context:question')
    assert any(item.get('content') == 'Blue' for item in context['items'])


@pytest.mark.asyncio
async def test_replay_events_after_sequence(persistent_execute):
    execute = persistent_execute.services.executor
    Reply = registry.resolve('agent.assistant.Reply')
    reply = Reply(session_id='replay', inputs=[{'role': 'user', 'content': 'Hello'}])
    await execute.execute(reply)
    chunks = [item async for item in reply.state]
    ReplayEvents = registry.resolve('session.store.ReplayEvents')
    replay_cmd = ReplayEvents(session_id='replay', last_seq=chunks[0]['seq'])
    await execute.execute(replay_cmd)
    replayed = [item async for item in replay_cmd.state]
    assert [item['seq'] for item in replayed[:-1]] == [item['seq'] for item in chunks[1:]]


@pytest.mark.asyncio
async def test_interrupted_turn_is_saved(persistent_execute):
    execute = persistent_execute.services.executor
    Interrupt = registry.resolve('agent.assistant.Interrupt')
    await execute.execute(Interrupt(session_id='interrupted'))

    Reply = registry.resolve('agent.assistant.Reply')
    reply = Reply(session_id='interrupted', inputs=[{'role': 'user', 'content': 'stop'}])
    await execute.execute(reply)
    chunks = [item async for item in reply.state]
    assert any(item['type'] == 'reply.interrupted' for item in chunks)

    turn_id = chunks[0]['turn_id']
    saved = await services['session.store'].protocol.get(f'turn:interrupted:{turn_id}')
    assert saved['finish_reason'] == 'interrupted'


@pytest.mark.asyncio
async def test_crash_during_tool_never_repeats_the_tool(persistent_execute):
    execute = persistent_execute.services.executor
    await services.session.set('context:crash', {'items': [
        {'role': 'user', 'content': 'continue safely'},
    ]})
    await services.session.set('turn:crash', {
        'status': 'running', 'phase': 'tool', 'turn_id': 'turn_crash',
        'iteration': 1,
        'pending_tool_call': {
            'id': 'old_call', 'name': 'ReadPath', 'input': {'path': 'README.md'},
        },
    })

    Resume = registry.resolve('agent.assistant.Resume')
    resume = Resume(session_id='crash')
    await execute.execute(resume)
    chunks = [item async for item in resume.state]
    tool_result = next(item for item in chunks if item['type'] == 'tool.result')
    assert tool_result['payload']['status'] == 'error'
    assert any(item['type'] == 'reply.finished' for item in chunks)


@pytest.mark.asyncio
async def test_plan_credential_and_trace_persist(persistent_execute):
    execute = persistent_execute.services.executor
    CreatePlan = registry.resolve('plan.notebook.CreatePlan')
    await execute.execute(CreatePlan(session_id='state', goal='g', tasks=[]))
    PutCredential = registry.resolve('credential.vault.PutCredential')
    await execute.execute(PutCredential(user_id='u', system='s', payload={'key': 'value'}))

    trace = services['observe.tracer']
    await trace.protocol.set('span:trace:span', {'trace_id': 'trace', 'span_id': 'span'})
    assert await services['plan.notebook'].protocol.get('plan:state')
    assert await services['credential.vault'].protocol.get('cred:u:s')
    assert await trace.protocol.get('span:trace:span')
