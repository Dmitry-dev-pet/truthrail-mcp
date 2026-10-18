"""One live Agentgateway deployment, two internal Docker NICs, strict JWT.

This module runs ONLY in ephemeral GitHub-hosted Docker tests. No real clients,
Cloudflare connector, tunnel key, Atlas data, user tokens, or VPS.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import socket
import sys
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

OWNER_URL = "http://172.30.143.10:8765/mcp"
EXTERNAL_URL = "http://172.30.144.10:8766/mcp"
OWNER_IP = "172.30.143.10"
EXTERNAL_IP = "172.30.144.10"

OWNER_TOOLS = {"chat_create", "chat_join", "chat_send", "chat_wait",
               "chat_status", "chat_close", "owner_marker"}
EXTERNAL_TOOLS = {"chat_join", "chat_send", "chat_wait", "chat_status"}
READER_TOOLS = {"chat_status"}
ALL_TOKENS = "/tmp/ephemeral_tokens"


def token(path: str = "", root: str = ALL_TOKENS) -> str:
    if path not in {
        "owner.jwt", "owner_expired.jwt", "owner_wrong_audience.jwt",
        "external_writer.jwt", "external_reader.jwt",
        "external_claims_owner.jwt", "external_wrong_audience.jwt",
        "external_expired.jwt",
    }:
        raise AssertionError("unrecognized-test-token")
    return (Path(root) / path).read_text(encoding="ascii").strip()


def matching(names: list[str], short_name: str) -> str:
    found = [name for name in names if
             name == short_name or name.endswith("_" + short_name)]
    assert len(found) == 1, "mcp-tool-name-drift:" + short_name
    return found[0]


def has_tool(names: list[str], short_name: str) -> bool:
    return any(x == short_name or x.endswith("_" + short_name) for x in names)


def unpack(reply: Any) -> dict:
    if reply.is_error:
        raise AssertionError("allowed-tool-call-failed")
    result = getattr(reply, "structured_content", None)
    if result is None:
        texts = [x.text for x in reply.content
                 if getattr(x, "type", None) == "text"]
        assert len(texts) == 1, "invalid-mcp-content"
        result = json.loads(texts[0])
    if isinstance(result, dict) and isinstance(result.get("result"), dict):
        result = result["result"]
    assert type(result) is dict, "invalid-mcp-result"
    return result


async def create(stack: AsyncExitStack, url: str, bearer: str | None,
                 extra_headers: dict | None = None):
    headers = {}
    if bearer is not None:
        headers["Authorization"] = "Bearer " + bearer
    if extra_headers is not None:
        headers.update(extra_headers)
    transport = await stack.enter_async_context(httpx2.AsyncClient(
        headers=headers,
        timeout=httpx2.Timeout(8.0, read=22.0),
        trust_env=False,
    ))
    return await stack.enter_async_context(Client(
        streamable_http_client(url, http_client=transport),
        raise_exceptions=True,
    ))


async def listing(url: str, bearer: str | None, extra_headers=None):
    async with AsyncExitStack() as stack:
        client = await create(stack, url, bearer, extra_headers)
        return [tool.name for tool in (await client.list_tools()).tools]


async def denied_connection(url: str, bearer: str | None, case: str):
    try:
        names = await listing(url, bearer, {
            "X-Alfred-Role": "owner",
            "X-Forwarded-User": "approved-owner",
            "X-Forwarded-For": "127.0.0.1",
            "X-Original-URI": "/mcp",
        })
    except Exception:
        return
    raise AssertionError("invalid-identity-accepted:" + case +
                         ":visible=" + str(len(names)))


async def denied_tool(client, tool: str, arguments: dict | None = None):
    try:
        result = await client.call_tool(tool, arguments or {})
    except Exception:
        return
    if result.is_error:
        return
    payload = unpack(result)
    if payload.get("status") == "denied":
        return
    raise AssertionError("hidden-tool-executed")


async def wait_ready(url: str, bearer: str, expected: str):
    last_type = "not_attempted"
    last_visible = 0
    last_status = "unknown"
    for _ in range(70):
        try:
            names = await listing(url, bearer)
            last_visible = len(names)
            last_type = "tool_list_incomplete"
            if has_tool(names, expected):
                return
        except Exception as exc:
            # No exception strings, response bodies, URLs or credentials.
            classes = []
            codes = []
            def visit(err, depth=0):
                if depth > 5 or len(classes) >= 14:
                    return
                classes.append(type(err).__name__)
                response = getattr(err, "response", None)
                status = getattr(response, "status_code", None)
                if type(status) is int:
                    codes.append(str(status))
                for child in getattr(err, "exceptions", ()):
                    visit(child, depth + 1)
                cause = getattr(err, "__cause__", None)
                if cause is not None:
                    visit(cause, depth + 1)
            visit(exc)
            last_type = "/".join(classes)
            last_status = ",".join(codes) if codes else "unavailable"
        await asyncio.sleep(.15)
    probe_status = "unavailable"
    try:
        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer " + bearer},
            timeout=2.0, trust_env=False
        ) as http:
            response = await http.get(url)
            probe_status = str(response.status_code)
    except Exception as exc:
        probe_status = type(exc).__name__
    raise RuntimeError("gateway-listener-unavailable:" + expected +
                       ":failure_class=" + last_type +
                       ":http_status=" + last_status +
                       ":http_get_status=" + probe_status +
                       ":visible_tools=" + str(last_visible))


async def verify_owner_external():
    owner_token, writer_token, reader_token = (
        token("owner.jwt"), token("external_writer.jwt"),
        token("external_reader.jwt"),
    )
    await wait_ready(OWNER_URL, owner_token, "owner_marker")
    await wait_ready(EXTERNAL_URL, writer_token, "chat_send")
    async with AsyncExitStack() as stack:
        owner = await create(stack, OWNER_URL, owner_token)
        external = await create(stack, EXTERNAL_URL, writer_token)
        observer = await create(stack, EXTERNAL_URL, reader_token)
        all_owner = [t.name for t in (await owner.list_tools()).tools]
        all_external = [t.name for t in (await external.list_tools()).tools]
        all_observer = [t.name for t in (await observer.list_tools()).tools]
        assert len(all_owner) == len(OWNER_TOOLS)
        assert all(has_tool(all_owner, tool) for tool in OWNER_TOOLS)
        assert len(all_external) == len(EXTERNAL_TOOLS)
        assert all(has_tool(all_external, tool) for tool in EXTERNAL_TOOLS)
        assert not any(has_tool(all_external, t) for t in
                       OWNER_TOOLS - EXTERNAL_TOOLS)
        assert len(all_observer) == len(READER_TOOLS)
        assert has_tool(all_observer, "chat_status")
        assert not has_tool(all_external, "owner_marker")
        owner_tool = matching(all_owner, "owner_marker")
        ok = unpack(await owner.call_tool(owner_tool, {}))
        assert ok == {"synthetic": True, "marker": "owner-catalog-fixture-only"}

        # Forged identity and a word-for-word 'alfred:owner' claim signed
        # by EXTERNAL issuer must never create an owner principal.
        claim = token("external_claims_owner.jwt")
        fake = await listing(EXTERNAL_URL, claim, {
            "X-Alfred-Role": "owner",
            "X-Forwarded-User": "approved-owner",
        })
        assert len(fake) == 1 and has_tool(fake, "chat_status")
        assert not has_tool(fake, "owner_marker")
        async with AsyncExitStack() as fake_stack:
            fake_client = await create(fake_stack, EXTERNAL_URL, claim, {
                "X-Alfred-Role": "owner",
                "X-Forwarded-User": "approved-owner",
            })
            await denied_tool(fake_client, owner_tool)
        await denied_tool(external, owner_tool)
        await denied_tool(observer, matching(all_external, "chat_send"), {
            "room_id": "f" * 32, "ticket": "invalid", "message": "denied"
        })

        cases = [
            ("absent-owner", OWNER_URL, None),
            ("absent-external", EXTERNAL_URL, None),
            ("external-as-owner", OWNER_URL, writer_token),
            ("fake-external-owner", OWNER_URL, claim),
            ("owner-as-external", EXTERNAL_URL, owner_token),
            ("owner-wrong-aud", OWNER_URL, token("owner_wrong_audience.jwt")),
            ("external-wrong-aud", EXTERNAL_URL,
             token("external_wrong_audience.jwt")),
            ("owner-expired", OWNER_URL, token("owner_expired.jwt")),
            ("external-expired", EXTERNAL_URL, token("external_expired.jwt")),
            ("bad-signature", OWNER_URL, writer_token[:-4] + "oops"),
        ]
        for case, url, bearer in cases:
            await denied_connection(url, bearer, case)

        # One in-memory room, three A -> B -> A round trips, real MCP
        # network requests through TWO JWT policy routes.
        new_room = unpack(await owner.call_tool(
            matching(all_owner, "chat_create"), {}
        ))
        room_id = new_room["room_id"]
        a_ticket = new_room["a_ticket"]
        invite = new_room["b_invite"]
        joined = unpack(await external.call_tool(
            matching(all_external, "chat_join"),
            {"room_id": room_id, "invitation": invite}
        ))
        assert joined["role"] == "B"
        b_ticket = joined["b_ticket"]
        await denied_tool(external, matching(all_external, "chat_join"), {
            "room_id": room_id, "invitation": invite,
        })
        cursor_a = cursor_b = 0
        for i in range(3):
            waiting_b = asyncio.create_task(external.call_tool(
                matching(all_external, "chat_wait"), {
                    "room_id": room_id, "ticket": b_ticket,
                    "after_id": cursor_b, "wait_seconds": 5
                }
            ))
            await asyncio.sleep(.1)
            assert not waiting_b.done(), "external-longpoll-not-blocking"
            sent_a = unpack(await owner.call_tool(
                matching(all_owner, "chat_send"), {
                    "room_id": room_id, "ticket": a_ticket,
                    "message": "A/" + str(i + 1),
                    **({"reply_to": cursor_a} if cursor_a else {})
                }
            ))
            got_b = unpack(await asyncio.wait_for(waiting_b, 8))
            assert got_b["from"] == "A"
            assert got_b["id"] == sent_a["id"]
            assert got_b["message"] == "A/" + str(i + 1)
            cursor_b = got_b["id"]

            waiting_a = asyncio.create_task(owner.call_tool(
                matching(all_owner, "chat_wait"), {
                    "room_id": room_id, "ticket": a_ticket,
                    "after_id": cursor_a, "wait_seconds": 5
                }
            ))
            await asyncio.sleep(.1)
            assert not waiting_a.done(), "owner-longpoll-not-blocking"
            sent_b = unpack(await external.call_tool(
                matching(all_external, "chat_send"), {
                    "room_id": room_id, "ticket": b_ticket,
                    "message": "B/" + str(i + 1),
                    "reply_to": cursor_b,
                }
            ))
            got_a = unpack(await asyncio.wait_for(waiting_a, 8))
            assert got_a["from"] == "B"
            assert got_a["id"] == sent_b["id"]
            assert got_a["message"] == "B/" + str(i + 1)
            cursor_a = got_a["id"]

        meta = unpack(await observer.call_tool(
            matching(all_observer, "chat_status"),
            {"room_id": room_id, "ticket": b_ticket}
        ))
        assert meta["message_count"] == 6 and meta["role"] == "B"
        forbidden = unpack(await observer.call_tool(
            matching(all_observer, "chat_status"),
            {"room_id": room_id, "ticket": "invalid"}
        ))
        assert forbidden["status"] == "denied"
        closed = unpack(await owner.call_tool(
            matching(all_owner, "chat_close"),
            {"room_id": room_id, "ticket": a_ticket}
        ))
        assert closed["status"] == "closed"
        print(json.dumps({
            "single_deployment_jwt": "verified",
            "owner_tools": len(all_owner),
            "external_writer_tools": len(all_external),
            "external_reader_tools": len(all_observer),
            "wrong_identity_denials": len(cases),
            "real_http_round_trips": 3,
            "room_role_ticket_denial": True,
            "external_owner_marker_denied": True,
            "separate_issuers": True,
            "separate_audiences": True,
        }, sort_keys=True))


async def verify_network_client(role: str, root: str):
    if role not in {"owner", "external"}:
        raise AssertionError("unknown-probe-role")
    if role == "owner":
        url, tkn, expected, forbidden = (
            OWNER_URL, token("owner.jwt", root), OWNER_TOOLS,
            set()
        )
    else:
        url, tkn, expected, forbidden = (
            EXTERNAL_URL, token("external_writer.jwt", root),
            EXTERNAL_TOOLS, {"owner_marker", "chat_create", "chat_close"}
        )
    async with AsyncExitStack() as stack:
        mcp = await create(stack, url, tkn)
        names = [x.name for x in (await mcp.list_tools()).tools]
        assert len(names) == len(expected)
        assert all(has_tool(names, t) for t in expected)
        assert not any(has_tool(names, t) for t in forbidden)
        if role == "owner":
            payload = unpack(await mcp.call_tool(
                matching(names, "owner_marker"), {}
            ))
            assert payload["marker"] == "owner-catalog-fixture-only"
        else:
            await denied_tool(mcp, "shared-peer-chat_owner_marker")

    # An actual external-network container CANNOT reach the owner interface.
    # TCP errors here prove network isolation separately from JWT rules.
    destination = OWNER_IP if role == "external" else EXTERNAL_IP
    port = 8765 if role == "external" else 8766
    on_own_nic = OWNER_IP if role == "owner" else EXTERNAL_IP
    for case, address, port_to_check in [
        ("foreign-nic", destination, port),
        ("private-backend", on_own_nic, 18765),
        ("other-port-own-nic", on_own_nic, port),
    ]:
        with socket.socket() as s:
            s.settimeout(1.0)
            try:
                connected = s.connect_ex((address, port_to_check)) == 0
            except OSError:
                connected = False
        assert not connected, "forbidden-network-connection:" + case
    print(json.dumps({"network_probed": role, "valid_mcp_authorized": True,
                      "denied_tcp_cases": 3}))


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python combined_auth_client.py all|owner|external [tokens]")
    if sys.argv[1] == "all":
        asyncio.run(verify_owner_external())
        return
    if len(sys.argv) != 3:
        raise SystemExit("missing-test-token-directory")
    asyncio.run(verify_network_client(sys.argv[1], sys.argv[2]))


if __name__ == "__main__":
    main()
