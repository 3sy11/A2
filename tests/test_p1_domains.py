"""P1: domain capabilities wired into the agent main loop (S03/S10/S11/S13/S23)."""

from __future__ import annotations

import importlib
from contextlib import ExitStack
from pathlib import Path

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

EMIT_MODULES = (
    'a2.agent.commands',
    'a2.skill.commands',
    'a2.tool.commands',
    'a2.plan.commands',
    'a2.memory.commands',
)


class EmitRecorder:
    """Collect the events commands publish, since hub events never run here."""

    def __init__(self):
        self.events = []

    async def emit(self, topic=None, source=None, **kwargs):
        self.events.append(source)

    def of(self, name: str) -> list:
        return [event for event in self.events if type(event).__name__ == name]


@pytest.fixture
def emitted(monkeypatch):
    """Replace the hub proxy in every emitting module with a recorder."""
    recorder = EmitRecorder()
    for name in EMIT_MODULES:
        monkeypatch.setattr(importlib.import_module(name), 'hub', recorder, raising=False)
    return recorder


def script(services, *steps):
    """Install a scripted model that walks `steps`, repeating the last one."""
    calls = []

    def run(messages, tools):
        calls.append({'messages': messages, 'tools': tools})
        return steps[min(len(calls) - 1, len(steps) - 1)]

    run.calls = calls
    services['model.chat'].protocol.scripts = {'*': run}
    return run


def text(body: str) -> dict:
    return {
        'content': [{'type': 'text', 'text': body}],
        'finish_reason': 'stop',
        'usage': {},
    }


def call(name: str, args: dict, call_id: str = 'c1') -> dict:
    return {
        'content': [{'type': 'tool_call', 'id': call_id, 'name': name, 'input': args}],
        'finish_reason': 'tool_calls',
        'usage': {},
    }


def joined(messages: list) -> str:
    return '\n'.join(str(message.get('content', '')) for message in messages)


async def run_reply(execute, **kwargs):
    Reply = registry.resolve('agent.assistant.Reply')
    reply = Reply(**kwargs)
    await execute.execute(reply)
    return [chunk async for chunk in reply.state]


# --- I1: injection contract and identity -------------------------------------


@pytest.mark.asyncio
async def test_user_id_reaches_session_and_finished_event(persistent_execute, emitted):
    execute = persistent_execute.services.executor
    script(persistent_execute.services, text('hi'))

    await run_reply(
        execute, session_id='id1', user_id='u1',
        inputs=[{'role': 'user', 'content': 'Hello', 'name': 'user'}],
    )

    OpenSession = registry.resolve('session.store.OpenSession')
    opened = await execute.execute(OpenSession(session_id='id1'))
    assert opened['user_id'] == 'u1'
    assert emitted.of('ReplyFinished')[0].user_id == 'u1'


@pytest.mark.asyncio
async def test_assemble_renders_every_injected_source(persistent_execute):
    execute = persistent_execute.services.executor
    Assemble = registry.resolve('context.default.Assemble')

    result = await execute.execute(Assemble(
        session_id='ctx1', agent='assistant', system_prompt='SYS',
        skills=[{'name': 'manual', 'description': 'how to write the report'}],
        memories=[{'text': 'prefers dark mode'}],
        plan={'rendered': 'Task list:\n[ ] task_1: gather numbers'},
        rag_hits=[{'source': 'doc.md', 'text': 'the answer is 42'}],
    ))

    rendered = joined(result['messages'])
    assert 'SYS' in rendered
    assert 'manual: how to write the report' in rendered
    assert 'prefers dark mode' in rendered
    assert 'task_1: gather numbers' in rendered
    assert '[doc.md] the answer is 42' in rendered
    assert result['injected']['rag_hits'] == 1
    assert result['injected']['memories'] == 1
    assert result['injected']['skills'] == 1
    assert result['injected']['plan'] is True


