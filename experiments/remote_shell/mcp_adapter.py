"""Experimental MCP adapter: leases commands and stores replies, NEVER executes shell.

Requires mcp==2.3.0, separate authenticated transport and loopback relay.
Not deployed or registered in any live ChatGPT MCP connection.
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import quote

from .mac_client import call, verify_relay_url
from .relay import ID_RE, MAX_WAIT


def make_server(relay_url: str, agent_token: str):
    from mcp.server import MCPServer

    url = verify_relay_url(relay_url)
    if len(agent_token) < 32:
        raise ValueError("missing-agent-token")
    server = MCPServer("Alfred experimental remote shell")

    @server.tool()
    async def terminal_wait(wait_seconds: int = 10) -> dict:
        """Wait briefly for one Mac terminal command. Does not execute it."""
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= MAX_WAIT:
            return {"status": "error", "reason": "invalid-wait"}
        try:
            return await asyncio.to_thread(
                call, url, agent_token, "GET",
                f"/v1/commands/next?wait={wait_seconds}", None, MAX_WAIT + 8,
            )
        except Exception:
            return {"status": "error", "reason": "relay-unavailable"}

    @server.tool()
    async def terminal_reply(command_id: str, stdout: str, stderr: str, exit_code: int) -> dict:
        """Return result of shell command executed in the ChatGPT Linux sandbox."""
        if type(command_id) is not str or not ID_RE.fullmatch(command_id):
            return {"status": "error", "reason": "invalid-id"}
        if type(stdout) is not str or type(stderr) is not str or type(exit_code) is not int:
            return {"status": "error", "reason": "invalid-result"}
        try:
            return await asyncio.to_thread(
                call, url, agent_token, "POST", "/v1/results/" + quote(command_id),
                {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}, 8,
            )
        except Exception:
            return {"status": "error", "reason": "relay-unavailable-or-rejected"}

    return server


def main():
    if os.environ.get("ALFRED_EXPERIMENTAL_SHELL_MCP_ENABLED") != "1":
        raise SystemExit("experimental-shell-mcp-disabled")
    server = make_server(
        os.environ.get("ALFRED_RELAY_URL", ""),
        os.environ.get("ALFRED_RELAY_AGENT_TOKEN", ""),
    )
    if os.environ.get("ALFRED_EXPERIMENTAL_SHELL_BIND", "127.0.0.1") != "127.0.0.1":
        raise SystemExit("shell-mcp-requires-loopback-bind")
    server.run(
        transport="streamable-http", host="127.0.0.1", port=8769,
        stateless_http=True, json_response=True, max_request_body_size=100000,
    )


if __name__ == "__main__":
    main()
