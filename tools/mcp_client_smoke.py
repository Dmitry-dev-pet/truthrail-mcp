#!/usr/bin/env python3
"""Minimal external HTTPS MCP client. Credential values stay in its process.

Run only against an owner-approved endpoint. This does not provision an ingress.
No redirects, retries, model calls, or source text in diagnostic output.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
from urllib.parse import urlsplit

TOOLS = {"dataset_info", "search_rulers", "get_timeline", "compare_rulers", "get_evidence"}
MAX_RESPONSE = 196608
PROTOCOL = "2025-11-25"


class ProbeError(Exception):
    pass


def endpoint_url(value):
    parts = urlsplit(value)
    if (len(value) > 256 or "?" in value or "#" in value or parts.scheme != "https"
            or not parts.hostname or parts.username or parts.password or parts.port
            or parts.path != "/mcp"):
        raise ProbeError("invalid-endpoint")
    return value


def post(endpoint, token, message):
    """One bounded HTTPS MCP POST via standard Python MCP v2's HTTP transport.

    No redirects, retries, environment proxy, spoofed User-Agent, or response leak.
    Lazy import keeps the trusted source-integrity check dependency-free.
    """
    endpoint_url(endpoint)
    try:
        import httpx2
    except ImportError:
        raise ProbeError("client-dependency-unavailable") from None

    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": PROTOCOL}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with httpx2.Client(timeout=15.0, follow_redirects=False, trust_env=False) as transport:
            with transport.stream("POST", endpoint, headers=headers,
                                  content=json.dumps(message, separators=(",", ":")).encode()) as response:
                status = response.status_code
                if status != 200:
                    # Includes 202 notifications, 401 denials, redirects and all errors.
                    return status, None
                payload = bytearray()
                for chunk in response.iter_bytes():
                    if len(payload) + len(chunk) > MAX_RESPONSE:
                        raise ProbeError("response-too-large")
                    payload.extend(chunk)
                if not payload:
                    return status, None
                if "application/json" not in response.headers.get("Content-Type", "").lower():
                    raise ProbeError("expected-json-response")
                return status, json.loads(payload)
    except (httpx2.HTTPError, OSError, ValueError):
        raise ProbeError("https-request-failed") from None

def rpc(endpoint, token, identifier, method, params=None):
    message = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if params is not None:
        message["params"] = params
    status, body = post(endpoint, token, message)
    if status != 200 or not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or body.get("id") != identifier:
        raise ProbeError("mcp-response-failed")
    if "error" in body or not isinstance(body.get("result"), dict):
        raise ProbeError("mcp-rpc-failed")
    return body["result"]


def tool_data(result):
    if result.get("isError"):
        raise ProbeError("mcp-tool-failed")
    data = result.get("structuredContent")
    if isinstance(data, dict):
        return data
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1 or content[0].get("type") != "text":
        raise ProbeError("mcp-tool-data-failed")
    try:
        data = json.loads(content[0]["text"])
    except (KeyError, TypeError, ValueError):
        raise ProbeError("mcp-tool-data-failed") from None
    if not isinstance(data, dict):
        raise ProbeError("mcp-tool-data-failed")
    return data


def smoke(endpoint, token):
    endpoint_url(endpoint)
    if not re.fullmatch(r"alf_[A-Za-z0-9_-]{43,86}", token):
        raise ProbeError("missing-or-invalid-client-credential")
    probe = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    for candidate in (None, "alf_" + secrets.token_urlsafe(32)):
        if post(endpoint, candidate, probe)[0] != 401:
            raise ProbeError("unauthorized-access-not-denied")
    init = rpc(endpoint, token, 2, "initialize", {"protocolVersion": PROTOCOL,
               "capabilities": {}, "clientInfo": {"name": "truthrail-minimal-client", "version": "0.1.0"}})
    if init.get("protocolVersion") != PROTOCOL:
        raise ProbeError("protocol-negotiation-failed")
    if post(endpoint, token, {"jsonrpc": "2.0", "method": "notifications/initialized"})[0] != 202:
        raise ProbeError("initialization-notification-failed")
    listed = rpc(endpoint, token, 3, "tools/list").get("tools", [])
    if len(listed) != 5 or {row.get("name") for row in listed} != TOOLS:
        raise ProbeError("unexpected-tool-surface")
    info = tool_data(rpc(endpoint, token, 4, "tools/call", {"name": "dataset_info", "arguments": {}}))
    if info.get("dataset") != "canonical-rulers-atlas-v1" or type(info.get("canonical_persons")) is not int or info["canonical_persons"] < 1:
        raise ProbeError("unexpected-atlas-dataset")
    evidence = tool_data(rpc(endpoint, token, 5, "tools/call", {"name": "get_evidence", "arguments": {"record_id": "power-p03"}}))
    if evidence.get("found") is not True or evidence.get("record_id") != "power-p03" or "PC03" not in evidence.get("source_ids", []):
        raise ProbeError("sourced-evidence-missing")
    return {"transport": "external_https", "anonymous_denied": True, "wrong_token_denied": True,
            "tools": 5, "canonical_persons": info["canonical_persons"], "sourced_evidence": True,
            "revocation_verified": False, "model_called": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revoked-only", action="store_true")
    args = parser.parse_args()
    try:
        endpoint = endpoint_url(os.environ.get("MCP_ENDPOINT", ""))
        token = os.environ.get("MCP_CLIENT_TOKEN", "")
        if args.revoked_only:
            if not re.fullmatch(r"alf_[A-Za-z0-9_-]{43,86}", token):
                raise ProbeError("missing-or-invalid-client-credential")
            if post(endpoint, token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[0] != 401:
                raise ProbeError("revoked-credential-not-denied")
            result = {"revoked_credential_denied": True, "model_called": False}
        else:
            result = smoke(endpoint, token)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ProbeError, ValueError):
        print(json.dumps({"verified": False, "reason": "client-smoke-failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