@pytest.mark.asyncio
async def test_assemble_without_sources_is_unchanged(persistent_execute):
    execute = persistent_execute.services.executor
    Assemble = registry.resolve('context.default.Assemble')

    result = await execute.execute(Assemble(
        session_id='ctx2', agent='assistant', system_prompt='SYS',
    ))

    assert [message['role'] for message in result['messages']] == ['system', 'system']
    assert result['injected']['rag_hits'] == 0
    assert result['injected']['plan'] is False


# --- I2: skill progressive disclosure (S13) ----------------------------------


@pytest.mark.asyncio
async def test_skill_catalog_is_injected_and_body_loads_on_demand(
    persistent_execute, emitted,
):
    bootstrap = persistent_execute
    execute = bootstrap.services.executor
    skill_dir = Path('.a2/skills/manual')
    skill_dir.mkdir(parents=True)
    (skill_dir / 'SKILL.md').write_text(
        '---\n'
        'name: manual\n'
        'description: how to write the weekly report\n'
        'keywords: report\n'
        '---\n'
        'Step 1: gather the numbers.\n',
        encoding='utf-8',
    )

    scripted = script(
        bootstrap.services,
        call('LoadSkill', {'name': 'manual'}),
        text('followed the manual'),
    )
    await run_reply(
        execute, session_id='sk1',
        inputs=[{'role': 'user', 'content': 'write the weekly report', 'name': 'user'}],
    )

    catalog = joined(scripted.calls[0]['messages'])
    assert 'manual: how to write the weekly report' in catalog
    assert 'Step 1: gather the numbers.' not in catalog

    body = joined(scripted.calls[1]['messages'])
    assert 'Step 1: gather the numbers.' in body
    assert [event.skills for event in emitted.of('SkillActivated')] == [['manual']]


# --- I3: document RAG with citations (S10) -----------------------------------


@pytest.mark.asyncio
async def test_document_rag_is_injected_with_citation(persistent_execute):
    bootstrap = persistent_execute
    execute = bootstrap.services.executor
    # The hashed pseudo-embedder has no notion of similarity, so ranking here
    # proves the wiring, not retrieval quality (real vectors are P2).
    bootstrap.services['agent.assistant'].rag_score_threshold = -1.0
    Path('doc.md').write_text('A2 is an event driven agent framework.', encoding='utf-8')

    IngestDocument = registry.resolve('knowledge.base.IngestDocument')
    ingest = IngestDocument(path='doc.md', collection='default')
    await execute.execute(ingest)
    [chunk async for chunk in ingest.state]

    scripted = script(bootstrap.services, text('A2 is event driven.'))
    await run_reply(
        execute, session_id='rag1',
        inputs=[{'role': 'user', 'content': 'what is A2', 'name': 'user'}],
    )

    messages = joined(scripted.calls[0]['messages'])
    assert 'event driven agent framework' in messages
    assert '[doc.md]' in messages


# --- I4: long-term memory across sessions (S11) ------------------------------


async def _start(bootstrap):
    for key in (
        'bollydog.Session', 'session.store', 'agent.assistant', 'model.chat',
        'model.embed', 'context.default', 'tool.toolkit', 'workspace.local',
        'plan.notebook', 'skill.hub', 'knowledge.base', 'memory.longterm',
        'credential.vault', 'observe.tracer',
    ):
        service = bootstrap.services.get(key)
        if service:
            await service.maybe_start()


async def _remember_preference(services, execute, emitted, session_id: str, user_id: str):
    """Run one turn and let the memory subscriber extract a fact from it."""
    script(services, text('Sure, I will remember that you prefer dark mode.'))
    await run_reply(
        execute, session_id=session_id, user_id=user_id,
        inputs=[{'role': 'user', 'content': 'remember I prefer dark mode', 'name': 'user'}],
    )
    finished = emitted.of('ReplyFinished')[0]
    OnReplyFinished = services.exchange.resolve('memory.longterm.OnReplyFinished')
    await execute.execute(OnReplyFinished(data={'events': [{
        'session_id': session_id, 'turn_id': finished.turn_id, 'user_id': user_id,
        'content': finished.content, 'inputs': finished.inputs, 'usage': {},
        'finish_reason': 'stop', 'ms': 0,
    }]}))


