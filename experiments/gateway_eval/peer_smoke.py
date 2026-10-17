"""Local only: one Agentgateway, one Peer Chat MCP, two real HTTP MCP clients.

No Atlas, VPS, tunnel, production auth, persistent secrets or external API.
"""
from __future__ import annotations

from contextlib import AsyncExitStack
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from .smoke import write_config, rss_mib

URL = "http://127.0.0.1:18766/mcp"
BACKEND = "http://127.0.0.1:18765/mcp"
JWKS = "http://127.0.0.1:18767/jwks.json"

SCOPES = {
    "chat_create": "chat:create",
    "chat_join": "chat:join",
    "chat_send": "chat:send",
    "chat_wait": "chat:wait",
    "chat_status": "chat:status",
    "chat_close": "chat:close",
}


def config(status_enabled: bool) -> str:
    rules = [
        f"      - 'mcp.tool.name == \"{tool}\" && \"{scope}\" in jwt.scopes'"
        for tool, scope in SCOPES.items()
        if status_enabled or tool != "chat_status"
    ]
    return "\n".join([
        "# GitHub-hosted CI only, all services loopback, no production keys",
        "config:",
        "  adminAddr: 127.0.0.1:15078",
        "mcp:",
        "  port: 18766",
        "  statefulMode: stateless",
        "  policies:",
        "    mcpAuthentication:",
        "      mode: strict",
        "      issuer: http://127.0.0.1:18767",
        '      audiences: ["http://127.0.0.1:18766/mcp"]',
        "      jwks:",
        "        url: http://127.0.0.1:18767/jwks.json",
        "      resourceMetadata:",
        "        resource: http://127.0.0.1:18766/mcp",
        "        scopesSupported: [chat:create, chat:join, chat:send, chat:wait, chat:status, chat:close]",
        "        bearerMethodsSupported: [header]",
        "    mcpAuthorization:",
        "      rules:",
        *rules,
        "  targets:",
        "  - name: peer-chat",
        "    mcp:",
        "      host: http://127.0.0.1:18765/mcp",
        "",
    ])


def digest_result(reply):
    assert not reply.is_error, "MCP-reported-error"
    data = getattr(reply, "structured_content", None)
    if data is None:
        chunks = [part.text for part in reply.content
                  if getattr(part, "type", "") == "text"]
        assert len(chunks) == 1, "invalid-content"
        data = json.loads(chunks[0])
    if type(data) is dict and type(data.get("result")) is dict:
        data = data["result"]
    assert type(data) is dict, "non-object-tool-reply"
    return data


async def wait_service(backend):
    for _ in range(80):
        if backend.poll() is not None:
            raise RuntimeError("peer-backend-exited")
        try:
            async with Client(BACKEND, raise_exceptions=True) as probe:
                names = {x.name for x in (await probe.list_tools()).tools}
                if names == set(SCOPES):
                    return
        except Exception:
            pass
        await asyncio.sleep(0.15)
    raise RuntimeError("peer-backend-unavailable")


def name_of(names, suffix):
    matches = [name for name in names if name == suffix or name.endswith("_" + suffix)]
    assert len(matches) == 1, "unexpected-tool-name"
    return matches[0]


def has_tool(names, suffix):
    return any(name == suffix or name.endswith("_" + suffix) for name in names)


async def create_client(stack, token):
    client = await stack.enter_async_context(httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + token},
        timeout=httpx2.Timeout(10.0, read=25.0),
        trust_env=False,
    ))
    return await stack.enter_async_context(Client(
        streamable_http_client(URL, http_client=client), raise_exceptions=True
    ))


async def denied(client, full_tool_name, args):
    try:
        reply = await client.call_tool(full_tool_name, args)
    except Exception:
        return True
    obj = digest_result(reply) if not reply.is_error else {}
    return reply.is_error or obj.get("status") == "denied"


