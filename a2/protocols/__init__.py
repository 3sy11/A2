"""A2 Protocol ABCs — extensions of bollydog Protocol (D-10)."""

from a2.protocols.model import ChatModelProtocol, EmbeddingProtocol, TTSProtocol
from a2.protocols.vector import VectorStoreProtocol
from a2.protocols.sandbox import SandboxProtocol
from a2.protocols.mcp import McpProtocol
from a2.protocols.permission import PermissionProtocol

__all__ = [
    'ChatModelProtocol',
    'EmbeddingProtocol',
    'TTSProtocol',
    'VectorStoreProtocol',
    'SandboxProtocol',
    'McpProtocol',
    'PermissionProtocol',
]