@pytest.mark.asyncio
async def test_memory_recalled_in_a_different_session(persistent_execute, emitted):
    bootstrap = persistent_execute
    execute = bootstrap.services.executor
    await _remember_preference(bootstrap.services, execute, emitted, 'memA', 'u1')

    keys = await bootstrap.services['memory.longterm'].protocol.keys('memory:user:u1:*')
    assert keys

    scripted = script(bootstrap.services, text('dark mode'))
    await run_reply(
        execute, session_id='memB', user_id='u1',
        inputs=[{'role': 'user', 'content': 'what do I prefer', 'name': 'user'}],
    )

    assert 'prefer dark mode' in joined(scripted.calls[0]['messages'])


@pytest.mark.asyncio
async def test_memory_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    first = Bootstrap(config=CONFIG)
    with ExitStack() as stack:
        stack.enter_context(_services_ctx_stack.push(first.services))
        stack.enter_context(_registry_ctx_stack.push(first.services.registry))
        stack.enter_context(_session_ctx_stack.push(first.services.session))
        async with first.services.executor:
            try:
                await _start(first)
                script(first.services, text('Sure, I will remember that you prefer dark mode.'))
                await run_reply(
                    first.services.executor, session_id='restartA', user_id='u9',
                    inputs=[{'role': 'user', 'content': 'remember I prefer dark mode',
                             'name': 'user'}],
                )
                OnReplyFinished = services.exchange.resolve('memory.longterm.OnReplyFinished')
                await first.services.executor.execute(OnReplyFinished(data={'events': [{
                    'session_id': 'restartA', 'turn_id': 't1', 'user_id': 'u9',
                    'content': [{'type': 'text', 'text': 'I will remember you prefer dark mode.'}],
                    'inputs': [], 'usage': {}, 'finish_reason': 'stop', 'ms': 0,
                }]}))
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
                keys = await second.services['memory.longterm'].protocol.keys('memory:user:u9:*')
                assert keys
            finally:
                await _close_protocols(second)


# --- I5: plan-driven execution (S03) -----------------------------------------


@pytest.mark.asyncio
async def test_plan_is_created_and_progresses(persistent_execute, emitted):
    bootstrap = persistent_execute
    execute = bootstrap.services.executor
    scripted = script(
        bootstrap.services,
        call('CreatePlan', {
            'goal': 'write the report',
            'tasks': [{'subject': 'gather numbers'}, {'subject': 'draft it'}],
        }),
        call('UpdateTask', {'task_id': 'task_1', 'state': 'completed'}, call_id='c2'),
        text('the report is done'),
    )

    await run_reply(
        execute, session_id='plan1',
        inputs=[{'role': 'user', 'content': 'write the report', 'name': 'user'}],
    )

    plan = await bootstrap.services['plan.notebook'].protocol.get('plan:plan1')
    assert [task['state'] for task in plan['tasks']] == ['completed', 'pending']
    assert emitted.of('PlanCreated')
    assert [event.state for event in emitted.of('TaskUpdated')] == ['completed']

    listed = joined(scripted.calls[1]['messages'])
    assert 'Current task list:' in listed
    assert 'task_1: gather numbers' in listed


# --- I6: task-driven tool-group activation (S23) -----------------------------


@pytest.mark.asyncio
async def test_tool_groups_start_narrow_and_open_on_request(persistent_execute):
    bootstrap = persistent_execute
    execute = bootstrap.services.executor
    ListTools = registry.resolve('tool.toolkit.ListTools')

    def names(schemas):
        return {schema['name'] for schema in schemas}

    before = await execute.execute(ListTools(session_id='grp1', agent='assistant', groups=[]))
    assert 'ActivateToolGroup' in names(before)
    assert 'WritePath' not in names(before)

    script(
        bootstrap.services,
        call('ActivateToolGroup', {'enable': ['edit']}),
        text('group opened'),
    )
    await run_reply(
        execute, session_id='grp1',
        inputs=[{'role': 'user', 'content': 'write a file for me', 'name': 'user'}],
    )

    after = await execute.execute(ListTools(session_id='grp1', agent='assistant', groups=[]))
    assert 'WritePath' in names(after)