async def execute_test(tokens, *, status_enabled: bool):
    async with AsyncExitStack() as stack:
        a = await create_client(stack, tokens["a.jwt"])
        b = await create_client(stack, tokens["b.jwt"])
        observer = await create_client(stack, tokens["observer.jwt"])
        a_names = {t.name for t in (await a.list_tools()).tools}
        b_names = {t.name for t in (await b.list_tools()).tools}
        o_names = {t.name for t in (await observer.list_tools()).tools}
        assert {name.rsplit("_", 2)[-1] for name in a_names} # nonempty
        for role, names, allowed in [
            ("A", a_names, {"chat_create", "chat_send", "chat_wait", "chat_close"}),
            ("B", b_names, {"chat_join", "chat_send", "chat_wait"}),
            ("Observer", o_names, {"chat_status"} if status_enabled else set()),
        ]:
            for tool in SCOPES:
                assert has_tool(names, tool) == (tool in allowed), (
                    f"scoped-discovery-failed-{role}-{tool}"
                )
        assert not any(has_tool(names, "dataset_info")
                       for names in (a_names, b_names, o_names)), "atlas-must-be-absent"
        if not status_enabled:
            return None
        return a, b, observer


async def exchange_and_hot_reload(gateway, backend, cfg, tokens):
    # Scoped discovery; no room data printed or serialized to GitHub logs.
    async with AsyncExitStack() as stack:
        a = await create_client(stack, tokens["a.jwt"])
        b = await create_client(stack, tokens["b.jwt"])
        observer = await create_client(stack, tokens["observer.jwt"])
        tool_a = [t.name for t in (await a.list_tools()).tools]
        tool_b = [t.name for t in (await b.list_tools()).tools]
        tool_o = [t.name for t in (await observer.list_tools()).tools]
        for tool in ("chat_create", "chat_send", "chat_wait", "chat_close"):
            assert has_tool(tool_a, tool)
        for tool in ("chat_join", "chat_send", "chat_wait"):
            assert has_tool(tool_b, tool)
        assert not has_tool(tool_b, "chat_create")
        assert not has_tool(tool_a, "chat_join")
        assert not tool_o, "observer-should-see-no-tool-before-policy"
        assert not has_tool(tool_a, "dataset_info"), "atlas-not-excluded"

        a_tool = lambda tail: name_of(tool_a, tail)
        b_tool = lambda tail: name_of(tool_b, tail)
        created = digest_result(await a.call_tool(a_tool("chat_create"), {}))
        room = created["room_id"]
        a_ticket, invite = created["a_ticket"], created["b_invite"]
        assert len(room) == 32 and len(a_ticket) >= 32 and len(invite) >= 32

        assert await denied(b, a_tool("chat_create"), {}), "b-must-not-create-room"
        assert await denied(b, b_tool("chat_send"), {
            "room_id": room, "ticket": invite, "message": "attempt-invite-as-role"
        }), "invitation-must-not-act-as-send-ticket"

        joined = digest_result(await b.call_tool(b_tool("chat_join"), {
            "room_id": room, "invitation": invite
        }))
        b_ticket = joined["b_ticket"]
        assert joined["role"] == "B"
        assert await denied(b, b_tool("chat_join"), {
            "room_id": room, "invitation": invite
        }), "invitation-reuse-must-fail"

        last_a, last_b = 0, 0
        start = time.monotonic()
        for i in range(3):
            waiting_b = asyncio.create_task(b.call_tool(b_tool("chat_wait"), {
                "room_id": room, "ticket": b_ticket,
                "after_id": last_b, "wait_seconds": 4
            }))
            await asyncio.sleep(0.12)
            assert not waiting_b.done(), "b-long-poll-did-not-block"
            sent_a = digest_result(await a.call_tool(a_tool("chat_send"), {
                "room_id": room, "ticket": a_ticket,
                "message": f"turn-A-{i + 1}",
                **({"reply_to": last_a} if last_a else {})
            }))
            got_b = digest_result(await asyncio.wait_for(waiting_b, timeout=8))
            assert got_b["status"] == "message" and got_b["id"] == sent_a["id"]
            assert got_b["message"] == f"turn-A-{i + 1}"
            assert got_b["from"] == "A"
            last_b = got_b["id"]

            waiting_a = asyncio.create_task(a.call_tool(a_tool("chat_wait"), {
                "room_id": room, "ticket": a_ticket,
                "after_id": last_a, "wait_seconds": 4
            }))
            await asyncio.sleep(0.12)
            assert not waiting_a.done(), "a-long-poll-did-not-block"
            sent_b = digest_result(await b.call_tool(b_tool("chat_send"), {
                "room_id": room, "ticket": b_ticket,
                "message": f"turn-B-{i + 1}", "reply_to": last_b
            }))
            got_a = digest_result(await asyncio.wait_for(waiting_a, timeout=8))
            assert got_a["status"] == "message" and got_a["id"] == sent_b["id"]
            assert got_a["message"] == f"turn-B-{i + 1}"
            assert got_a["from"] == "B"
            last_a = got_a["id"]
        assert time.monotonic() - start >= 0.65, "not-a-blocking-test"

        # No duplicate delivered after cursor. A room ticket is resource-specific.
        idle = digest_result(await a.call_tool(a_tool("chat_wait"), {
            "room_id": room, "ticket": a_ticket, "after_id": last_a,
            "wait_seconds": 1
        }))
        assert idle["status"] == "timeout", "wrong-cursor-timeout"
        assert await denied(b, b_tool("chat_send"), {
            "room_id": room, "ticket": "invalid-ticket",
            "message": "forged-role"
        }), "ticket-not-enforced"
        assert await denied(b, b_tool("chat_send"), {
            "room_id": "f" * 32, "ticket": b_ticket,
            "message": "cross-room"
        }), "room-boundary-not-enforced"
        assert await denied(observer, b_tool("chat_send"), {
            "room_id": room, "ticket": a_ticket, "message": "no-grant"
        }), "observer-should-have-no-write-scope"

        gateway_pid, backend_pid = gateway.pid, backend.pid
        config_before = cfg.read_text()
        write_config(cfg, config(status_enabled=True))
        async def observer_has_status():
            async with AsyncExitStack() as newstack:
                fresh = await create_client(newstack, tokens["observer.jwt"])
                names = [x.name for x in (await fresh.list_tools()).tools]
                if not has_tool(names, "chat_status"):
                    return False
                metadata = digest_result(await fresh.call_tool(
                    name_of(names, "chat_status"),
                    {"room_id": room, "ticket": b_ticket}
                ))
                assert metadata["message_count"] == 6
                assert metadata["role"] == "B"
                assert metadata["joined"] is True
                assert await denied(fresh, b_tool("chat_send"), {
                    "room_id": room, "ticket": b_ticket, "message": "no"
                })
                return True

        for _ in range(50):
            if gateway.poll() is not None or backend.poll() is not None:
                raise RuntimeError("process-died-on-hot-reload")
            if await observer_has_status():
                break
            await asyncio.sleep(0.15)
        else:
            raise RuntimeError("policy-hot-reload-failed")

        assert gateway.pid == gateway_pid and backend.pid == backend_pid
        rss_hot = (rss_mib(gateway_pid), rss_mib(backend_pid))
        write_config(cfg, config(status_enabled=False))

        async def observer_has_no_status():
            async with AsyncExitStack() as newstack:
                fresh = await create_client(newstack, tokens["observer.jwt"])
                names = [x.name for x in (await fresh.list_tools()).tools]
                return not has_tool(names, "chat_status")

        for _ in range(50):
            if gateway.poll() is not None or backend.poll() is not None:
                raise RuntimeError("process-died-on-policy-rollback")
            if await observer_has_no_status():
                break
            await asyncio.sleep(0.15)
        else:
            raise RuntimeError("policy-rollback-failed")
        assert gateway.pid == gateway_pid and backend.pid == backend_pid
        closed = digest_result(await a.call_tool(a_tool("chat_close"), {
            "room_id": room, "ticket": a_ticket
        }))
        assert closed["status"] == "closed"
        assert await denied(b, b_tool("chat_send"), {
            "room_id": room, "ticket": b_ticket, "message": "after-close"
        }), "closed-room-still-writable"
        return rss_hot


