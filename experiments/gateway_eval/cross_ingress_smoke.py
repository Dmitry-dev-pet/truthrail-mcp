"""Verify OWNER and EXTERNAL MCP traffic cannot cross privilege boundaries.

Single official Agentgateway v1.6.0 process, two independent route authN/authZ
policies and listener ports, ONE stateful Peer Chat backend.
No real ChatGPT tunnel, Cloudflare edge, VPS, user tokens or Atlas.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
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
from .peer_smoke import digest_result, name_of, has_tool

OWNER = "http://127.0.0.1:18766/mcp"
EXTERNAL = "http://127.0.0.1:18769/mcp"
BACKEND = "http://127.0.0.1:18765/mcp"
JWKS_READY = "http://127.0.0.1:18767/owner/jwks.json"

EXTERNAL_PERMS = [
    ("chat_join", "chat:join"),
    ("chat_send", "chat:send"),
    ("chat_wait", "chat:wait"),
    ("chat_status", "chat:status"),
]


def make_config(*, allow_send=True) -> str:
    external_rules = [
        f'      - \x27mcp.tool.name == "{name}" && "{scope}" in jwt.scopes\x27'
        for name, scope in EXTERNAL_PERMS
        if allow_send or name != "chat_send"
    ]
    # Explicit route bindings, not user-controlled headers/Host/path selectors.
    # Different issuers, JWKS keys, audiences AND tool policies.
    lines = [
        "# OFF-VPS TEST ONLY: no real owner or external credentials",
        "config:",
        "  adminAddr: 127.0.0.1:15078",
        "gateways:",
        "  native-owner:",
        "    bindAddress: 127.0.0.1",
        "    port: 18766",
        "  cloudflare-external:",
        "    bindAddress: 127.0.0.1",
        "    port: 18769",
        "routes:",
        "- name: native-owner-route",
        "  gateways: [native-owner]",
        "  matches:",
        "  - path:",
        "      pathPrefix: /mcp",
        "  policies:",
        "    mcpAuthentication:",
        "      mode: strict",
        "      issuer: http://127.0.0.1:18767/owner",
        '      audiences: ["http://127.0.0.1:18766/mcp"]',
        "      jwks:",
        "        url: http://127.0.0.1:18767/owner/jwks.json",
        "      resourceMetadata:",
        "        resource: http://127.0.0.1:18766/mcp",
        "        scopesSupported: [alfred:owner]",
        "        bearerMethodsSupported: [header]",
        "    mcpAuthorization:",
        "      rules:",
        "      - 'jwt.sub == \"approved-owner\" && \"alfred:owner\" in jwt.scopes'",
        "  backends:",
        "  - mcp:",
        "      targets:",
        "      - name: shared-peer-chat",
        "        mcp:",
        "          host: http://127.0.0.1:18765/mcp",
        "- name: external-scoped-route",
        "  gateways: [cloudflare-external]",
        "  matches:",
        "  - path:",
        "      pathPrefix: /mcp",
        "  policies:",
        "    mcpAuthentication:",
        "      mode: strict",
        "      issuer: http://127.0.0.1:18767/external",
        '      audiences: ["http://127.0.0.1:18769/mcp"]',
        "      jwks:",
        "        url: http://127.0.0.1:18767/external/jwks.json",
        "      resourceMetadata:",
        "        resource: http://127.0.0.1:18769/mcp",
        "        scopesSupported: [chat:join, chat:send, chat:wait, chat:status]",
        "        bearerMethodsSupported: [header]",
        "    mcpAuthorization:",
        "      rules:",
        *external_rules,
        "  backends:",
        "  - mcp:",
        "      targets:",
        "      - name: shared-peer-chat",
        "        mcp:",
        "          host: http://127.0.0.1:18765/mcp",
        "",
    ]
    return "\n".join(lines)


async def client(stack, url, token=None, forged_headers=None):
    headers = {}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    if forged_headers:
        headers.update(forged_headers)
    http = await stack.enter_async_context(httpx2.AsyncClient(
        headers=headers,
        timeout=httpx2.Timeout(10.0, read=25.0),
        trust_env=False,
    ))
    return await stack.enter_async_context(Client(
        streamable_http_client(url, http_client=http),
        raise_exceptions=True,
    ))


async def tool_names(url, token=None, forged_headers=None) -> list[str]:
    async with AsyncExitStack() as stack:
        conn = await client(stack, url, token, forged_headers)
        return [t.name for t in (await conn.list_tools()).tools]


async def must_reject_connection(url, token=None, forged_headers=None, label=""):
    try:
        names = await tool_names(url, token, forged_headers)
    except Exception:
        return
    # No credential or full room message is logged, only the exact case label.
    raise AssertionError("wrong-identity-admitted:" + label +
                         ":visible-tool-count=" + str(len(names)))


async def must_reject_call(url, token, method, args=None, forged_headers=None):
    try:
        async with AsyncExitStack() as stack:
            conn = await client(stack, url, token, forged_headers)
            result = await conn.call_tool(method, args or {})
            if result.is_error:
                return
            obj = digest_result(result)
            if obj.get("status") == "denied":
                return
    except Exception:
        return
    raise AssertionError("unauthorized tool-call reached backend")


def token_map(root: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="ascii").strip()
            for p in root.glob("*.jwt")}


async def wait_ready(port, token, expected):
    for _ in range(75):
        try:
            names = await tool_names(port, token)
            if expected in names or has_tool(names, expected):
                return
        except Exception:
            pass
        await asyncio.sleep(0.12)
    raise RuntimeError("owner-or-external-gateway-not-ready")


async def exercise(owner_token, ext_writer, ext_reader, ext_owner_claim, tokens,
                   path, gatewayproc, backendproc):
    async with AsyncExitStack() as stack:
        owner = await client(stack, OWNER, owner_token)
        external = await client(stack, EXTERNAL, ext_writer)
        reader = await client(stack, EXTERNAL, ext_reader)
        names_owner = [t.name for t in (await owner.list_tools()).tools]
        names_external = [t.name for t in (await external.list_tools()).tools]
        names_read = [t.name for t in (await reader.list_tools()).tools]

        expected_owner = {
            "chat_create", "chat_join", "chat_send", "chat_wait",
            "chat_status", "chat_close", "owner_marker",
        }
        for tool in expected_owner:
            if not has_tool(names_owner, tool):
                raise AssertionError("owner-did-not-receive-approved-catalog:"+tool)
        if len(names_owner) != len(expected_owner):
            raise AssertionError("owner-catalog-unexpected-size")
        for suffix in ("chat_join", "chat_send", "chat_wait", "chat_status"):
            assert has_tool(names_external, suffix)
        for suffix in ("chat_create", "chat_close", "owner_marker"):
            assert not has_tool(names_external, suffix), "external-can-see-owner-tool"
        assert len(names_external) == 4
        assert len(names_read) == 1 and has_tool(names_read, "chat_status")

        owner_tool = name_of(names_owner, "owner_marker")
        marker = digest_result(await owner.call_tool(owner_tool, {}))
        assert marker == {
            "synthetic": True, "marker": "owner-catalog-fixture-only"
        }, "owner-private-tool-not-callable"

        # Even a token issued with the literal owner scope string by the
        # EXTERNAL trust root must not elevate privileges.
        fake = await tool_names(EXTERNAL, ext_owner_claim, {
            "X-Alfred-Role": "owner",
            "X-Forwarded-User": "approved-owner",
            "X-Original-URI": "/mcp",
        })
        assert has_tool(fake, "chat_status") and len(fake) == 1
        assert not has_tool(fake, "owner_marker")
        await must_reject_call(
            EXTERNAL, ext_owner_claim, owner_tool, {},
            {"X-Alfred-Role": "owner", "X-Forwarded-User": "approved-owner"}
        )
        await must_reject_call(EXTERNAL, ext_writer, owner_tool)
        await must_reject_call(EXTERNAL, ext_writer,
                               name_of(names_owner, "chat_create"), {})
        await must_reject_call(EXTERNAL, ext_reader,
                               name_of(names_external, "chat_send"),
                               {"room_id": "f" * 32,
                                "ticket": "invalid",
                                "message": "no-scope"})
        # Both listeners are reachable on the SAME runner loopback:
        # this proves each listener's auth denies the WRONG identity,
        # NOT physical network isolation (a separate rollout gate).
        tests = [
            ("no-token-owner", OWNER, None),
            ("no-token-external", EXTERNAL, None),
            ("external-writer-to-owner", OWNER, ext_writer),
            ("external-forged-owner-to-owner", OWNER, ext_owner_claim),
            ("owner-token-to-external", EXTERNAL, owner_token),
            ("owner-token-wrong-audience", OWNER,
             tokens["owner_wrong_audience.jwt"]),
            ("external-token-wrong-audience", EXTERNAL,
             tokens["external_wrong_audience.jwt"]),
            ("expired-owner", OWNER, tokens["owner_expired.jwt"]),
            ("expired-external", EXTERNAL, tokens["external_expired.jwt"]),
            ("bad-signature", OWNER, ext_writer[:-4] + "oops"),
        ]
        for label, url, tok in tests:
            await must_reject_connection(
                url, tok,
                {"X-Alfred-Role": "owner", "X-Forwarded-User": "approved-owner",
                 "X-Forwarded-For": "127.0.0.1"},
                label=label
            )

        # Both separate listeners still talk to the SAME bounded mailbox.
        create = digest_result(await owner.call_tool(
            name_of(names_owner, "chat_create"), {}
        ))
        room, ticket_a, invite = create["room_id"], create["a_ticket"], create["b_invite"]
        joined = digest_result(await external.call_tool(
            name_of(names_external, "chat_join"),
            {"room_id": room, "invitation": invite}
        ))
        ticket_b = joined["b_ticket"]
        pending = asyncio.create_task(owner.call_tool(
            name_of(names_owner, "chat_wait"),
            {"room_id": room, "ticket": ticket_a,
             "after_id": 0, "wait_seconds": 5}
        ))
        await asyncio.sleep(.15)
        assert not pending.done(), "native-owner-long-poll-not-blocked"
        sent = digest_result(await external.call_tool(
            name_of(names_external, "chat_send"),
            {"room_id": room, "ticket": ticket_b,
             "message": "external-inert-message"}
        ))
        got = digest_result(await asyncio.wait_for(pending, timeout=8))
        assert got["from"] == "B" and got["id"] == sent["id"]
        assert got["message"] == "external-inert-message"
        status = digest_result(await reader.call_tool(
            name_of(names_read, "chat_status"),
            {"room_id": room, "ticket": ticket_b}
        ))
        assert status["message_count"] == 1
        # Resource access cannot be obtained with invalid/mismatched tickets.
        invalid = digest_result(await reader.call_tool(
            name_of(names_read, "chat_status"),
            {"room_id": room, "ticket": "invalid"}
        ))
        assert invalid["status"] == "denied"

        # Remove permission from ONLY the external route while still allowing
        # every approved owner tool, and without resetting the active room.
        before_pid = gatewayproc.pid
        backend_pid = backendproc.pid
        write_config(path, make_config(allow_send=False))
        for _ in range(60):
            if gatewayproc.poll() is not None or backendproc.poll() is not None:
                raise RuntimeError("policy-change-restarted-or-crashed-process")
            try:
                ext_names = await tool_names(EXTERNAL, ext_writer)
                if not has_tool(ext_names, "chat_send"):
                    break
            except Exception:
                pass
            await asyncio.sleep(.15)
        else:
            raise AssertionError("external-send-not-revoked-on-hot-policy-change")
        await must_reject_call(EXTERNAL, ext_writer,
                               name_of(names_external, "chat_send"),
                               {"room_id": room, "ticket": ticket_b,
                                "message": "revoked"})
        owner_after = await tool_names(OWNER, owner_token)
        assert has_tool(owner_after, "owner_marker")
        assert has_tool(owner_after, "chat_send")
        assert gatewayproc.pid == before_pid and backendproc.pid == backend_pid

        write_config(path, make_config(allow_send=True))
        for _ in range(60):
            if gatewayproc.poll() is not None or backendproc.poll() is not None:
                raise RuntimeError("policy-rollback-crashed-process")
            try:
                ext_names = await tool_names(EXTERNAL, ext_writer)
                if has_tool(ext_names, "chat_send"):
                    break
            except Exception:
                pass
            await asyncio.sleep(.15)
        else:
            raise AssertionError("external-send-not-restored-on-rollback")
        # The in-memory room state survived both policy changes.
        again = digest_result(await external.call_tool(
            name_of(names_external, "chat_send"),
            {"room_id": room, "ticket": ticket_b,
             "message": "restored-scope", "reply_to": sent["id"] if False else None}
        ))
        assert again["status"] == "sent"
        got_second = digest_result(await owner.call_tool(
            name_of(names_owner, "chat_wait"),
            {"room_id": room, "ticket": ticket_a,
             "after_id": sent["id"], "wait_seconds": 0}
        ))
        assert got_second["message"] == "restored-scope"
        closed = digest_result(await owner.call_tool(
            name_of(names_owner, "chat_close"),
            {"room_id": room, "ticket": ticket_a}
        ))
        assert closed["status"] == "closed"
        return {
            "owner_tool_list_count": len(names_owner),
            "external_write_tools": len(names_external),
            "external_read_tools": len(names_read),
            "cross_port_wrong_token_denials": len(tests),
            "cross_route_policy_reload": True,
            "shared_room": True,
            "gateway_rss_mib": rss_mib(gatewayproc.pid),
            "backend_rss_mib": rss_mib(backendproc.pid)
        }


async def main(binary):
    with tempfile.TemporaryDirectory(prefix="alfred-two-ingress-test-") as td:
        root = Path(td)
        jwks_dir = root / "jwt"
        issuer = subprocess.Popen(
            [sys.executable, "-m", "experiments.gateway_eval.dual_issuer",
             str(jwks_dir)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        backend = subprocess.Popen(
            [sys.executable, "-m", "experiments.gateway_eval.peer_backend"],
            env={**__import__("os").environ, "ALFRED_OWNER_ONLY_FIXTURE": "1"},
            stdout=(root / "backend.log").open("w"),
            stderr=subprocess.STDOUT,
        )
        gateway = None
        try:
            for _ in range(80):
                try:
                    with urlopen(JWKS_READY, timeout=.3) as response:
                        if response.status == 200:
                            break
                except Exception:
                    pass
                await asyncio.sleep(.12)
            else:
                raise RuntimeError("ephemeral-JWKS-issuer-not-started")

            for _ in range(80):
                try:
                    async with Client(BACKEND, raise_exceptions=True) as b:
                        names = [t.name for t in (await b.list_tools()).tools]
                        if has_tool(names, "owner_marker"):
                            break
                except Exception:
                    pass
                await asyncio.sleep(.12)
            else:
                raise RuntimeError("single-shared-mcp-backend-not-started")

            cfg = root / "gateway.yaml"
            write_config(cfg, make_config())
            validation = subprocess.run(
                [str(binary), "--validate-only", "-f", str(cfg)],
                capture_output=True, text=True, timeout=12
            )
            if validation.returncode:
                raise RuntimeError("gateway-dual-route-config-rejected")

            gateway = subprocess.Popen(
                [str(binary), "-f", str(cfg)],
                stdout=(root / "gateway.log").open("w"),
                stderr=subprocess.STDOUT,
            )
            tokens = token_map(jwks_dir)
            assert len(tokens) == 8, "fixture-issuer-incomplete"
            await wait_ready(OWNER, tokens["owner.jwt"], "owner_marker")
            await wait_ready(EXTERNAL, tokens["external_writer.jwt"], "chat_send")
            check = await exercise(tokens["owner.jwt"],
                tokens["external_writer.jwt"],
                tokens["external_reader.jwt"],
                tokens["external_claims_owner.jwt"], tokens,
                cfg, gateway, backend
            )
            print(json.dumps({
                "experiment": "one-agentgateway-two-independent-ingress-policies",
                "pinned_upstream": "agentgateway-v1.6.0",
                "verified": True,
                "owner_catalog_full": True,
                "external_owner_tool_hidden": True,
                "external_owner_tool_call_denied": True,
                "forged_owner_headers_ignored": True,
                "distinct_jwt_issuers": True,
                "distinct_audiences": True,
                "owner_route_rejects_external_tokens": True,
                "external_route_rejects_owner_token": True,
                "no_token_rejected": True,
                "expired_tokens_rejected": True,
                "policy_change_owner_unchanged": True,
                "physical_network_isolation_tested": False,
                "native_chatgpt_tested": False,
                "cloudflare_connected": False,
                "vps_mutated": False,
                **check,
            }, sort_keys=True))
        finally:
            for process in (gateway, backend, issuer):
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m experiments.gateway_eval.cross_ingress_smoke BIN")
    path = Path(sys.argv[1]).resolve()
    if not path.is_file():
        raise SystemExit("verified-Agentgateway-binary-missing")
    asyncio.run(main(path))
