"""Credential domain commands."""

from __future__ import annotations

import time
import uuid

from bollydog.globals import app, hub
from bollydog.models.base import BaseCommand, BaseEvent


class PutCredential(BaseCommand):
    """Store user credential."""

    user_id: str = ''
    system: str = ''
    kind: str = 'apikey'
    payload: dict = {}

    async def __call__(self) -> str:
        cred_id = uuid.uuid4().hex[:12]
        blob = app.encrypt(self.payload)
        record = {
            'cred_id': cred_id,
            'user_id': self.user_id,
            'system': self.system,
            'kind': self.kind,
            'payload': blob,
            'expires_at': 0.0,
        }
        key = f'cred:{self.user_id}:{self.system}'
        await app.protocol.set(key, record)
        return cred_id


class GetCredential(BaseCommand):
    """Retrieve credential (masked)."""

    user_id: str = ''
    system: str = ''

    async def __call__(self) -> dict:
        key = f'cred:{self.user_id}:{self.system}'
        record = await app.protocol.get(key)
        if not record:
            await hub.emit(app.event('CredentialMissing', user_id=self.user_id, system=self.system))
            return {}
        decrypted = app.decrypt(record['payload'])
        return app.mask({**record, 'payload': decrypted})


class ListCredentials(BaseCommand):
    """List user credentials."""

    user_id: str = ''

    async def __call__(self) -> list:
        prefix = f'cred:{self.user_id}:'
        keys = await app.protocol.keys(prefix) if hasattr(app.protocol, 'keys') else []
        result = []
        for key in keys:
            record = await app.protocol.get(key)
            if record:
                result.append(app.mask(record))
        return result


class DeleteCredential(BaseCommand):
    """Delete credential."""

    cred_id: str = ''

    async def __call__(self) -> int:
        keys = await app.protocol.keys('cred:') if hasattr(app.protocol, 'keys') else []
        for key in keys:
            record = await app.protocol.get(key)
            if record and record.get('cred_id') == self.cred_id:
                await app.protocol.remove(key)
                return 1
        return 0


class CredentialMissing(BaseEvent):
    user_id: str = ''
    system: str = ''