async def probe(binary, folder):
    tokens_dir = folder / "ephemeral_tokens"
    keyproc = subprocess.Popen(
        [sys.executable, "-m", "experiments.gateway_eval.key_server", str(tokens_dir)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    backendproc = subprocess.Popen(
        [sys.executable, "-m", "experiments.gateway_eval.peer_backend"],
        stdout=(folder / "backend.log").open("w"), stderr=subprocess.STDOUT,
    )
    gatewayproc = None
    try:
        for _ in range(75):
            try:
                with urlopen(JWKS, timeout=0.3) as response:
                    if response.status == 200:
                        break
            except Exception:
                await asyncio.sleep(0.1)
        else:
            raise RuntimeError("ephemeral-JWKS-unavailable")
        await wait_service(backendproc)
        cfg = folder / "peer-only.yaml"
        write_config(cfg, config(False))
        valid = subprocess.run(
            [str(binary), "--validate-only", "-f", str(cfg)],
            capture_output=True, text=True, timeout=12
        )
        if valid.returncode:
            raise RuntimeError("peer-gateway-config-unaccepted")
        gatewayproc = subprocess.Popen(
            [str(binary), "-f", str(cfg)],
            stdout=(folder / "gateway.log").open("w"),
            stderr=subprocess.STDOUT,
        )
        tokens = {p.name: p.read_text().strip() for p in tokens_dir.glob("*.jwt")}
        for _ in range(60):
            if gatewayproc.poll() is not None:
                raise RuntimeError("gateway-exited-during-start")
            try:
                async with AsyncExitStack() as stack:
                    a = await create_client(stack, tokens["a.jwt"])
                    names = [t.name for t in (await a.list_tools()).tools]
                    if has_tool(names, "chat_create") and not has_tool(names, "dataset_info"):
                        break
            except Exception:
                pass
            await asyncio.sleep(0.15)
        else:
            raise RuntimeError("scoped-peer-gateway-unavailable")
        rss_before = (rss_mib(gatewayproc.pid), rss_mib(backendproc.pid))
        rss_hot = await exchange_and_hot_reload(gatewayproc, backendproc, cfg, tokens)
        print(json.dumps({
            "experiment": "agentgateway-v1.6.0-peer-only",
            "verified": True, "atlas_loaded": False,
            "scopes": True, "room_role_tickets": True,
            "single_shared_mcp_backend": True, "distinct_http_clients": 2,
            "round_trips": 3, "blocked_wait_both_directions": True,
            "no_duplicate_after_cursor": True,
            "jwt_tool_access_enforced": True,
            "hot_policy_change_and_rollback": True,
            "gateway_rss_before_mib": rss_before[0],
            "backend_rss_before_mib": rss_before[1],
            "gateway_rss_after_mib": rss_hot[0],
            "backend_rss_after_mib": rss_hot[1],
            "estimated_sum_rss_after_mib": round(sum(rss_hot), 2),
            "vps_changed": False, "tunnel_changed": False,
            "native_chatgpt_turn_tested": False,
        }, sort_keys=True))
    finally:
        for process in (gatewayproc, backendproc, keyproc):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m experiments.gateway_eval.peer_smoke /path/to/agentgateway")
    binary = Path(sys.argv[1]).resolve()
    if not binary.is_file():
        raise SystemExit("missing-gateway")
    with tempfile.TemporaryDirectory(prefix="alfred-peer-only-") as tmp:
        asyncio.run(probe(binary, Path(tmp)))


if __name__ == "__main__":
    main()
