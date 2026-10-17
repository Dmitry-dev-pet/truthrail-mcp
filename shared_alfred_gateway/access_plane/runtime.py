"""Stable, scope-checked facade; no model-provided identity or module execution.

Callers MUST be authenticated at the trusted ingress on every call. This core
does not implement OAuth HTTP middleware, issue production tokens, or authorize
VPS mutations. No Python plugins / URLs / shell are loaded from the registry.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable, Mapping

from .chat import PeerRooms
from .identity import AUDIENCE, Denied, Principal
from .registry import ModuleRegistry, TrustedPolicy


class AccessPlane:
    def __init__(
        self, registry: ModuleRegistry, policy: TrustedPolicy,
        *, rooms: PeerRooms | None = None,
        delegates: Mapping[str, Callable[[str, dict], Awaitable[dict]]] | None = None,
        clock=time.time,
    ):
        self.registry = registry
        self.policy = policy
        self.rooms = rooms if rooms is not None else PeerRooms()
        self.delegates = dict(delegates or {})
        self.clock = clock

    def _policy(self) -> TrustedPolicy:
        # A trusted loader may refresh a separately secured policy snapshot
        # without allowing a capability module to assign its own scopes.
        obj = self.policy() if callable(self.policy) else self.policy
        if not isinstance(obj, TrustedPolicy):
            raise Denied("invalid-policy")
        return obj

    def _caller(self, principal: Principal, scope: str = "capabilities:list") -> None:
        if not isinstance(principal, Principal) or not principal.allows(
            scope, None, self.clock()
        ):
            raise Denied("forbidden")

    def list(self, principal: Principal) -> dict:
        self._caller(principal)
        version, modules = self.registry.snapshot()
        visible = []
        policy = self._policy()
        for name, module in sorted(modules.items()):
            actions = sorted({
                action for (cname, action), rule in policy.rules.items()
                if cname == name and rule.adapter == module.adapter
                and not rule.requires_operator_approval
                and principal.allows(rule.scope, None, self.clock())
            })
            if actions:
                visible.append({"name": name, "version": module.version, "actions": actions})
        return {"revision": version, "capabilities": visible}

    async def invoke(self, principal: Principal, capability: str, action: str,
                     effect: str, parameters: dict,
                     renew: Callable[[], Principal] | None = None) -> dict:
        try:
            valid = (type(parameters) is dict
                     and len(json.dumps(parameters, ensure_ascii=False).encode("utf-8")) <= 8192)
        except (ValueError, TypeError, OverflowError):
            valid = False
        if not valid:
            raise Denied("invalid-arguments")
        module = self.registry.require(capability)
        resource = parameters.get("room_id")
        if resource is not None and type(resource) is not str:
            raise Denied("invalid-resource")
        self._policy().check(principal, module, action, effect, resource)
        if module.adapter == "builtin:peer_chat":
            try:
                if effect == "write" and action == "create":
                    if parameters:
                        raise Denied("invalid-arguments")
                    return self.rooms.create()
                if effect == "write" and action == "join":
                    if set(parameters) != {"room_id", "invite"}:
                        raise Denied("invalid-arguments")
                    return self.rooms.join(parameters["room_id"], parameters["invite"])
                if effect == "write" and action == "send":
                    if set(parameters) - {"room_id", "ticket", "message", "reply_to"} or not {
                        "room_id", "ticket", "message"
                    } <= set(parameters):
                        raise Denied("invalid-arguments")
                    return self.rooms.send(
                        parameters["room_id"], parameters["ticket"],
                        parameters["message"], parameters.get("reply_to")
                    )
                if effect == "write" and action == "close":
                    if set(parameters) != {"room_id", "ticket"}:
                        raise Denied("invalid-arguments")
                    return self.rooms.close(parameters["room_id"], parameters["ticket"])
                if effect == "read" and action == "status":
                    if set(parameters) != {"room_id", "ticket"}:
                        raise Denied("invalid-arguments")
                    return self.rooms.status(parameters["room_id"], parameters["ticket"])
                if effect == "wait" and action == "next":
                    if set(parameters) - {"room_id", "ticket", "after_id", "wait_seconds"} or not {
                        "room_id", "ticket", "after_id"
                    } <= set(parameters):
                        raise Denied("invalid-arguments")
                    answer = await asyncio.to_thread(
                        self.rooms.wait, parameters["room_id"], parameters["ticket"],
                        parameters["after_id"], parameters.get("wait_seconds", 15)
                    )
                    # Caller can be revoked/expire while the tool is blocked.
                    self._policy().check(renew() if renew is not None else principal,
                                         module, action, effect, resource)
                    return answer
                raise Denied("unknown-action")
            except (ValueError, TypeError, KeyError) as exc:
                raise Denied("invalid-arguments") from exc

        if module.adapter.startswith("delegate:"):
            key = module.adapter.removeprefix("delegate:")
            handler = self.delegates.get(key)
            if handler is None:
                raise Denied("delegate-unavailable")
            # The authorized delegate is injected by a trusted host, not a
            # module path or an Internet URL from untrusted client input.
            try:
                response = await asyncio.wait_for(handler(action, parameters), timeout=15)
                valid_response = (
                    type(response) is dict
                    and len(json.dumps(response, ensure_ascii=False).encode("utf-8")) <= 16384
                )
            except Exception as exc:
                raise Denied("delegate-unavailable") from exc
            if not valid_response:
                raise Denied("invalid-delegate-response")
            self._policy().check(renew() if renew is not None else principal,
                                 module, action, effect, resource)
            return response
        raise Denied("unknown-adapter")
