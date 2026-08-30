"""Skill domain commands."""

from __future__ import annotations

from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand, BaseEvent


class ListSkills(BaseCommand):
    """Level-1 disclosure: skill catalog."""

    enabled_only: bool = True

    async def __call__(self) -> list:
        return app.catalog(self.enabled_only)


class MatchSkills(BaseCommand):
    """Level-2 disclosure: match skills to query."""

    query: str = ''
    top_k: int = 3

    async def __call__(self) -> list:
        return app.match(self.query, self.top_k)


class LoadSkill(BaseCommand):
    """Level-3 disclosure: load skill body."""

    name: str = ''
    include_resources: bool = False

    async def __call__(self) -> dict:
        body = app.body(self.name)
        result = {'name': self.name, 'body': body}
        if self.include_resources:
            result['resources'] = []
        return result


class InstallSkill(BaseCommand):
    """Install skill from source. _stub_: full ZIP/GitHub install in P8."""

    source: str = ''
    kind: str = 'path'
    overwrite: bool = False

    async def __call__(self) -> dict:
        return {'status': 'stub', 'message': 'InstallSkill not yet implemented', 'source': self.source}


class SkillActivated(BaseEvent):
    session_id: str = ''
    skills: list = []


class SkillCatalogChanged(BaseEvent):
    added: list = []
    removed: list = []
