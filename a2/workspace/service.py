"""WorkspaceService — sandboxed file/command execution."""

from __future__ import annotations

import os
from pathlib import Path

from a2.kernel import A2Service


class WorkspaceService(A2Service):
    domain = 'workspace'
    commands = ['commands']

    root: str = '.a2/workspace'
    allow_commands: list = []
    deny_commands: list = ['rm -rf /', 'shutdown', 'mkfs']
    exec_timeout: int = 120

    def resolve(self, path: str) -> str:
        base = Path(self.root).resolve()
        target = (base / path).resolve()
        if not str(target).startswith(str(base)):
            raise PermissionError(f'path escapes sandbox: {path}')
        return str(target)

    def policy(self, command: str) -> bool:
        for denied in self.deny_commands:
            if denied in command:
                return False
        if self.allow_commands:
            return any(allowed in command for allowed in self.allow_commands)
        return True

    def artifact_path(self, session_id: str, key: str) -> str:
        return os.path.join(self.root, 'artifacts', session_id, key)

    def artifact_ref(self, session_id: str, key: str) -> str:
        return f'artifact://{session_id}/{key}'

    def ref_to_path(self, ref: str) -> str:
        if not ref.startswith('artifact://'):
            raise ValueError(f'invalid artifact ref: {ref}')
        parts = ref.replace('artifact://', '').split('/', 1)
        if len(parts) != 2:
            raise ValueError(f'invalid artifact ref: {ref}')
        return self.artifact_path(parts[0], parts[1])
