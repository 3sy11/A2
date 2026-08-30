"""Layer 1: kernel unit tests."""

import pytest

from a2.kernel.chunk import chunk, next_seq
from a2.message.models import Msg, TextBlock


def test_msg_user():
    msg = Msg.user('hello')
    assert msg.role == 'user'
    assert msg.text() == 'hello'


@pytest.mark.asyncio
async def test_chunk_structure():
    from bollydog.testing import run_command
    from bollydog.adapters.memory import MemoryProtocol
    from bollydog.service.session import Session

    proto = MemoryProtocol()
    await proto.on_start()
    sess = Session()
    sess.add_dependency(proto)
    await sess.on_start()

    from bollydog.globals import _session_ctx_stack
    with _session_ctx_stack.push(sess):
        c = await chunk('reply.started', session_id='s1', payload={'x': 1})
    assert c['type'] == 'reply.started'
    assert c['session_id'] == 's1'
    assert c['payload']['x'] == 1


def test_text_block():
    block = TextBlock(text='test')
    assert block.type == 'text'
    assert block.text == 'test'
