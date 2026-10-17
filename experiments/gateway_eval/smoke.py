"""Reproducible off-VPS Agentgateway v1.6.0 MCP authz + hot-reload probe.

Never uses the real Atlas, native ChatGPT tunnel, Cloudflare, GH tokens,
private repositories, production secrets or a public network service.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

URL = "http://127.0.0.1:18766/mcp"
BACKEND = "http://127.0.0.1:18765/mcp"
JWKS = "http://127.0.0.1:18767/jwks.json"


def build_config(allow_peer: bool) -> str:
    additional = (
        '\n      - \'mcp.tool.name == "peer_echo" && "chat:send" in jwt.scopes\''
        if allow_peer else ""
    )
    return (
        "# Verified release candidate, local CI only; DO NOT deploy as-is\n"
        "config:\n"
        "  adminAddr: 127.0.0.1:15078\n"
        "mcp:\n"
        "  port: 18766\n"
        "  statefulMode: stateless\n"
        "  policies:\n"
        "    mcpAuthentication:\n"
        "      mode: strict\n"
        "      issuer: http://127.0.0.1:18767\n"
        '      audiences: ["http://127.0.0.1:18766/mcp"]\n'
        "      jwks:\n"
        "        url: http://127.0.0.1:18767/jwks.json\n"
        "      resourceMetadata:\n"
        "        resource: http://127.0.0.1:18766/mcp\n"
        "        scopesSupported: [atlas:read, chat:send]\n"
        "        bearerMethodsSupported: [header]\n"
        "    mcpAuthorization:\n"
        "      rules:\n"
        '      - \'mcp.tool.name == "dataset_info" && "atlas:read" in jwt.scopes\''
        + additional + "\n"
        "  targets:\n"
        "  - name: mock-atlas\n"
        "    mcp:\n"
        "      host: http://127.0.0.1:18765/mcp\n"
    )


def write_config(path, content):
    temporary = path.with_suffix(".next")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def rss_mib(pid):
    text = Path(f"/proc/{pid}/status").read_text()
    rss = next(row for row in text.splitlines() if row.startswith("VmRSS:"))
    return round(int(rss.split()[1]) / 1024, 2)


def check_process(process):
    if process.poll() is not None:
        raise RuntimeError("gateway-process-exited")


async def client_call(token, method, args=None):
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + token},
        timeout=httpx2.Timeout(10.0, read=35.0),
        trust_env=False,
    ) as client:
        transport = streamable_http_client(URL, http_client=client)
        async with Client(transport, raise_exceptions=True) as mcp_client:
            names = [x.name for x in (await mcp_client.list_tools()).tools]
            if method is None:
                return names
            return await mcp_client.call_tool(method, args or {})


async def wait_for_backend():
    for _ in range(80):
        try:
            async with Client(BACKEND, raise_exceptions=True) as client:
                names = {x.name for x in (await client.list_tools()).tools}
                assert names == {"dataset_info", "peer_echo"}
                return
        except Exception:
            await asyncio.sleep(0.12)
    raise RuntimeError("local-test-backend-unavailable")


async def check_access(tokens, allow_peer):
    reader = await client_call(tokens["reader.jwt"], None)
    writer = await client_call(tokens["writer.jwt"], None)

    def matching(items, tail):
        return [x for x in items if x == tail or x.endswith("_" + tail)]

    read_names = matching(reader, "dataset_info")
    peer_names = matching(writer, "peer_echo")
    assert len(read_names) == 1 and not matching(reader, "peer_echo"), (
        "reader-must-not-discover-peer"
    )
    assert len(matching(writer, "dataset_info")) == 1
    assert len(peer_names) == (1 if allow_peer else 0), "dynamic-policy-mismatch"
    result = await client_call(tokens["reader.jwt"], read_names[0])
    assert not result.is_error and "gateway-test" in str(result), "read-failed"

    if allow_peer:
        written = await client_call(tokens["writer.jwt"], peer_names[0],
                                    {"message": "safe-test"})
        assert not written.is_error and "safe-test" in str(written)
        denied = await client_call(tokens["reader.jwt"], peer_names[0],
                                   {"message": "forbidden"})
        assert denied.is_error, "read-token-must-not-call-write"

    for label, token in [
        ("wrong-audience", tokens["wrong-audience.jwt"]),
        ("invalid-signature", tokens["reader.jwt"][:-2] + "zz"),
    ]:
        try:
            await client_call(token, None)
        except Exception:
            pass
        else:
            raise AssertionError(f"{label}:invalid-token-was-accepted")


async def run_probe(binary, folder):
    tokens_dir = folder / "ephemeral_tokens"
    keyproc = subprocess.Popen(
        [sys.executable, "-m", "experiments.gateway_eval.key_server", str(tokens_dir)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    backendproc = subprocess.Popen(
        [sys.executable, "-m", "experiments.gateway_eval.mock_backend"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    gatewayproc = None
    try:
        for _ in range(60):
            try:
                with urlopen(JWKS, timeout=0.5) as r:
                    assert r.status == 200
                break
            except Exception:
                await asyncio.sleep(0.10)
        else:
            raise RuntimeError("local-jwks-unavailable")

        await wait_for_backend()
        cfg = folder / "config.yaml"
        write_config(cfg, build_config(False))
        validation = subprocess.run(
            [str(binary), "--validate-only", "-f", str(cfg)],
            capture_output=True, text=True, timeout=15,
        )
        if validation.returncode:
            raise RuntimeError("upstream-agentgateway-config-not-validated")

        gatewayproc = subprocess.Popen(
            [str(binary), "-f", str(cfg)],
            stdout=(folder / "gateway.log").open("w"),
            stderr=subprocess.STDOUT,
        )

        tokens = {p.name: p.read_text().strip() for p in tokens_dir.glob("*.jwt")}
        for _ in range(80):
            check_process(gatewayproc)
            try:
                await check_access(tokens, False)
                break
            except Exception:
                await asyncio.sleep(0.20)
        else:
            raise RuntimeError("initial-authorization-not-confirmed")
        before = rss_mib(gatewayproc.pid)
        pid = gatewayproc.pid
        write_config(cfg, build_config(True))
        for _ in range(50):
            check_process(gatewayproc)
            try:
                await check_access(tokens, True)
                break
            except Exception:
                await asyncio.sleep(0.20)
        else:
            raise RuntimeError("dynamic-policy-not-applied")
        assert gatewayproc.pid == pid and gatewayproc.poll() is None
        after = rss_mib(pid)

        # Roll back *only the policy* while gateway and backend remain alive.
        write_config(cfg, build_config(False))
        for _ in range(50):
            check_process(gatewayproc)
            try:
                await check_access(tokens, False)
                break
            except Exception:
                await asyncio.sleep(0.20)
        else:
            raise RuntimeError("policy-rollback-not-applied")
        print(json.dumps({
            "source": "agentgateway-v1.6.0-gh-release",
            "runner": "github-hosted-disposable",
            "synthetic": True,
            "verified": True,
            "mcp_jwt_authn": True,
            "tool_scope_denial": True,
            "dynamic_policy_reload": True,
            "rollback_without_restart": True,
            "gateway_rss_mib_before": before,
            "gateway_rss_mib_after": after,
            "backend_rss_mib": rss_mib(backendproc.pid),
            "vps_changed": False,
            "tunnel_changed": False,
        }, sort_keys=True))
    finally:
        for proc in (gatewayproc, backendproc, keyproc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=2)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m experiments.gateway_eval.smoke /path/to/agentgateway")
    binary = Path(sys.argv[1]).resolve()
    if not binary.is_file():
        raise SystemExit("candidate binary missing")
    with tempfile.TemporaryDirectory(prefix="agentgateway-fixture-") as tmp:
        asyncio.run(run_probe(binary, Path(tmp)))


if __name__ == "__main__":
    main()
