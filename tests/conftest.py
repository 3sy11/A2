"""Test configuration for A2."""

from pathlib import Path

import pytest
from contextlib import ExitStack

from bollydog.bootstrap import Bootstrap
from bollydog.globals import (
    _registry_ctx_stack,
    _services_ctx_stack,
    _session_ctx_stack,
)

ROOT = Path(__file__).parents[1]
CONFIG = str(ROOT / 'config' / 'agent.toml')


async def _close_protocols(bootstrap):
    """Tests start services without the daemon lifecycle, so close SQLite explicitly."""
    closed = set()
    for svc in bootstrap.services.values():
        protocol = getattr(svc, 'protocol', None)
        if not protocol or id(protocol) in closed or not getattr(protocol, 'adapter', None):
            continue
        closed.add(id(protocol))
        await protocol.on_stop()


@pytest.fixture
async def execute():
    """ExecuteService fixture — Bootstrap already registers in __init__."""
    bootstrap = Bootstrap(config=CONFIG)
    with ExitStack() as stack:
        stack.enter_context(_services_ctx_stack.push(bootstrap.services))
        if bootstrap.services.registry:
            stack.enter_context(_registry_ctx_stack.push(bootstrap.services.registry))
        if bootstrap.services.session:
            stack.enter_context(_session_ctx_stack.push(bootstrap.services.session))
        async with bootstrap.services.executor:
            try:
                yield bootstrap.services.executor
            finally:
                await _close_protocols(bootstrap)


@pytest.fixture
async def persistent_execute(tmp_path, monkeypatch):
    """Run services in an isolated directory while keeping SQLite files for a test."""
    monkeypatch.chdir(tmp_path)
    bootstrap = Bootstrap(config=CONFIG)
    with ExitStack() as stack:
        stack.enter_context(_services_ctx_stack.push(bootstrap.services))
        if bootstrap.services.registry:
            stack.enter_context(_registry_ctx_stack.push(bootstrap.services.registry))
        if bootstrap.services.session:
            stack.enter_context(_session_ctx_stack.push(bootstrap.services.session))
        async with bootstrap.services.executor:
            for key in (
                'bollydog.Session', 'session.store', 'agent.assistant', 'model.chat',
                'context.default', 'tool.toolkit', 'workspace.local', 'plan.notebook',
                'credential.vault', 'observe.tracer',
            ):
                svc = bootstrap.services.get(key)
                if svc:
                    await svc.maybe_start()
            try:
                yield bootstrap
            finally:
                await _close_protocols(bootstrap)
