"""Tool domain models."""

from __future__ import annotations

from bollydog.models.base import BaseDomain


class ToolSpec(BaseDomain):
    name: str = ''
    destination: str = ''
    description: str = ''
    parameters: dict = {}
    group: str = 'basic'
    read_only: bool = False
    concurrency_safe: bool = True
    external: bool = False


class PermissionRule(BaseDomain):
    pattern: str = '*'
    action: str = 'allow'
    when: dict = {}
    reason: str = ''


class ToolGroup(BaseDomain):
    name: str = ''
    description: str = ''
    instructions: str = ''
    tools: list = []
    always_on: bool = False
