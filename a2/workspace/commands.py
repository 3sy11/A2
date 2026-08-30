"""Workspace commands — also registered as tools."""

from __future__ import annotations

import glob as globmod
import re

from typing import ClassVar

from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand, BaseEvent

from a2.kernel import chunk


class ReadPath(BaseCommand):
    """Read file contents from workspace."""
    group: ClassVar[str] = 'basic'
    read_only: ClassVar[bool] = True

    path: str = ''
    offset: int = 0
    limit: int = 0

    async def __call__(self) -> dict:
        target = app.resolve(self.path)
        with open(target, encoding='utf-8', errors='replace') as f:
            content = f.read()
        if self.offset or self.limit:
            lines = content.splitlines()
            end = self.offset + self.limit if self.limit else None
            content = '\n'.join(lines[self.offset:end])
        return {'path': self.path, 'content': content, 'size': len(content)}


class WritePath(BaseCommand):
    """Write content to a workspace file."""
    group: ClassVar[str] = 'edit'

    path: str = ''
    content: str = ''
    mode: str = 'overwrite'

    async def __call__(self) -> dict:
        target = app.resolve(self.path)
        Path = __import__('pathlib').Path
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        flag = 'a' if self.mode == 'append' else 'w'
        with open(target, flag, encoding='utf-8') as f:
            f.write(self.content)
        return {'path': self.path, 'bytes': len(self.content.encode())}


class ListPath(BaseCommand):
    """List directory contents."""
    group: ClassVar[str] = 'basic'
    read_only: ClassVar[bool] = True

    path: str = '.'
    depth: int = 1

    async def __call__(self) -> list:
        target = app.resolve(self.path)
        import os
        entries = []
        for entry in os.listdir(target):
            full = os.path.join(target, entry)
            entries.append({
                'name': entry,
                'path': os.path.join(self.path, entry),
                'is_dir': os.path.isdir(full),
            })
        return entries


class GlobSearch(BaseCommand):
    """Glob search in workspace."""
    group: ClassVar[str] = 'basic'
    read_only: ClassVar[bool] = True

    pattern: str = '*'
    path: str = '.'

    async def __call__(self) -> list:
        base = app.resolve(self.path)
        matches = globmod.glob(os.path.join(base, self.pattern), recursive=True)
        return [m.replace(base + '/', '').replace(base + os.sep, '') for m in matches]


class GrepSearch(BaseCommand):
    """Search file contents by regex."""
    group: ClassVar[str] = 'basic'
    read_only: ClassVar[bool] = True

    pattern: str = ''
    path: str = '.'
    glob: str = '**/*'
    max_results: int = 50

    async def __call__(self) -> list:
        import os
        base = app.resolve(self.path)
        regex = re.compile(self.pattern)
        results = []
        for root, _, files in os.walk(base):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, encoding='utf-8', errors='replace') as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                rel = fpath.replace(base + os.sep, '')
                                results.append({'path': rel, 'line': i, 'text': line.strip()})
                                if len(results) >= self.max_results:
                                    return results
                except (OSError, UnicodeDecodeError):
                    continue
        return results


class EditPath(BaseCommand):
    """Edit file by replacing text."""
    group: ClassVar[str] = 'edit'

    path: str = ''
    old: str = ''
    new: str = ''
    replace_all: bool = False

    async def __call__(self) -> dict:
        target = app.resolve(self.path)
        with open(target, encoding='utf-8') as f:
            content = f.read()
        if self.replace_all:
            updated = content.replace(self.old, self.new)
            count = content.count(self.old)
        else:
            updated = content.replace(self.old, self.new, 1)
            count = 1 if self.old in content else 0
        with open(target, 'w', encoding='utf-8') as f:
            f.write(updated)
        return {'path': self.path, 'replacements': count}


class RunCommand(BaseCommand):
    """Run shell command in sandbox."""
    group: ClassVar[str] = 'exec'
    concurrency_safe: ClassVar[bool] = False

    command: str = ''
    cwd: str = '.'
    env: dict = {}
    timeout: int = 0

    async def __call__(self):
        if not app.policy(self.command):
            yield await chunk('error', payload={'code': 'denied', 'message': 'command denied', 'retryable': False})
            return
        timeout = self.timeout or app.exec_timeout
        files = []
        async for item in app.protocol.exec_stream(self.command, self.cwd, self.env, timeout):
            if 'exit_code' in item:
                yield await chunk('exec.completed', payload={'exit_code': item['exit_code'], 'files': files, 'ms': 0})
            else:
                yield await chunk('exec.output', payload={'stream': item.get('stream', 'stdout'), 'text': item.get('text', '')})


class RunPython(BaseCommand):
    """Execute Python code in sandbox."""
    group: ClassVar[str] = 'exec'
    concurrency_safe: ClassVar[bool] = False

    code: str = ''
    timeout: int = 60
    artifacts: list = []

    async def __call__(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.py', delete=False, mode='w') as f:
            f.write(self.code)
            path = f.name
        cmd = f'python {path}'
        async for item in app.protocol.exec_stream(cmd, '.', {}, self.timeout):
            if 'exit_code' in item:
                yield await chunk('exec.completed', payload={'exit_code': item['exit_code'], 'files': self.artifacts, 'ms': 0})
            else:
                yield await chunk('exec.output', payload={'stream': item.get('stream', 'stdout'), 'text': item.get('text', '')})


class StoreArtifact(BaseCommand):
    """Store large output as artifact."""

    key: str = ''
    content: str = ''
    mime: str = 'text/plain'
    session_id: str = ''

    async def __call__(self) -> str:
        path = app.artifact_path(self.session_id, self.key)
        __import__('pathlib').Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.content)
        ref = app.artifact_ref(self.session_id, self.key)
        await hub.emit(app.event('ArtifactStored', ref=ref, bytes=len(self.content.encode()), mime=self.mime))
        return ref


class ReadArtifact(BaseCommand):
    """Read stored artifact."""
    group: ClassVar[str] = 'basic'
    read_only: ClassVar[bool] = True

    ref: str = ''
    offset: int = 0
    limit: int = 0

    async def __call__(self) -> dict:
        path = app.ref_to_path(self.ref)
        with open(path, encoding='utf-8', errors='replace') as f:
            content = f.read()
        if self.offset or self.limit:
            lines = content.splitlines()
            end = self.offset + self.limit if self.limit else None
            content = '\n'.join(lines[self.offset:end])
        return {'ref': self.ref, 'content': content}


class GrepArtifact(BaseCommand):
    """Search within artifact."""
    group: ClassVar[str] = 'basic'
    read_only: ClassVar[bool] = True

    ref: str = ''
    pattern: str = ''
    max_results: int = 50

    async def __call__(self) -> dict:
        path = app.ref_to_path(self.ref)
        regex = re.compile(self.pattern)
        matches = []
        with open(path, encoding='utf-8', errors='replace') as f:
            for i, line in enumerate(f, 1):
                if regex.search(line):
                    matches.append({'line': i, 'text': line.strip()})
                    if len(matches) >= self.max_results:
                        break
        return {'ref': self.ref, 'matches': matches}


class ArtifactStored(BaseEvent):
    ref: str = ''
    bytes: int = 0
    mime: str = ''


import os  # noqa: E402 — used by GlobSearch/GrepSearch
