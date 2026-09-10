"""CredentialService — secure credential vault."""

from __future__ import annotations

import base64
import hashlib
import json
import os

from a2.kernel import A2Service


class CredentialService(A2Service):
    domain = 'credential'
    commands = ['commands']

    secret_env: str = 'A2_CREDENTIAL_KEY'

    def _key(self) -> bytes:
        secret = os.environ.get(self.secret_env, 'a2-dev-key-change-me')
        return hashlib.sha256(secret.encode()).digest()

    def encrypt(self, payload: dict) -> str:
        data = json.dumps(payload).encode()
        key = self._key()
        xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        return base64.b64encode(xored).decode()

    def decrypt(self, blob: str) -> dict:
        key = self._key()
        data = base64.b64decode(blob.encode())
        plain = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        return json.loads(plain.decode())

    def mask(self, cred: dict) -> dict:
        return {**cred, 'payload': {'masked': True}}

    def inject(self, headers: dict, cred: dict) -> dict:
        kind = cred.get('kind', 'apikey')
        payload = cred.get('payload', {})
        result = dict(headers)
        if kind == 'bearer':
            result['Authorization'] = f'Bearer {payload.get("token", "")}'
        elif kind == 'apikey':
            result['X-API-Key'] = payload.get('key', '')
        return result

    def inject_model(self, provider: str) -> dict:
        return {}
