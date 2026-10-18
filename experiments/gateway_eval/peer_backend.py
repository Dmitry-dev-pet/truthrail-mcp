"""Ephemeral shared-state Peer Chat MCP behind an authenticated gateway.

This isolated test server owns NO model/API keys, accounts, shell or file tools.
CI binds it to loopback. In production only a reviewed private ingress may
reach this service: room tickets do not replace client authorization.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
from hmac import compare_digest
import re
import secrets
import threading
import time

from mcp.server import MCPServer

ROOM_RE = re.compile(r"[0-9a-f]{32}\Z")
TTL = 300
MAX_ROOMS = 8
MAX_MESSAGES = 64
MAX_TEXT_BYTES = 1024
MAX_WAIT = 10


class Denied(Exception):
    pass


@dataclass
class Room:
    expires: float
    tickets: dict[str, tuple[str, bool]] = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    joined: bool = False
    closed: bool = False


class PeerMailbox:
    """One process, one shared mailbox, non-destructive cursor reads."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.cv = threading.Condition()
        self.rooms: dict[str, Room] = {}

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def ticket() -> str:
        return secrets.token_urlsafe(32)

    def prune(self) -> None:
        for room_id, room in list(self.rooms.items()):
            if room.expires <= self.clock():
                del self.rooms[room_id]

    def room(self, room_id: str) -> Room:
        if type(room_id) is not str or ROOM_RE.fullmatch(room_id) is None:
            raise Denied("forbidden")
        self.prune()
        room = self.rooms.get(room_id)
        if room is None or room.closed:
            raise Denied("forbidden")
        return room

    def role(self, room: Room, token: str, invite: bool = False) -> str:
        if type(token) is not str or not 32 <= len(token) <= 128:
            raise Denied("forbidden")
        target = self.digest(token)
        for stored, (role, is_invite) in room.tickets.items():
            if compare_digest(stored, target) and is_invite == invite:
                return role
        raise Denied("forbidden")

    def create(self) -> dict:
        with self.cv:
            self.prune()
            if len(self.rooms) >= MAX_ROOMS:
                raise Denied("capacity-reached")
            rid = secrets.token_hex(16)
            a, invite = self.ticket(), self.ticket()
            room = Room(self.clock() + TTL)
            room.tickets[self.digest(a)] = ("A", False)
            room.tickets[self.digest(invite)] = ("B", True)
            self.rooms[rid] = room
            return {"room_id": rid, "a_ticket": a, "b_invite": invite,
                    "ttl_seconds": TTL}

    def join(self, rid: str, invite: str) -> dict:
        with self.cv:
            room = self.room(rid)
            if room.joined or self.role(room, invite, True) != "B":
                raise Denied("forbidden")
            b = self.ticket()
            del room.tickets[self.digest(invite)]
            room.tickets[self.digest(b)] = ("B", False)
            room.joined = True
            self.cv.notify_all()
            return {"role": "B", "b_ticket": b}

    def send(self, rid: str, token: str, message: str,
             reply_to: int | None = None) -> dict:
        if (type(message) is not str or not message.strip()
                or len(message.encode("utf-8")) > MAX_TEXT_BYTES
                or any(ord(ch) < 32 and ch not in "\t\n" for ch in message)):
            raise Denied("invalid-message")
        if reply_to is not None and (type(reply_to) is not int or reply_to < 1):
            raise Denied("invalid-reply")
        with self.cv:
            room = self.room(rid)
            role = self.role(room, token)
            if len(room.messages) >= MAX_MESSAGES:
                raise Denied("capacity-reached")
            if reply_to is not None and not any(
                m["id"] == reply_to and m["from"] != role
                for m in room.messages
            ):
                raise Denied("invalid-reply")
            mid = len(room.messages) + 1
            event = {"id": mid, "from": role,
                     "to": "B" if role == "A" else "A",
                     "reply_to": reply_to, "message": message}
            room.messages.append(event)
            self.cv.notify_all()
            return {"status": "sent", "id": mid}

    def wait(self, rid: str, token: str, after_id: int,
             wait_seconds: int = 5) -> dict:
        if type(after_id) is not int or after_id < 0:
            raise Denied("invalid-cursor")
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= MAX_WAIT:
            raise Denied("invalid-wait")
        with self.cv:
            deadline = self.clock() + wait_seconds
            while True:
                room = self.room(rid)
                role = self.role(room, token)
                for event in room.messages:
                    if event["to"] == role and event["id"] > after_id:
                        return {"status": "message", **event}
                remaining = min(deadline, room.expires) - self.clock()
                if remaining <= 0:
                    return {"status": "timeout", "after_id": after_id}
                self.cv.wait(min(remaining, 0.5))

    def status(self, rid: str, token: str) -> dict:
        with self.cv:
            room = self.room(rid)
            return {"role": self.role(room, token), "message_count": len(room.messages),
                    "joined": room.joined}

    def close(self, rid: str, token: str) -> dict:
        with self.cv:
            room = self.room(rid)
            if self.role(room, token) != "A":
                raise Denied("forbidden")
            room.closed = True
            room.tickets.clear()
            self.cv.notify_all()
            return {"status": "closed"}


mailbox = PeerMailbox()
server = MCPServer("Minimal shared-state Peer Chat fixture")


def guarded(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except Denied as exc:
        return {"status": "denied", "reason": str(exc)}
    except Exception:
        return {"status": "error", "reason": "invalid-request"}


@server.tool()
async def chat_create() -> dict:
    """Create a temporary room and short-lived A ticket plus one-use B invitation."""
    return guarded(mailbox.create)


@server.tool()
async def chat_join(room_id: str, invitation: str) -> dict:
    """Redeem B's invitation exactly once to receive a B-specific ticket."""
    return guarded(mailbox.join, room_id, invitation)


@server.tool()
async def chat_send(room_id: str, ticket: str, message: str,
                    reply_to: int | None = None) -> dict:
    """Send inert text in a single room, with role derived from the ticket."""
    return guarded(mailbox.send, room_id, ticket, message, reply_to)


@server.tool()
async def chat_wait(room_id: str, ticket: str, after_id: int = 0,
                    wait_seconds: int = 5) -> dict:
    """Block up to ten seconds for a message intended for this room ticket."""
    return await asyncio.to_thread(
        guarded, mailbox.wait, room_id, ticket, after_id, wait_seconds
    )


@server.tool()
async def chat_status(room_id: str, ticket: str) -> dict:
    """Return only bounded metadata for an authorized room ticket."""
    return guarded(mailbox.status, room_id, ticket)


@server.tool()
async def chat_close(room_id: str, ticket: str) -> dict:
    """Close the room using the original A ticket."""
    return guarded(mailbox.close, room_id, ticket)


# Only for the explicit off-VPS owner-versus-external trust-boundary test:
# an inert extra tool represents a reviewed owner-only Alfred capability.
# Not part of the default Peer Chat server / packed release.
if __import__("os").environ.get("ALFRED_OWNER_ONLY_FIXTURE") == "1":
    @server.tool()
    async def owner_marker() -> dict:
        """Inert synthetic marker for verifying owner-only tool protection."""
        return {"synthetic": True, "marker": "owner-catalog-fixture-only"}


if __name__ == "__main__":
    server.run(
        transport="streamable-http", host="127.0.0.1", port=18765,
        stateless_http=True, json_response=True, max_request_body_size=8192,
    )
