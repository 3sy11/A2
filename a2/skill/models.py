"""Skill domain models."""

from bollydog.models.base import BaseDomain


class Skill(BaseDomain):
    name: str = ''
    description: str = ''
    keywords: list = []
    body_path: str = ''
    resources: list = []
    tools: list = []
    version: str = '0.1.0'
    enabled: bool = True
