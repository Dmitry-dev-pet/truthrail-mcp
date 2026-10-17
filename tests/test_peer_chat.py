"""Synthetic source tests only; not a live ChatGPT or Mac end-to-end test."""
import threading
import unittest

from experiments.peer_chat.mailbox import Mailbox, MAX_MESSAGES, MAX_ROOMS, ROOM_IDLE_TTL


class PeerChatTests(unittest.TestCase):
    def setUp(self):
        self.mailbox = Mailbox()
        self.room = self.mailbox.create_room()["room_id"]

    def test_three_exchanges_using_cursors(self):
        last_a, last_b = 0, 0
        for i in range(3):
            sent_a = self.mailbox.send(self.room, "A", f"A/{i+1}", last_b or None)
            got_b = self.mailbox.wait(self.room, "B", last_a, 0)
            self.assertEqual((got_b["from"], got_b["message"]), ("A", f"A/{i+1}"))
            last_a = sent_a["id"]
            sent_b = self.mailbox.send(self.room, "B", f"B/{i+1}", last_a)
            got_a = self.mailbox.wait(self.room, "A", last_b, 0)
            self.assertEqual((got_a["from"], got_a["message"]), ("B", f"B/{i+1}"))
            last_b = sent_b["id"]
        self.assertEqual(self.mailbox.wait(self.room, "A", last_b, 0)["status"], "timeout")
        self.assertEqual(self.mailbox.wait(self.room, "B", last_a, 0)["status"], "timeout")

    def test_blocked_wait_wakes_when_peer_sends(self):
        entered, out = threading.Event(), []
        def waiter():
            entered.set()
            out.append(self.mailbox.wait(self.room, "A", 0, 2))
        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(1))
        sent = self.mailbox.send(self.room, "B", "answer")
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(out[0]["id"], sent["id"])
        self.assertEqual(out[0]["message"], "answer")

    def test_validation_and_cross_room_isolation(self):
        other = self.mailbox.create_room()["room_id"]
        self.mailbox.send(self.room, "A", "private")
        self.assertEqual(self.mailbox.wait(other, "B", 0, 0)["status"], "timeout")
        for role in ("C", "", None):
            with self.assertRaises(ValueError):
                self.mailbox.send(self.room, role, "bad")
        for message in ("", "\x00", "x" * 2050):
            with self.assertRaises(ValueError):
                self.mailbox.send(self.room, "A", message)
        for cursor in (True, -1, "0"):
            with self.assertRaises(ValueError):
                self.mailbox.wait(self.room, "B", cursor, 0)
        for seconds in (True, 21, -1):
            with self.assertRaises(ValueError):
                self.mailbox.wait(self.room, "B", 0, seconds)
        with self.assertRaises(ValueError):
            self.mailbox.send(self.room, "A", "self reply", reply_to=1)
        with self.assertRaises(ValueError):
            self.mailbox.send(self.room, "B", "bad reply", reply_to=400)

    def test_close_interrupts_wait(self):
        entered, out = threading.Event(), []
        def waiter():
            entered.set()
            out.append(self.mailbox.wait(self.room, "B", 0, 5))
        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(1))
        self.mailbox.close(self.room)
        thread.join(timeout=2)
        self.assertEqual(out[0]["status"], "closed")
        with self.assertRaises(ValueError):
            self.mailbox.send(self.room, "A", "after close")

    def test_bounded_memory_and_expiry(self):
        for i in range(MAX_MESSAGES):
            self.mailbox.send(self.room, "A", f"message {i}")
        with self.assertRaises(OverflowError):
            self.mailbox.send(self.room, "B", "overflow")
        fake_time = [0.]
        relay = Mailbox(clock=lambda: fake_time[0])
        expired = relay.create_room()["room_id"]
        fake_time[0] = ROOM_IDLE_TTL + 1
        with self.assertRaises(ValueError):
            relay.wait(expired, "A", 0, 0)
        for i in range(MAX_ROOMS):
            relay.create_room()
        with self.assertRaises(OverflowError):
            relay.create_room()

    def test_registration_when_mcp_dependency_available(self):
        try:
            from experiments.peer_chat.mcp_adapter import make_server
            mcp = make_server(Mailbox())
        except ModuleNotFoundError as exc:
            if exc.name == "mcp":
                self.skipTest("MCP package is not installed in local sandbox")
            raise
        import asyncio
        listed = asyncio.run(mcp.list_tools())
        self.assertEqual({tool.name for tool in listed},
                         {"chat_create", "chat_send", "chat_wait", "chat_close"})
        wait_schema = next(tool.input_schema for tool in listed if tool.name == "chat_wait")
        self.assertIn("wait_seconds", wait_schema["properties"])


if __name__ == "__main__":
    unittest.main()
