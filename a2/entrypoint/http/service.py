"""HTTP entry service."""

from bollydog.models.service import AppService


class HttpEntryService(AppService):
    domain = 'entry'
    commands = ['commands']
    routers = {
        'Chat': ['SSE', '/api/chat/stream'],
    }
