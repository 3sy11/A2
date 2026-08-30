"""Session domain commands."""

from __future__ import annotations

import time
import uuid

from bollydog.globals import app
from bollydog.models.base import BaseCommand


class OpenSession(BaseCommand):
    """Open or create a session."""

    session_id: str = ''
    user_id: str = ''
    agent: str = ''

    async def __call__(self) -> dict:
        sid = self.session_id or uuid.uuid4().hex
        key = app.session_key(sid)
        existing = await app.protocol.get(key)
        if existing:
            parks = await app.protocol.get(app.park_key(sid))
            return {**existing, 'resumable': bool(parks)}
        record = app.new_record(sid, self.user_id, self.agent)
        record['title'] = 'New conversation'
        await app.protocol.set(key, record)
        return {**record, 'resumable': False}


class SaveTurn(BaseCommand):
    """Persist a conversation turn."""

    session_id: str = ''
    turn_id: str = ''
    record: dict = {}

    async def __call__(self) -> int:
        key = app.turn_key(self.session_id, self.turn_id)
        await app.protocol.set(key, self.record)
        session_key = app.session_key(self.session_id)
        session_rec = await app.protocol.get(session_key) or {}
        session_rec['turn_count'] = session_rec.get('turn_count', 0) + 1
        session_rec['updated_at'] = time.time()
        if not session_rec.get('title'):
            session_rec['title'] = app.title_of(self.record.get('inputs', []))
        await app.protocol.set(session_key, session_rec)
        return session_rec['turn_count']


class LoadSession(BaseCommand):
    """Load session history."""

    session_id: str = ''
    last_n: int = 50

    async def __call__(self) -> dict:
        session_key = app.session_key(self.session_id)
        session_rec = await app.protocol.get(session_key) or {}
        prefix = f'turn:{self.session_id}:'
        turns = []
        keys = await app.protocol.keys(prefix) if hasattr(app.protocol, 'keys') else []
        for key in sorted(keys)[-self.last_n :]:
            turn = await app.protocol.get(key)
            if turn:
                turns.append(turn)
        return {'session': session_rec, 'turns': turns}


class ListSessions(BaseCommand):
    """List user sessions."""

    user_id: str = ''
    limit: int = 20
    offset: int = 0

    async def __call__(self) -> list:
        keys = await app.protocol.keys('session:') if hasattr(app.protocol, 'keys') else []
        sessions = []
        for key in keys:
            rec = await app.protocol.get(key)
            if rec and (not self.user_id or rec.get('user_id') == self.user_id):
                sessions.append(rec)
        sessions.sort(key=lambda x: x.get('updated_at', 0), reverse=True)
        return sessions[self.offset : self.offset + self.limit]


class DeleteSession(BaseCommand):
    """Delete a session."""

    session_id: str = ''

    async def __call__(self) -> int:
        await app.protocol.remove(app.session_key(self.session_id))
        return 1


class Park(BaseCommand):
    """Park turn state awaiting human input."""

    session_id: str = ''
    turn_id: str = ''
    kind: str = ''
    pending: dict = {}

    async def __call__(self) -> str:
        park_id = uuid.uuid4().hex[:12]
        state = {
            'park_id': park_id,
            'session_id': self.session_id,
            'turn_id': self.turn_id,
            'kind': self.kind,
            'pending': self.pending,
            'created_at': time.time(),
        }
        key = app.park_key(self.session_id)
        parks = await app.protocol.get(key) or {}
        parks[park_id] = state
        await app.protocol.set(key, parks)
        return park_id


class Unpark(BaseCommand):
    """Retrieve parked state."""

    session_id: str = ''
    park_id: str = ''

    async def __call__(self) -> dict:
        key = app.park_key(self.session_id)
        parks = await app.protocol.get(key) or {}
        state = parks.pop(self.park_id, {})
        await app.protocol.set(key, parks)
        return state


class AppendEvent(BaseCommand):
    """Append streaming event for replay."""

    session_id: str = ''
    turn_id: str = ''
    chunk: dict = {}

    async def __call__(self) -> int:
        seq = self.chunk.get('seq', 0)
        key = app.event_key(self.session_id, seq)
        await app.protocol.set(key, self.chunk)
        return seq


class ReplayEvents(BaseCommand):
    """Replay stored events from a sequence number."""

    session_id: str = ''
    last_seq: int = 0

    async def __call__(self):
        prefix = f'evt:{self.session_id}:'
        keys = await app.protocol.keys(prefix) if hasattr(app.protocol, 'keys') else []
        events = []
        for key in keys:
            seq = int(key.split(':')[-1])
            if seq > self.last_seq:
                events.append((seq, await app.protocol.get(key)))
        for seq, evt in sorted(events):
            yield evt
        yield {'type': 'replay.completed', 'payload': {'last_seq': events[-1][0] if events else self.last_seq}}
