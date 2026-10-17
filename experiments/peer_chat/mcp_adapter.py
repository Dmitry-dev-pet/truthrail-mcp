"""Isolated, source-only MCP adapter. No public listener or credentials included."""
from __future__ import annotations

import asyncio
import os
from .mailbox import Mailbox, MAX_WAIT


def make_server(mailbox: Mailbox | None = None):
    from mcp.server import MCPServer

    relay = mailbox if mailbox is not None else Mailbox()
    server = MCPServer("Alfred experimental peer chat")

    @server.tool()
    async def chat_create() -> dict:
        """Create an ephemeral two-role message room and return its secret room ID."""
        try:
            return relay.create_room()
        except OverflowError:
            return {"status": "error", "reason": "room-limit"}

    @server.tool()
    async def chat_send(room_id: str, sender: str, message: str, reply_to: int | None = None) -> dict:
        """Send one inert text message as role A or B; never execute its contents."""
        try:
            return relay.send(room_id, sender, message, reply_to)
        except (ValueError, OverflowError) as exc:
            return {"status": "error", "reason": str(exc)}

    @server.tool()
    async def chat_wait(room_id: str, role: str, after_id: int = 0,
                        wait_seconds: int = 15) -> dict:
        """Block up to 20 seconds for the OTHER role's next message after after_id."""
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= MAX_WAIT:
            return {"status": "error", "reason": "invalid-wait"}
        try:
            return await asyncio.to_thread(relay.wait, room_id, role, after_id, wait_seconds)
        except ValueError as exc:
            return {"status": "error", "reason": str(exc)}

    @server.tool()
    async def chat_close(room_id: str) -> dict:
        """Stop the bounded experiment and wake any waiting calls."""
        try:
            return relay.close(room_id)
        except ValueError as exc:
            return {"status": "error", "reason": str(exc)}

    return server


def main():
    if os.environ.get("ALFRED_EXPERIMENTAL_PEER_CHAT_ENABLED") != "1":
        raise SystemExit("peer-chat-mcp-disabled")
    server = make_server()
    server.run(transport="streamable-http", host="127.0.0.1", port=8775,
               stateless_http=True, json_response=True, max_request_body_size=8192)


if __name__ == "__main__":
    main()
