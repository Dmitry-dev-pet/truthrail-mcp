"""An isolated, *non-production* model for scoped grants at a trusted MCP ingress.

OAuth/JWT verification belongs to the ingress, not to a model-callable tool.
This in-memory store is a test fixture for introspection, rotation, and revoke.
Never expose issue/revoke as MCP tools or put bearer tokens in tool arguments.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets
import threading
import time

AUDIENCE = "alfred:mcp"
SCOPES = frozenset({
    "capabilities:list", "chat:room:create", "chat:room:join",
    "chat:room:read", "chat:room:send", "chat:room:wait",
    "chat:room:close", "status:read",
})
SAFE_ID = re.compile(r"[a-zA-Z0-9:_-]{1,128}\Z")


class Denied(Exception):
    """Fail closed without revealing whether a token, room, or resource exists."""


@dataclass(frozen=True)
class Principal:
    client_id: str
    audience: str
    scopes: frozenset[str]
    expires_at: float
    resource: str | None = None

    def allows(self, scope: str, resource: str | None, now: float) -> bool:
        return (
            now < self.expires_at and self.audience == AUDIENCE
            and scope in self.scopes
            and (self.resource is None or self.resource == resource)
        )


class ScopedTokenVault:
    """Ephemeral demo ledger; must be replaced by a reviewed OAuth/Bearer edge.

    Only trusted operator-side code can call issue/revoke. The returned raw token
    is never persisted here, never included in MCP tool output and never logged.
    """

    def __init__(self, clock=time.time):
        self.clock = clock
        self._lock = threading.RLock()
        self._grants: dict[str, Principal] = {}

    def issue(self, client_id: str, scopes, *, ttl_seconds: int = 600,
              audience: str = AUDIENCE, resource: str | None = None) -> str:
        if type(client_id) is not str or not SAFE_ID.fullmatch(client_id):
            raise ValueError("invalid-client-id")
        scopes = frozenset(scopes)
        if not scopes or not scopes.issubset(SCOPES):
            raise ValueError("invalid-scope")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("invalid-ttl")
        if audience != AUDIENCE:
            raise ValueError("invalid-audience")
        if resource is not None and (
            type(resource) is not str or not SAFE_ID.fullmatch(resource)
        ):
            raise ValueError("invalid-resource")
        with self._lock:
            if len(self._grants) >= 128:
                raise OverflowError("token-ledger-full")
            raw = secrets.token_urlsafe(32)
            digest = hashlib.sha256(raw.encode("ascii")).hexdigest()
            self._grants[digest] = Principal(
                client_id, audience, scopes, self.clock() + ttl_seconds, resource
            )
            return raw

    def authenticate(self, bearer: str, audience: str = AUDIENCE) -> Principal:
        if type(bearer) is not str or not 32 <= len(bearer) <= 128:
            raise Denied("unauthorized")
        digest = hashlib.sha256(bearer.encode("utf-8")).hexdigest()
        with self._lock:
            for stored, principal in self._grants.items():
                if hmac.compare_digest(stored, digest):
                    if principal.audience != audience or principal.expires_at <= self.clock():
                        break
                    return principal
        raise Denied("unauthorized")

    def revoke(self, bearer: str) -> None:
        if type(bearer) is not str:
            raise ValueError("invalid-token")
        digest = hashlib.sha256(bearer.encode("utf-8")).hexdigest()
        with self._lock:
            self._grants.pop(digest, None)
