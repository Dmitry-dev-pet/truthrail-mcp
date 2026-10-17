"""Source-only security tests for the stable scoped MCP facade.

Never touches VPS, Cloudflare, the native tunnel, private tokens, or a Mac.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from shared_alfred_gateway.access_plane.identity import (
    AUDIENCE, Denied, Principal, ScopedTokenVault,
)
from shared_alfred_gateway.access_plane.chat import PeerRooms
from shared_alfred_gateway.access_plane.registry import ModuleRegistry
from shared_alfred_gateway.access_plane.policy_files import TrustedPolicyFiles
from shared_alfred_gateway.access_plane.runtime import AccessPlane
from shared_alfred_gateway.access_plane.mcp_bridge import register_access_tools

CHAT = [
    ("create", "chat:room:create", "write"),
    ("join", "chat:room:join", "write"),
    ("send", "chat:room:send", "write"),
    ("next", "chat:room:wait", "wait"),
    ("status", "chat:room:read", "read"),
    ("close", "chat:room:close", "write"),
]


def module_spec(name="peer_chat", version="1", adapter="builtin:peer_chat"):
    return {"name": name, "version": version, "adapter": adapter}


def rule(cap, action, scope, effect, adapter, operator_gate=False):
    return {"capability": cap, "action": action, "scope": scope, "effect": effect,
            "adapter": adapter, "operator_gate": operator_gate}


def atomic_snapshot(root: Path, payload: dict):
    """Emulate a trusted operator's atomic deployment. Never called by MCP tools."""
    (root / "releases").mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    (root / "releases" / (digest + ".json")).write_bytes(raw)
    pending = root / "active.next"
    pending.write_text(json.dumps({"sha256": digest}), encoding="utf-8")
    os.replace(pending, root / "active.json")
    return digest


def unpack(reply):
    assert not reply.is_error, reply
    result = getattr(reply, "structured_content", None)
    if result is None:
        chunks = [part.text for part in reply.content
                  if getattr(part, "type", None) == "text"]
        assert len(chunks) == 1
        result = json.loads(chunks[0])
    if type(result) is dict and type(result.get("result")) is dict:
        result = result["result"]
    assert type(result) is dict
    return result


class AccessPlaneFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = [1000.0]
        self.now = lambda: self.clock[0]
        self.module_root = Path(self.temp.name) / "modules"
        self.policy_root = Path(self.temp.name) / "operator_policy"
        self.modules = [module_spec()]
        self.rules = [
            rule("peer_chat", action, scope, effect, "builtin:peer_chat")
            for action, scope, effect in CHAT
        ]
        self._write_modules()
        self._write_policy()
        self.vault = ScopedTokenVault(clock=self.now)
        self.rooms = PeerRooms(clock=self.now)

        async def delegate_status(action, parameters):
            return {"source": "approved_delegate", "action": action,
                    "resource": parameters.get("resource_id", "default")}

        self.plane = AccessPlane(
            ModuleRegistry(self.module_root),
            TrustedPolicyFiles(self.policy_root, self.now),
            rooms=self.rooms,
            delegates={"status": delegate_status},
            clock=self.now,
        )
        self.a_scopes = {
            "capabilities:list", "chat:room:create", "chat:room:send",
            "chat:room:wait", "chat:room:read", "chat:room:close"
        }
        self.b_scopes = {
            "capabilities:list", "chat:room:join", "chat:room:send",
            "chat:room:wait", "chat:room:read"
        }

    def _write_modules(self):
        return atomic_snapshot(self.module_root, {"schema": 1, "modules": self.modules})

    def _write_policy(self):
        return atomic_snapshot(self.policy_root, {"schema": 1, "rules": self.rules})

    def identity(self, scopes, *, client_id="test-client", ttl=120, resource=None):
        raw = self.vault.issue(client_id, scopes, ttl_seconds=ttl, resource=resource)
        return raw, self.vault.authenticate(raw)

    async def call(self, principal, action, effect, **kwargs):
        return await self.plane.invoke(
            principal, "peer_chat", action, effect, kwargs
        )


