"""Synthetic tests of optional peer-chat within the shared Atlas MCP.

No live tunnel, GitHub, Mac or VPS activity. The chat payload is inert text.
"""
from __future__ import annotations

import asyncio
import json
import threading
import unittest

from shared_alfred_gateway.peer_chat import Mailbox, MAX_MESSAGES, ROOM_IDLE_TTL
from shared_alfred_gateway import server as shared


def unwrap(reply):
    if reply.is_error:
        raise AssertionError("MCP call failed")
    obj = getattr(reply, "structured_content", None)
    if obj is None:
        text = [c.text for c in reply.content if getattr(c, "type", None) == "text"]
        if len(text) != 1:
            raise AssertionError("unexpected MCP content")
        obj = json.loads(text[0])
    if type(obj) is not dict:
        raise AssertionError("unexpected MCP payload")
    return obj


class PeerChatMailboxTests(unittest.TestCase):
    def setUp(self):
        self.mailbox = Mailbox()
        self.room = self.mailbox.create_room()["room_id"]

    def test_blocking_wait_is_woken_by_peer_and_cursor_does_not_lose_messages(self):
        started = threading.Event()
        results = []

        def waiter():
            started.set()
            results.append(self.mailbox.wait(self.room, "A", 0, 3))

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        self.assertTrue(started.wait(1))
        self.assertTrue(thread.is_alive())
        msg = self.mailbox.send(self.room, "B", "Reply to A")
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["id"], msg["id"])
        self.assertEqual(results[0]["message"], "Reply to A")
        # Late waiters see the last message without any destructive dequeue.
        self.assertEqual(self.mailbox.wait(self.room, "A", 0, 0)["id"], msg["id"])
        self.assertEqual(self.mailbox.wait(self.room, "A", msg["id"], 0)["status"], "timeout")

    def test_three_round_trips_with_global_message_cursors(self):
        cursor_a, cursor_b = 0, 0
        for n in range(3):
            sent = self.mailbox.send(self.room, "A", f"A/{n+1}",
                                     cursor_a or None)
            recv_b = self.mailbox.wait(self.room, "B", cursor_b, 0)
            self.assertEqual(recv_b["id"], sent["id"])
            cursor_b = recv_b["id"]
            reply = self.mailbox.send(self.room, "B", f"B/{n+1}", cursor_b)
            recv_a = self.mailbox.wait(self.room, "A", cursor_a, 0)
            self.assertEqual(recv_a["id"], reply["id"])
            cursor_a = recv_a["id"]
        self.assertEqual(self.mailbox.wait(self.room, "A", cursor_a, 0)["status"], "timeout")

    def test_role_validation_and_close(self):
        with self.assertRaises(ValueError):
            self.mailbox.send(self.room, "evil", "test")
        for invalid in ("", "x" * 2049, "\x00"):
            with self.assertRaises(ValueError):
                self.mailbox.send(self.room, "A", invalid)
        for invalid in (True, 21, -1):
            with self.assertRaises(ValueError):
                self.mailbox.wait(self.room, "A", 0, invalid)
        # A room ID does not authorize random IDs or other rooms.
        with self.assertRaises(ValueError):
            self.mailbox.wait("1" * 32, "A", 0, 0)
        sent = self.mailbox.send(self.room, "A", "Hi")
        with self.assertRaises(ValueError):
            self.mailbox.send(self.room, "A", "Self-reply", sent["id"])
        self.mailbox.close(self.room)
        self.assertEqual(self.mailbox.wait(self.room, "B", 0, 0)["status"], "message")
        with self.assertRaises(ValueError):
            self.mailbox.send(self.room, "A", "after close")

    def test_finite_room_message_and_ttl(self):
        for n in range(MAX_MESSAGES):
            self.mailbox.send(self.room, "A", f"msg{n}")
        with self.assertRaises(OverflowError):
            self.mailbox.send(self.room, "A", "overflow")
        tick = [0.0]
        mailbox = Mailbox(clock=lambda: tick[0])
        room = mailbox.create_room()["room_id"]
        tick[0] = ROOM_IDLE_TTL + 1
        with self.assertRaises(ValueError):
            mailbox.wait(room, "B", 0, 0)


