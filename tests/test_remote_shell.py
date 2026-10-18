"""Source-only tests. No real Mac, VPS, OAuth, or ChatGPT connection."""
from __future__ import annotations

import threading
import time
import unittest

from experiments.remote_shell.mac_client import call, verify_relay_url, run_command
from experiments.remote_shell.relay import Mailbox, RelayServer
from experiments.remote_shell.shell_session import ShellSession

MAC_TOKEN = "m" * 44
AGENT_TOKEN = "a" * 44


class QueueTests(unittest.TestCase):
    def test_delivery_and_reply_exactly_once(self):
        queue = Mailbox()
        ident = queue.submit("ls")
        leased = queue.receive(0)
        self.assertEqual(leased["command"], "ls")
        self.assertEqual(leased["command_id"], ident)
        self.assertEqual(queue.receive(0)["status"], "empty")
        result = {"stdout": "hello\n", "stderr": "", "exit_code": 0}
        self.assertTrue(queue.reply(ident, result))
        self.assertFalse(queue.reply(ident, result))
        self.assertEqual(queue.get(ident)["result"], result)

    def test_refuses_invalid_commands_and_results(self):
        queue = Mailbox()
        for cmd in ("", "a\nb", "x" * 4097, None):
            with self.assertRaises(ValueError):
                queue.submit(cmd)
        ident = queue.submit("pwd")
        queue.receive(0)
        with self.assertRaises(ValueError):
            queue.reply(ident, {"stdout": "x" * 70000, "stderr": "", "exit_code": 0})
        with self.assertRaises(ValueError):
            queue.reply(ident, {"stdout": "", "stderr": "", "exit_code": True})

    def test_expiry_does_not_replay(self):
        clock = [0.]
        queue = Mailbox(now=lambda: clock[0])
        ident = queue.submit("ls")
        clock[0] = 301.
        self.assertEqual(queue.receive(0)["status"], "empty")
        self.assertEqual(queue.get(ident)["status"], "expired")

    def test_waiter_wakes_on_submission(self):
        queue = Mailbox()
        result = []
        worker = threading.Thread(target=lambda: result.append(queue.receive(2)), daemon=True)
        worker.start()
        time.sleep(0.05)
        queue.submit("pwd")
        worker.join(1)
        self.assertEqual(result[0]["command"], "pwd")


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = RelayServer(("127.0.0.1", 0), Mailbox(), MAC_TOKEN, AGENT_TOKEN)
        cls.url = "http://127.0.0.1:" + str(cls.server.server_port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_round_trip_mac_receives_stdout(self):
        responses, errors = [], []

        def client():
            try:
                responses.append(run_command(self.url, MAC_TOKEN, "printf hello", wait_timeout=3))
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=client, daemon=True)
        worker.start()
        leased = call(self.url, AGENT_TOKEN, "GET", "/v1/commands/next?wait=2")
        self.assertEqual(leased["command"], "printf hello")
        ack = call(self.url, AGENT_TOKEN, "POST", "/v1/results/" + leased["command_id"], {
            "stdout": "hello", "stderr": "", "exit_code": 0,
        })
        self.assertEqual(ack["status"], "accepted")
        worker.join(4)
        self.assertEqual(errors, [])
        self.assertEqual(responses[0]["stdout"], "hello")

    def test_simulated_executor_maintains_cd(self):
        outputs = []
        with ShellSession(timeout=5) as shell:
            for cmd in ("cd /tmp", "pwd", "printf WORKS"):
                worker = threading.Thread(
                    target=lambda c=cmd: outputs.append(
                        run_command(self.url, MAC_TOKEN, c, wait_timeout=4)
                    ),
                    daemon=True,
                )
                worker.start()
                leased = call(self.url, AGENT_TOKEN, "GET", "/v1/commands/next?wait=2")
                response = shell.run(leased["command"])
                ack = call(self.url, AGENT_TOKEN, "POST", "/v1/results/" + leased["command_id"], response)
                self.assertEqual(ack["status"], "accepted")
                worker.join(4)
                self.assertFalse(worker.is_alive())
        self.assertEqual(outputs[1]["stdout"].strip(), "/tmp")
        self.assertEqual(outputs[2]["stdout"], "WORKS")


    def test_mcp_protocol_round_trip_via_inprocess_client(self):
        """Use real MCP tool calls, not direct Python method calls."""
        import asyncio
        import json
        from mcp import Client
        from experiments.remote_shell.mcp_adapter import make_server

        outputs = []
        def terminal():
            outputs.append(run_command(
                self.url, MAC_TOKEN, "printf integration",
                wait_timeout=8
            ))
        worker = threading.Thread(target=terminal, daemon=True)
        worker.start()

        async def exercise():
            async with Client(make_server(self.url, AGENT_TOKEN),
                              raise_exceptions=True) as client:
                listed = await client.list_tools()
                self.assertEqual(
                    {tool.name for tool in listed.tools},
                    {"terminal_wait", "terminal_reply"},
                )
                received = await client.call_tool("terminal_wait", {"wait_seconds": 3})
                self.assertFalse(received.is_error)
                leased = received.structured_content
                if leased is None:
                    leased = json.loads(received.content[0].text)
                self.assertEqual(leased["command"], "printf integration")
                ack = await client.call_tool("terminal_reply", {
                    "command_id": leased["command_id"],
                    "stdout": "integration", "stderr": "", "exit_code": 0,
                })
                self.assertFalse(ack.is_error)
        asyncio.run(exercise())
        worker.join(timeout=8)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outputs[0]["stdout"], "integration")


    def test_role_separation(self):
        with self.assertRaises(RuntimeError) as ctx:
            call(self.url, MAC_TOKEN, "GET", "/v1/commands/next?wait=0")
        self.assertIn("HTTP 401", str(ctx.exception))
        with self.assertRaises(RuntimeError) as ctx:
            call(self.url, AGENT_TOKEN, "POST", "/v1/commands", {"command": "ls"})
        self.assertIn("HTTP 401", str(ctx.exception))

    def test_remote_plain_http_rejected(self):
        with self.assertRaises(ValueError):
            verify_relay_url("http://example.org")
        self.assertEqual(verify_relay_url(self.url), self.url)