class IdentityPolicyTests(AccessPlaneFixture):
    def test_scoped_bearer_audience_expiry_revocation_resource(self):
        token, caller = self.identity({"status:read"}, resource="project:alpha", ttl=10)
        self.assertTrue(caller.allows("status:read", "project:alpha", self.now()))
        self.assertFalse(caller.allows("status:read", "project:beta", self.now()))
        self.assertFalse(caller.allows("chat:room:send", "project:alpha", self.now()))
        with self.assertRaises(Denied):
            self.vault.authenticate(token, audience="another-service")
        with self.assertRaises(ValueError):
            self.vault.issue("client", {"*"})
        with self.assertRaises(ValueError):
            self.vault.issue("client", {"infra:deploy"})
        self.clock[0] += 11
        with self.assertRaises(Denied):
            self.vault.authenticate(token)
        next_token, _ = self.identity({"status:read"})
        self.vault.revoke(next_token)
        with self.assertRaises(Denied):
            self.vault.authenticate(next_token)

    def test_dynamic_manifest_new_module_and_rollback_without_process_restart(self):
        _, principal = self.identity({"capabilities:list", "status:read"})
        self.assertEqual(self.plane.list(principal)["capabilities"], [])
        previous = (self.module_root / "active.json").read_bytes()
        previous_policy = (self.policy_root / "active.json").read_bytes()

        self.modules.append(module_spec("status_probe", "2", "delegate:status"))
        self._write_modules()
        # Module availability alone confers no scope. Unknown in policy => hidden.
        self.assertEqual(self.plane.list(principal)["capabilities"], [])

        self.rules.append(rule("status_probe", "read", "status:read",
                               "read", "delegate:status"))
        self._write_policy()
        visible = self.plane.list(principal)["capabilities"]
        self.assertEqual([x["name"] for x in visible], ["status_probe"])
        self.assertEqual(visible[0]["version"], "2")
        self.assertEqual(asyncio.run(self.plane.invoke(
            principal, "status_probe", "read", "read", {"resource_id": "x"}
        ))["source"], "approved_delegate")

        # Install another module revision without changing running object or MCP schema.
        self.modules[-1]["version"] = "3"
        self._write_modules()
        self.assertEqual(self.plane.list(principal)["capabilities"][0]["version"], "3")

        # Atomic rollback of both separately owned references, no Python reload.
        (self.module_root / "active.json").write_bytes(previous)
        (self.policy_root / "active.json").write_bytes(previous_policy)
        self.assertEqual(self.plane.list(principal)["capabilities"], [])
        with self.assertRaises(Denied):
            asyncio.run(self.plane.invoke(principal, "status_probe", "read",
                                          "read", {}))

    def test_manifest_cannot_assign_its_own_scopes_or_load_python_or_external_url(self):
        _, principal = self.identity(self.a_scopes)
        self.modules[0]["scope"] = "infra:deploy"
        self._write_modules()
        with self.assertRaises(Denied):
            self.plane.list(principal)

        self.modules = [module_spec(adapter="delegate:https://evil.invalid")]
        self._write_modules()
        with self.assertRaises(Denied):
            self.plane.list(principal)

        self.modules = [module_spec()]
        self._write_modules()
        self.rules.append(rule("infra_admin", "deploy", "status:read",
                               "write", "delegate:status", operator_gate=True))
        self.modules.append(module_spec("infra_admin", "1", "delegate:status"))
        self._write_policy()
        self._write_modules()
        with self.assertRaises(Denied):
            asyncio.run(self.plane.invoke(principal, "infra_admin",
                                          "deploy", "write", {}))

        # Tampered release bytes fail digest verification, no stale cached grant.
        pointer = json.loads((self.module_root / "active.json").read_text())
        target = self.module_root / "releases" / (pointer["sha256"] + ".json")
        target.write_text('{"schema":1,"modules":[]}', encoding="utf-8")
        with self.assertRaises(Denied):
            self.plane.list(principal)


