"""Small bounded in-memory message relay. No LLM, shell, or external calls."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import secrets
import threading
import time

MAX_WAIT = 20
MAX_ROOMS = 16
MAX_MESSAGES = 64
MAX_MESSAGE_BYTES = 2048
ROOM_IDLE_TTL = 900
ROOM_ID = re.compile(r"[0-9a-f]{32}\Z")


@dataclass
class Room:
    last_active: float
    messages: list[dict] = field(default_factory=list)
    closed: bool = False


class Mailbox:
    """One-process bounded mailbox; callers must use a separately authenticated MCP edge."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.cv = threading.Condition()
        self.rooms: dict[str, Room] = {}

    def _prune(self):
        now = self.clock()
        for key, room in list(self.rooms.items()):
            if now - room.last_active >= ROOM_IDLE_TTL:
                del self.rooms[key]

    def _get(self, room_id):
        if type(room_id) is not str or ROOM_ID.fullmatch(room_id) is None:
            raise ValueError("invalid-room-id")
        self._prune()
        room = self.rooms.get(room_id)
        if room is None:
            raise ValueError("unknown-or-expired-room")
        return room

    @staticmethod
    def _role(role):
        if role not in ("A", "B") or type(role) is not str:
            raise ValueError("invalid-role")

    def create_room(self) -> dict:
        with self.cv:
            self._prune()
            if len(self.rooms) >= MAX_ROOMS:
                raise OverflowError("room-limit")
            ident = secrets.token_hex(16)
            self.rooms[ident] = Room(last_active=self.clock())
            return {"status": "created", "room_id": ident, "ttl_seconds": ROOM_IDLE_TTL}

    def send(self, room_id: str, sender: str, message: str, reply_to: int | None = None) -> dict:
        self._role(sender)
        if type(message) is not str or not message.strip():
            raise ValueError("invalid-message")
        if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("message-too-large")
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in message):
            raise ValueError("invalid-message")
        if reply_to is not None and (type(reply_to) is not int or reply_to <= 0):
            raise ValueError("invalid-reply-id")
        with self.cv:
            room = self._get(room_id)
            if room.closed:
                raise ValueError("room-closed")
            if len(room.messages) >= MAX_MESSAGES:
                raise OverflowError("message-limit")
            if reply_to is not None and not any(
                m["id"] == reply_to and m["from"] != sender for m in room.messages
            ):
                raise ValueError("unknown-or-self-reply")
            mid = len(room.messages) + 1
            room.messages.append({"id": mid, "from": sender, "to": "B" if sender == "A" else "A",
                                  "message": message, "reply_to": reply_to})
            room.last_active = self.clock()
            self.cv.notify_all()
            return {"status": "sent", "id": mid, "to": "B" if sender == "A" else "A"}

    def wait(self, room_id: str, role: str, after_id: int, wait_seconds: int = 15) -> dict:
        self._role(role)
        if type(after_id) is not int or after_id < 0:
            raise ValueError("invalid-cursor")
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= MAX_WAIT:
            raise ValueError("invalid-wait")
        with self.cv:
            room = self._get(room_id)
            deadline = self.clock() + wait_seconds
            while True:
                # Non-destructive cursor means that a message arriving before wait is not lost.
                for m in room.messages:
                    if m["id"] > after_id and m["to"] == role:
                        room.last_active = self.clock()
                        return {"status": "message", **m}
                if room.closed:
                    return {"status": "closed"}
                remaining = deadline - self.clock()
                if remaining <= 0:
                    return {"status": "timeout", "after_id": after_id}
                self.cv.wait(min(remaining, 1))

    def close(self, room_id: str) -> dict:
        with self.cv:
            room = self._get(room_id)
            room.closed = True
            room.last_active = self.clock()
            self.cv.notify_all()
            return {"status": "closed"}
