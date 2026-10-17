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


# Fixed diagnostic vocabulary. Neither arbitrary exception messages nor
# HTTP response contents are ever allowed into operation receipts.
SAFE_PHASES = frozenset({
    "anonymous-denial", "wrong-token-denial", "initialize", "notification",
    "tools-list", "dataset-info", "get-evidence", "revocation",
})
SAFE_ERRORS = frozenset({
    "invalid-endpoint", "client-dependency-unavailable", "https-request-failed",
    "response-too-large", "expected-json-response", "invalid-json-response",
    "mcp-response-failed", "mcp-rpc-failed", "mcp-tool-failed",
    "mcp-tool-data-failed", "missing-or-invalid-client-credential",
    "unauthorized-access-not-denied", "protocol-negotiation-failed",
    "initialization-notification-failed", "unexpected-tool-surface",
    "unexpected-atlas-dataset", "sourced-evidence-missing",
    "revoked-credential-not-denied",
})


class ProbeError(Exception):
    def __init__(self, code, *, phase="unknown", http_status=0):
        # A tool/provider exception must never become a log line or receipt.
        self.code = code if isinstance(code, str) and code in SAFE_ERRORS else "unverified"
        self.phase = phase if isinstance(phase, str) and phase in SAFE_PHASES else "unknown"
        self.http_status = (http_status if type(http_status) is int and 100 <= http_status <= 599
                            else 0)
        super().__init__(self.code)


def in_phase(phase, operation):
    """Tag a bounded local protocol step; no callbacks or source data in errors."""
    if phase not in SAFE_PHASES:
        raise ProbeError("unverified")
    try:
        return operation()
    except ProbeError as error:
        raise ProbeError(error.code, phase=phase, http_status=error.http_status) from None


def required_status(status, expected, reason):
    if status != expected:
        raise ProbeError(reason, http_status=status)


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
                try:
                    parsed = json.loads(payload)
                except (ValueError, UnicodeError):
                    raise ProbeError("invalid-json-response", http_status=status) from None
                return status, parsed
    except (httpx2.HTTPError, OSError, ValueError):
        raise ProbeError("https-request-failed") from None

def rpc(endpoint, token, identifier, method, params=None):
    message = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if params is not None:
        message["params"] = params
    status, body = post(endpoint, token, message)
    if status != 200 or not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or body.get("id") != identifier:
        raise ProbeError("mcp-response-failed", http_status=status)
    if "error" in body or not isinstance(body.get("result"), dict):
        raise ProbeError("mcp-rpc-failed", http_status=status)
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

    def assert_denial(candidate):
        status, _ = post(endpoint, candidate, probe)
        required_status(status, 401, "unauthorized-access-not-denied")

    in_phase("anonymous-denial", lambda: assert_denial(None))
    # This is an independent random token. The grant under test is never logged.
    in_phase("wrong-token-denial", lambda: assert_denial("alf_" + secrets.token_urlsafe(32)))

    init = in_phase("initialize", lambda: rpc(
        endpoint, token, 2, "initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "truthrail-minimal-client", "version": "0.1.0"},
        }))
    if init.get("protocolVersion") != PROTOCOL:
        raise ProbeError("protocol-negotiation-failed", phase="initialize", http_status=200)

    def assert_notification():
        status, _ = post(endpoint, token, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        required_status(status, 202, "initialization-notification-failed")
    in_phase("notification", assert_notification)

    listed = in_phase("tools-list", lambda: rpc(endpoint, token, 3, "tools/list").get("tools", []))
    if not isinstance(listed, list) or len(listed) != 5 or any(
            not isinstance(row, dict) for row in listed) or {row.get("name") for row in listed} != TOOLS:
        raise ProbeError("unexpected-tool-surface", phase="tools-list", http_status=200)

    info = in_phase("dataset-info", lambda: tool_data(rpc(
        endpoint, token, 4, "tools/call", {"name": "dataset_info", "arguments": {}})))
    if (info.get("dataset") != "canonical-rulers-atlas-v1"
            or type(info.get("canonical_persons")) is not int or info["canonical_persons"] < 1):
        raise ProbeError("unexpected-atlas-dataset", phase="dataset-info", http_status=200)

    evidence = in_phase("get-evidence", lambda: tool_data(rpc(
        endpoint, token, 5, "tools/call",
        {"name": "get_evidence", "arguments": {"record_id": "power-p03"}})))
    if (evidence.get("found") is not True or evidence.get("record_id") != "power-p03"
            or "PC03" not in evidence.get("source_ids", [])):
        raise ProbeError("sourced-evidence-missing", phase="get-evidence", http_status=200)

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
            in_phase("revocation", lambda: required_status(
                post(endpoint, token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[0],
                401, "revoked-credential-not-denied"))
            result = {"revoked_credential_denied": True, "model_called": False}
        else:
            result = smoke(endpoint, token)
        print(json.dumps(result, sort_keys=True))
        return 0
    except ProbeError as error:
        # Strictly local enum labels and a numeric HTTP code only.
        print(json.dumps({"verified": False, "reason": "client-smoke-failed",
                          "phase": error.phase, "code": error.code,
                          "http_status": error.http_status}, sort_keys=True))
        return 1
    except ValueError:
        print(json.dumps({"verified": False, "reason": "client-smoke-failed",
                          "phase": "unknown", "code": "unverified", "http_status": 0},
                         sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