class SharedGatewayPeerMCPTests(unittest.TestCase):
    def test_flag_off_does_not_change_existing_five_schemas(self):
        from mcp import Client

        async def atlas(name, args):
            return {"from": "atlas", "name": name, "args": args}

        async def test():
            existing = shared.make_server(_atlas_backend=atlas)
            expanded = shared.make_server(_atlas_backend=atlas, peer_chat_enabled=True)
            async with Client(existing, raise_exceptions=True) as client1, \
                       Client(expanded, raise_exceptions=True) as client2:
                one = {t.name: t for t in (await client1.list_tools()).tools}
                two = {t.name: t for t in (await client2.list_tools()).tools}
                self.assertEqual(set(one), set(shared.ATLAS_TOOLS))
                self.assertEqual(set(two), set(shared.ATLAS_TOOLS) | set(shared.PEER_CHAT_TOOLS))
                for name in shared.ATLAS_TOOLS:
                    self.assertEqual(
                        one[name].model_dump(by_alias=True)["inputSchema"],
                        two[name].model_dump(by_alias=True)["inputSchema"],
                    )
                for name in shared.ATLAS_TOOLS:
                    result = await client2.call_tool(
                        name, {
                            "dataset_info": {},
                            "search_rulers": {"text": "Ленин"},
                            "get_timeline": {"year": 1953},
                            "compare_rulers": {"person_ids": ["person-p03"]},
                            "get_evidence": {"record_id": "record-p03"},
                        }[name]
                    )
                    self.assertIn("atlas", str(result))

        asyncio.run(test())

    def test_two_independent_in_process_mcp_clients_exchange_messages(self):
        from mcp import Client

        async def atlas(name, args):
            return {"status": "ok"}

        async def test():
            gateway = shared.make_server(_atlas_backend=atlas, peer_chat_enabled=True)
            async with Client(gateway, raise_exceptions=True) as a, \
                       Client(gateway, raise_exceptions=True) as b:
                created = unwrap(await a.call_tool("chat_create", {}))
                room = created["room_id"]
                async def send(client, sender, msg, ref=None):
                    payload = {"room_id": room, "sender": sender, "message": msg}
                    if ref is not None:
                        payload["reply_to"] = ref
                    return unwrap(await client.call_tool("chat_send", payload))

                # B waits within its own MCP call while A sends from another.
                waiting = asyncio.create_task(b.call_tool("chat_wait", {
                    "room_id": room, "role": "B", "after_id": 0, "wait_seconds": 3}))
                await asyncio.sleep(0.05)
                self.assertFalse(waiting.done())
                a1 = await send(a, "A", "A/1")
                b_got = unwrap(await waiting)
                self.assertEqual(b_got["id"], a1["id"])
                self.assertEqual(b_got["message"], "A/1")

                b1 = await send(b, "B", "B/1", a1["id"])
                a_got = unwrap(await a.call_tool("chat_wait", {
                    "room_id": room, "role": "A", "after_id": 0, "wait_seconds": 0
                }))
                self.assertEqual(a_got["id"], b1["id"])
                # Explicitly bounded: no unnecessary LLM inference or endless polling.
                self.assertEqual(unwrap(await a.call_tool("chat_wait", {
                    "room_id": room, "role": "A", "after_id": b1["id"],
                    "wait_seconds": 0
                }))["status"], "timeout")
                self.assertEqual(unwrap(await a.call_tool("chat_close", {
                    "room_id": room
                }))["status"], "closed")

        asyncio.run(test())


if __name__ == "__main__":
    unittest.main()
