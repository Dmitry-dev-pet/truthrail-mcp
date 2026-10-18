"""Independent HTTP MCP clients on segregated Docker networks.

Use only with a temporary native owner API key and external CI-signed JWTs.
The real production entrypoint must be exercised, not bypassed.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket
import sys
import time

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

OWNER_URL = "http://172.30.143.10:8765/mcp"
EXTERNAL_URL = "http://172.30.144.10:8766/mcp"
ROOM = Path("/run/test-session")
TOKENS = Path("/run/test-auth")
A_SET = {"chat_create", "chat_join", "chat_send",
         "chat_wait", "chat_status", "chat_close"}
B_SET = {"chat_join", "chat_send", "chat_wait", "chat_status"}


def local_token(name: str) -> str:
    assert name in {
        "owner.key", "external_writer.jwt", "external_reader.jwt",
        "external_claims_owner.jwt", "external_expired.jwt", "external_bad_aud.jwt"
    }
    return (TOKENS / name).read_text(encoding="ascii").strip()


async def connect(url, bearer):
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + bearer},
        timeout=httpx2.Timeout(10.0, read=20.0),
        trust_env=False,
    ) as http:
        async with Client(streamable_http_client(url, http_client=http),
                          raise_exceptions=True) as client:
            yield client


def unpack(response):
    if response.is_error:
        raise AssertionError("allowed-tool-call-error")
    item = getattr(response, "structured_content", None)
    if item is None:
        items = [c.text for c in response.content if getattr(c, "type", "") == "text"]
        assert len(items) == 1
        item = json.loads(items[0])
    if type(item) is dict and type(item.get("result")) is dict:
        item = item["result"]
    assert type(item) is dict
    return item


def full_name(names, suffix):
    xs = [x for x in names if x == suffix or x.endswith("_" + suffix)]
    assert len(xs) == 1, "tool-schema-drift:" + suffix
    return xs[0]


async def denied(client, name, arguments=None):
    try:
        reply = await client.call_tool(name, arguments or {})
        if reply.is_error:
            return
        val = unpack(reply)
        if val.get("status") == "denied":
            return
    except Exception:
        return
    raise AssertionError("forbidden-tool-call-success")


async def expected_auth_refusal(url, credential, *, owner_custom=False):
    try:
        if credential is None:
            headers = {}
        elif owner_custom:
            headers = {"X-Alfred-Native-Owner-Key": credential}
        else:
            headers = {"Authorization": "Bearer " + credential}
        async with httpx2.AsyncClient(
            headers=headers, timeout=5.0, trust_env=False,
        ) as transport:
            async with Client(streamable_http_client(url, http_client=transport),
                              raise_exceptions=True) as cl:
                result = (await cl.list_tools()).tools
                raise AssertionError("invalid-identity-allowed:"+str(len(result)))
    except AssertionError:
        raise
    except Exception:
        pass


def publish(filename, obj):
    pending = ROOM / (filename + ".next")
    pending.write_text(json.dumps(obj), encoding="utf-8")
    pending.replace(ROOM / filename)


async def load(filename, timeout=10):
    path = ROOM / filename
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        await asyncio.sleep(.05)
    raise AssertionError("missing-peer-marker:"+filename)


def net_check(role):
    other = "172.30.144.10" if role == "owner" else "172.30.143.10"
    own = "172.30.143.10" if role == "owner" else "172.30.144.10"
    bad_port = 8766 if role == "owner" else 8765
    for host, port in [
        (other, bad_port), (own, bad_port), (own, 18765),
    ]:
        with socket.socket() as s:
            s.settimeout(.7)
            try:
                connected = s.connect_ex((host, port)) == 0
            except OSError:
                connected = False
        assert not connected, "cross-network-path-open"


async def actor(role):
    net_check(role)
    if role == "owner":
        native_url, credential, expected = OWNER_URL, local_token("owner.key"), A_SET
        headers = {
            "X-Alfred-Native-Owner-Key": credential,
            "Authorization": "Bearer unrelated-connector-authentication",
        }
    else:
        native_url, credential, expected = EXTERNAL_URL, local_token("external_writer.jwt"), B_SET
        headers = {"Authorization": "Bearer " + credential}

    async with httpx2.AsyncClient(
        headers=headers,
        timeout=httpx2.Timeout(10.0, read=20.0),
        trust_env=False,
    ) as http:
        async with Client(streamable_http_client(native_url, http_client=http),
                          raise_exceptions=True) as c:
            names = [x.name for x in (await c.list_tools()).tools]
            assert len(names) == len(expected)
            for tool in expected:
                full_name(names, tool)
            if role == "owner":
                await expected_auth_refusal(OWNER_URL, local_token("external_writer.jwt"))
                await expected_auth_refusal(OWNER_URL, "wrong-owner-key", owner_custom=True)
                await expected_auth_refusal(OWNER_URL, None)
                await expected_auth_refusal(
                    OWNER_URL, "forged-owner-with-same-header", owner_custom=True
                )
                result = unpack(await c.call_tool(full_name(names, "chat_create"), {}))
                publish("room.json", result)
                await load("joined.json")
                last_a, last_b = 0, 0
                for i in range(1, 4):
                    await load("external_ready_" + str(i))
                    await asyncio.sleep(.15)
                    sent = unpack(await c.call_tool(full_name(names, "chat_send"), {
                        "room_id": result["room_id"], "ticket": result["a_ticket"],
                        "message": "owner-turn-" + str(i),
                        **({"reply_to": last_a} if last_a else {}),
                    }))
                    last_b = sent["id"]
                    recv = unpack(await c.call_tool(full_name(names, "chat_wait"), {
                        "room_id": result["room_id"], "ticket": result["a_ticket"],
                        "after_id": last_a, "wait_seconds": 6,
                    }))
                    assert recv["from"] == "B" and recv["message"] == "external-turn-" + str(i)
                    last_a = recv["id"]
                    publish("owner_round_" + str(i), {"ok": True})
                result2 = unpack(await c.call_tool(full_name(names, "chat_close"), {
                    "room_id": result["room_id"], "ticket": result["a_ticket"],
                }))
                assert result2["status"] == "closed"
                publish("closed.json", {"ok": True})
            else:
                await expected_auth_refusal(
                    EXTERNAL_URL, local_token("owner.key"), owner_custom=True
                )
                await expected_auth_refusal(EXTERNAL_URL, local_token("external_bad_aud.jwt"))
                await expected_auth_refusal(EXTERNAL_URL, local_token("external_expired.jwt"))
                await expected_auth_refusal(EXTERNAL_URL, None)
                await denied(c, "peer-chat_chat_create")
                await denied(c, "peer-chat_chat_close")
                # Verify a second external JWT has its OWN, strictly smaller
                # tool scope. An external token claiming alfred:owner remains
                # external: never allow it to inherit native owner tools.
                for candidate in ("external_reader.jwt", "external_claims_owner.jwt"):
                    async with httpx2.AsyncClient(
                        headers={"Authorization": "Bearer " + local_token(candidate)},
                        timeout=httpx2.Timeout(8.0, read=20.0),
                        trust_env=False,
                    ) as limited_http:
                        async with Client(streamable_http_client(
                            EXTERNAL_URL, http_client=limited_http,
                        ), raise_exceptions=True) as limited:
                            visible = [t.name for t in (await limited.list_tools()).tools]
                            assert len(visible) == 1
                            assert full_name(visible, "chat_status")
                            await denied(limited, "peer-chat_chat_send")
                            await denied(limited, "peer-chat_chat_create")
                            await denied(limited, "peer-chat_chat_close")
                room = await load("room.json")
                joined = unpack(await c.call_tool(full_name(names, "chat_join"), {
                    "room_id": room["room_id"], "invitation": room["b_invite"],
                }))
                assert joined["role"] == "B"
                await denied(c, full_name(names, "chat_join"), {
                    "room_id": room["room_id"], "invitation": room["b_invite"],
                })
                publish("joined.json", {"ok": True})
                last_b = 0
                for i in range(1, 4):
                    publish("external_ready_" + str(i), {"ok": True})
                    recv = unpack(await c.call_tool(full_name(names, "chat_wait"), {
                        "room_id": room["room_id"], "ticket": joined["b_ticket"],
                        "after_id": last_b, "wait_seconds": 6,
                    }))
                    assert recv["from"] == "A" and recv["message"] == "owner-turn-" + str(i)
                    last_b = recv["id"]
                    sent = unpack(await c.call_tool(full_name(names, "chat_send"), {
                        "room_id": room["room_id"], "ticket": joined["b_ticket"],
                        "message": "external-turn-" + str(i),
                        "reply_to": last_b,
                    }))
                    assert sent["status"] == "sent"
                    await load("owner_round_" + str(i))
                await load("closed.json")
    print(json.dumps({"actor": role, "scoped_tools": len(expected),
                      "auth_refusals": 4, "network_denials": 3, "round_trips": 3,
                      "native_tunnel_or_vps_accessed": False}))


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("owner", "external"):
        raise SystemExit("usage: probe.py owner|external")
    asyncio.run(actor(sys.argv[1]))


if __name__ == "__main__":
    main()
