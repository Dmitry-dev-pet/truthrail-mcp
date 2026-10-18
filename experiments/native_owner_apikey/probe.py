"""Off-VPS native owner API key header proof. Never emit its token."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

URL = "http://172.30.143.10:8765/mcp"
KEY_FILE = Path("/run/test-owner-auth/token")
HEADER = "X-Alfred-Native-Owner-Key"
EXPECTED = {"chat_create", "chat_join", "chat_send",
            "chat_wait", "chat_status", "chat_close"}


async def refused(headers: dict[str, str]) -> None:
    request = {
        "jsonrpc": "2.0", "method": "initialize", "id": 1,
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "native-owner-api-key-ci", "version": "1"},
        },
    }
    async with httpx2.AsyncClient(timeout=6, trust_env=False) as http:
        response = await http.post(
            URL, json=request,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **headers,
            },
        )
    assert response.status_code in (401, 403), (
        "untrusted-native-owner-credential-not-rejected"
    )


def result_data(reply) -> dict:
    assert not reply.is_error, "approved-peer-tool-error"
    payload = getattr(reply, "structured_content", None)
    if payload is None:
        texts = [
            item.text for item in reply.content
            if getattr(item, "type", "") == "text"
        ]
        assert len(texts) == 1, "unexpected-MCP-tool-response"
        payload = json.loads(texts[0])
    if type(payload) is dict and type(payload.get("result")) is dict:
        payload = payload["result"]
    assert type(payload) is dict
    return payload


async def valid_owner(key: str) -> None:
    # An arbitrary connector Authorization header must NOT shadow the
    # private, independent X-Alfred-Native-Owner-Key transport header.
    headers = {
        HEADER: key,
        "Authorization": "Bearer unrelated-forwarded-connector-token",
    }
    async with httpx2.AsyncClient(
        headers=headers,
        timeout=httpx2.Timeout(8.0, read=20.0),
        trust_env=False,
    ) as http:
        async with Client(
            streamable_http_client(URL, http_client=http),
            raise_exceptions=True,
        ) as owner:
            tools = [item.name for item in (await owner.list_tools()).tools]
            assert len(tools) == len(EXPECTED) and all(
                any(name == target or name.endswith("_" + target) for name in tools)
                for target in EXPECTED
            ), "owner-not-assigned-approved-tool-catalog"
            create = next(
                n for n in tools if n == "chat_create" or n.endswith("_chat_create")
            )
            close = next(
                n for n in tools if n == "chat_close" or n.endswith("_chat_close")
            )
            created = result_data(await owner.call_tool(create, {}))
            assert len(created["room_id"]) == 32
            assert result_data(
                await owner.call_tool(
                    close,
                    {"room_id": created["room_id"],
                     "ticket": created["a_ticket"]},
                )
            )["status"] == "closed"


async def main() -> None:
    key = KEY_FILE.read_text(encoding="ascii")
    assert len(key) >= 48 and key == key.strip()
    await refused({})
    await refused({"Authorization": "Bearer unrelated-fake"})
    await refused({HEADER: "wrong-key-without-secret"})
    await refused({HEADER: "wrong-key", "Authorization": "Bearer unrelated-fake"})
    await valid_owner(key)
    print(
        "owner_api_key_strict=passed; missing_wrong_and_authorization_only=denied; "
        "independent_owner_header_with_conflicting_authorization=accepted; "
        "owner_tools=6; private_Peer_Chat=passed"
    )


if __name__ == "__main__":
    asyncio.run(main())