class RoomAuthorizationTests(AccessPlaneFixture):
    def test_role_bound_one_use_tickets_and_scopes(self):
        _, a = self.identity(self.a_scopes, client_id="native-owner")
        _, b = self.identity(self.b_scopes, client_id="native-peer")
        async def run():
            created = await self.call(a, "create", "write")
            room, a_ticket, invitation = (
                created["room_id"], created["a_ticket"], created["b_invite"]
            )
            with self.assertRaises(Denied):
                await self.call(b, "send", "write",
                                room_id=room, ticket=invitation, message="spoof A")
            joined = await self.call(b, "join", "write",
                                     room_id=room, invite=invitation)
            b_ticket = joined["b_ticket"]
            with self.assertRaises(Denied):
                await self.call(b, "join", "write",
                                room_id=room, invite=invitation)
            with self.assertRaises(Denied):
                await self.call(b, "close", "write",
                                room_id=room, ticket=a_ticket)
            with self.assertRaises(Denied):
                await self.call(a, "send", "write", room_id=room,
                                ticket=b_ticket, message="A", sender="A")
            first = await self.call(a, "send", "write", room_id=room,
                                    ticket=a_ticket, message="A/1")
            received = await self.call(b, "next", "wait", room_id=room,
                                       ticket=b_ticket, after_id=0,
                                       wait_seconds=0)
            self.assertEqual((received["from"], received["id"]),
                             ("A", first["id"]))
            reply = await self.call(b, "send", "write", room_id=room,
                                    ticket=b_ticket, message="B/1",
                                    reply_to=first["id"])
            answer = await self.call(a, "next", "wait", room_id=room,
                                     ticket=a_ticket, after_id=0, wait_seconds=0)
            self.assertEqual((answer["from"], answer["id"]),
                             ("B", reply["id"]))
            self.assertEqual((await self.call(a, "status", "read",
                room_id=room, ticket=a_ticket))["messages_count"], 2)
            await self.call(a, "close", "write", room_id=room, ticket=a_ticket)
            with self.assertRaises(Denied):
                await self.call(b, "next", "wait", room_id=room,
                                ticket=b_ticket, after_id=0, wait_seconds=0)
        asyncio.run(run())

    def test_restricted_bearer_cannot_cross_room_or_effect(self):
        _, a = self.identity(self.a_scopes)
        room = asyncio.run(self.call(a, "create", "write"))
        only_read_token, bound = self.identity(
            {"chat:room:read"}, resource=room["room_id"]
        )
        _ = only_read_token
        self.assertEqual(asyncio.run(self.call(
            bound, "status", "read", room_id=room["room_id"],
            ticket=room["a_ticket"]))["role"], "A")
        with self.assertRaises(Denied):
            asyncio.run(self.call(bound, "send", "write", room_id=room["room_id"],
                                  ticket=room["a_ticket"], message="no"))
        another = asyncio.run(self.call(a, "create", "write"))
        with self.assertRaises(Denied):
            asyncio.run(self.call(bound, "status", "read", room_id=another["room_id"],
                                  ticket=another["a_ticket"]))

    def test_actual_blocking_wait_and_revoke_before_return(self):
        token, a = self.identity(self.a_scopes)
        _, b = self.identity(self.b_scopes)
        created = asyncio.run(self.call(a, "create", "write"))
        joined = asyncio.run(self.call(b, "join", "write",
                        room_id=created["room_id"], invite=created["b_invite"]))
        errors, outputs, began = [], [], threading.Event()

        def waiting():
            async def inside():
                began.set()
                return await self.plane.invoke(
                    a, "peer_chat", "next", "wait",
                    {"room_id": created["room_id"],
                     "ticket": created["a_ticket"], "after_id": 0,
                     "wait_seconds": 4},
                    renew=lambda: self.vault.authenticate(token))
            try:
                outputs.append(asyncio.run(inside()))
            except Denied as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=waiting, daemon=True)
        thread.start()
        self.assertTrue(began.wait(1))
        # Wait remains active; then revoke the external bearer before wake-up.
        self.vault.revoke(token)
        asyncio.run(self.call(b, "send", "write",
                     room_id=created["room_id"], ticket=joined["b_ticket"],
                     message="cannot leak to revoked identity"))
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(outputs)
        self.assertEqual(errors, ["unauthorized"])


class McpSdkFacadeTests(AccessPlaneFixture):
    def test_four_stable_mcp_tools_two_independent_clients_and_no_extra_tools(self):
        from mcp import Client
        from mcp.server import MCPServer

        _, native = self.identity(self.a_scopes | self.b_scopes)
        server = MCPServer("Access plane prototype")
        register_access_tools(server, self.plane, lambda: native)

        async def invoke(client, tool, action, data):
            response = await client.call_tool(tool, {
                "capability": "peer_chat",
                "action": action, "parameters": data
            })
            return unpack(response)

        async def run():
            async with Client(server, raise_exceptions=True) as a, \
                       Client(server, raise_exceptions=True) as b:
                names = {x.name for x in (await a.list_tools()).tools}
                self.assertEqual(names, {
                    "capabilities_list", "capabilities_read",
                    "capabilities_write", "capabilities_wait"
                })
                listed = unpack(await a.call_tool("capabilities_list", {}))
                self.assertEqual(listed["capabilities"][0]["name"], "peer_chat")

                created = await invoke(a, "capabilities_write", "create", {})
                room = created["room_id"]
                joined = await invoke(b, "capabilities_write", "join", {
                    "room_id": room, "invite": created["b_invite"]
                })
                pending = asyncio.create_task(invoke(
                    b, "capabilities_wait", "next", {
                        "room_id": room, "ticket": joined["b_ticket"],
                        "after_id": 0, "wait_seconds": 3
                    }
                ))
                await asyncio.sleep(0.05)
                self.assertFalse(pending.done(), "wait returned before send")
                sent = await invoke(a, "capabilities_write", "send", {
                    "room_id": room, "ticket": created["a_ticket"],
                    "message": "real in-process MCP A"
                })
                got = await pending
                self.assertEqual(got["id"], sent["id"])
                self.assertEqual(got["from"], "A")
                denied = await invoke(a, "capabilities_write", "deploy", {})
                self.assertEqual(denied["reason"], "forbidden")
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
