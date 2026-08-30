"""Credential domain models."""

from bollydog.models.base import BaseDomain


class Credential(BaseDomain):
    cred_id: str = ''
    user_id: str = ''
    system: str = ''
    kind: str = 'apikey'
    payload: dict = {}
    expires_at: float = 0.0
