"""Short-lived A/B mailbox with one-use B invite and role-bound hashed tickets.

Room tickets are *resource capabilities*, not OAuth credentials or verified
individual ChatGPT identities. They can be visible in chat/tool traces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hmac import compare_digest
import hashlib
import re
import secrets
import threading
import time

from .identity import Denied

ROOM_RE = re.compile(r"[a-f0-9]{32}\Z")
MAX_WAIT = 20
MAX_MESSAGES = 64
MAX_ROOMS = 16
MAX_MESSAGE_BYTES = 2048
TTL = 600


@dataclass
class Room:
    created: float
    expires: float
    tickets: dict[str, tuple[str, bool]] = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    closed: bool = False
    joined: bool = False


class PeerRooms:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.cv = threading.Condition()
        self.rooms: dict[str, Room] = {}

    @staticmethod
    def _ticket() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _digest(ticket: str) -> str:
        return hashlib.sha256(ticket.encode("utf-8")).hexdigest()

    def _prune(self):
        now = self.clock()
        for key, room in list(self.rooms.items()):
            if room.expires <= now:
                del self.rooms[key]

    def _room(self, name: str) -> Room:
        if type(name) is not str or ROOM_RE.fullmatch(name) is None:
            raise Denied("forbidden")
        self._prune()
        obj = self.rooms.get(name)
        if obj is None or obj.closed:
            raise Denied("forbidden")
        return obj

    def _role(self, room: Room, ticket: str, *, invite=False) -> str:
        if type(ticket) is not str or not 32 <= len(ticket) <= 128:
            raise Denied("forbidden")
        target = self._digest(ticket)
        for stored, (role, is_invite) in room.tickets.items():
            if compare_digest(target, stored):
                if bool(is_invite) != bool(invite):
                    break
                return role
        raise Denied("forbidden")

    def create(self) -> dict:
        with self.cv:
            self._prune()
            if len(self.rooms) >= MAX_ROOMS:
                raise Denied("capacity-reached")
            room_id = secrets.token_hex(16)
            a, invite = self._ticket(), self._ticket()
            room = Room(self.clock(), self.clock() + TTL)
            room.tickets[self._digest(a)] = ("A", False)
            room.tickets[self._digest(invite)] = ("B", True)
            self.rooms[room_id] = room
            return {"room_id": room_id, "a_ticket": a, "b_invite": invite,
                    "ttl_seconds": TTL}

    def join(self, room_id: str, invite: str) -> dict:
        with self.cv:
            room = self._room(room_id)
            self._role(room, invite, invite=True)
            if room.joined:
                raise Denied("forbidden")
            ticket = self._ticket()
            room.tickets.pop(self._digest(invite), None)
            room.tickets[self._digest(ticket)] = ("B", False)
            room.joined = True
            self.cv.notify_all()
            return {"role": "B", "b_ticket": ticket}

    def send(self, room_id: str, ticket: str, text: str,
             reply_to: int | None = None) -> dict:
        if type(text) is not str or not text.strip() or len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise Denied("invalid-message")
        if any(ord(c) < 32 and c not in "\t\n" for c in text):
            raise Denied("invalid-message")
        if reply_to is not None and (type(reply_to) is not int or reply_to < 1):
            raise Denied("invalid-reply-to")
        with self.cv:
            room = self._room(room_id)
            role = self._role(room, ticket)
            if len(room.messages) >= MAX_MESSAGES:
                raise Denied("capacity-reached")
            if reply_to is not None and not any(
                m["id"] == reply_to and m["from"] != role for m in room.messages
            ):
                raise Denied("invalid-reply-to")
            ident = len(room.messages) + 1
            event = {"id": ident, "from": role, "to": "B" if role == "A" else "A",
                     "message": text, "reply_to": reply_to}
            room.messages.append(event)
            self.cv.notify_all()
            return {"id": ident, "status": "sent"}

    def wait(self, room_id: str, ticket: str, after_id: int,
             seconds: int = 15) -> dict:
        if type(after_id) is not int or after_id < 0:
            raise Denied("invalid-cursor")
        if type(seconds) is not int or not 0 <= seconds <= MAX_WAIT:
            raise Denied("invalid-wait")
        with self.cv:
            deadline = self.clock() + seconds
            while True:
                room = self._room(room_id)
                role = self._role(room, ticket)
                for msg in room.messages:
                    if msg["to"] == role and msg["id"] > after_id:
                        return {"status": "message", **msg}
                remaining = min(deadline, room.expires) - self.clock()
                if remaining <= 0:
                    return {"status": "timeout", "after_id": after_id}
                self.cv.wait(min(remaining, 1))

    def status(self, room_id: str, ticket: str) -> dict:
        with self.cv:
            room = self._room(room_id)
            role = self._role(room, ticket)
            return {"role": role, "messages_count": len(room.messages),
                    "joined": room.joined}

    def close(self, room_id: str, ticket: str) -> dict:
        with self.cv:
            room = self._room(room_id)
            if self._role(room, ticket) != "A":
                raise Denied("forbidden")
            room.closed = True
            room.tickets.clear()
            self.cv.notify_all()
            return {"status": "closed"}
