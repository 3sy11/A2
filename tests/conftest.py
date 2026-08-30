"""Test configuration for A2."""

import pytest
from contextlib import ExitStack

from bollydog.bootstrap import Bootstrap
from bollydog.globals import (
    _registry_ctx_stack,
    _services_ctx_stack,
    _session_ctx_stack,
)

CONFIG = 'config/agent.toml'


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
            yield bootstrap.services.executor
