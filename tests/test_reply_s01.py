"""Layer 4: S01 walking skeleton — direct answer via Reply."""

from __future__ import annotations

import os

import pytest

from bollydog.globals import registry, services


@pytest.fixture
async def started_services(execute):
    """Start services that need protocol/session initialization."""
    session_svc = services.get('bollydog.Session')
    if session_svc:
        await session_svc.maybe_start()
    for key in ('session.store', 'agent.assistant', 'model.chat', 'context.default', 'tool.toolkit'):
        svc = services.get(key)
        if svc:
            await svc.maybe_start()
    return execute


@pytest.mark.asyncio
async def test_s01_direct_reply(started_services):
    """S01: user asks a question, system gives direct answer."""
    execute = started_services
    Reply = registry.resolve('agent.assistant.Reply')
    cmd = Reply(
        session_id='test_session_1',
        inputs=[{'role': 'user', 'content': 'Hello', 'name': 'user'}],
        structured_schema=None,
        resume_state=None,
        max_iters=0,
    )
    await execute.execute(cmd)
    chunks = []
    async for item in cmd.state:
        chunks.append(item)

    types = [c.get('type') for c in chunks]
    assert 'reply.started' in types
    assert 'reply.finished' in types

    finished = next(c for c in chunks if c.get('type') == 'reply.finished')
    content = finished['payload']['content']
    assert any(b.get('type') == 'text' for b in content)


@pytest.mark.asyncio
async def test_open_session(started_services):
    execute = started_services
    OpenSession = registry.resolve('session.store.OpenSession')
    cmd = OpenSession(session_id='sess_abc', user_id='user1', agent='assistant')
    result = await execute.execute(cmd)
    assert result['session_id'] == 'sess_abc'
    assert result['user_id'] == 'user1'


@pytest.mark.asyncio
async def test_list_tools(started_services):
    execute = started_services
    ListTools = registry.resolve('tool.toolkit.ListTools')
    cmd = ListTools(session_id='s1', agent='assistant', groups=['basic'])
    tools = await execute.execute(cmd)
    names = [t['name'] for t in tools]
    assert 'ReadPath' in names


@pytest.mark.asyncio
async def test_read_path_tool(started_services):
    execute = started_services
    os.makedirs('.a2/workspace', exist_ok=True)
    with open('.a2/workspace/test.txt', 'w') as f:
        f.write('file content here')

    workspace = services.get('workspace.local')
    if workspace:
        await workspace.maybe_start()

    ReadPath = registry.resolve('workspace.local.ReadPath')
    cmd = ReadPath(path='test.txt')
    result = await execute.execute(cmd)
    assert 'file content' in result['content']