class BashTests(unittest.TestCase):
    def test_directory_and_env_persist(self):
        with ShellSession(timeout=5) as shell:
            self.assertEqual(shell.run("cd /tmp")["exit_code"], 0)
            self.assertEqual(shell.run("pwd")["stdout"].strip(), "/tmp")
            self.assertEqual(shell.run("export ALFRED_TEST_VAR=ok")["exit_code"], 0)
            self.assertEqual(shell.run('printf "%s" "$ALFRED_TEST_VAR"')["stdout"], "ok")

    def test_nonzero_exit_and_combined_stderr(self):
        with ShellSession(timeout=5) as shell:
            result = shell.run("echo broken >&2; false")
            self.assertEqual(result["exit_code"], 1)
            self.assertIn("broken", result["stdout"])
            self.assertEqual(result["stderr"], "")



class McpRegistrationTests(unittest.TestCase):
    def test_exact_two_mcp_tools_are_registered(self):
        import asyncio
        from experiments.remote_shell.mcp_adapter import make_server
        mcp = make_server("http://127.0.0.1:8768", AGENT_TOKEN)
        tools = asyncio.run(mcp.list_tools())
        self.assertEqual({item.name for item in tools}, {"terminal_wait", "terminal_reply"})
        schemas = {item.name: item.input_schema for item in tools}
        self.assertIn("wait_seconds", schemas["terminal_wait"]["properties"])
        self.assertIn("command_id", schemas["terminal_reply"]["properties"])



if __name__ == "__main__":
    unittest.main()
