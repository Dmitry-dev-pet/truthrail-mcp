"""Read-only *live* Atlas-vs-shared MCP parity, never log backend payloads.

Run exclusively inside the reviewed, localhost-bound shadow adapter:
python -m shared_alfred_gateway.shadow_parity
No GitHub token, user-supplied origin, general URL proxy or background polling.
The adapter MUST remain detached from the existing ChatGPT tunnel while tested.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from shared_alfred_gateway.server import ATLAS_ORIGINS, ATLAS_TOOLS, MAX_ATLAS_RESPONSE

SHADOW_ORIGIN = "http://127.0.0.1:8766/mcp"
VECTORS: tuple[tuple[str, dict], ...] = (
    ("dataset_info", {}),
    ("search_rulers", {"text": "Сталин", "year": 1953, "limit": 3}),
    ("get_timeline", {"year": 1953, "limit": 3, "offset": 0}),
    ("compare_rulers", {"person_ids": ["person-e88112738c1054f5"]}),
    ("get_evidence", {"record_id": "power-p03"}),
)

def unwrap_tool_response(reply: Any) -> dict:
    if bool(getattr(reply, "is_error", False)):
        raise ValueError("backend_reported_error")
    payload = getattr(reply, "structured_content", None)
    if payload is None:
        content = getattr(reply, "content", None)
        texts = [
            x.text for x in (content or [])
            if getattr(x, "type", None) == "text" and isinstance(getattr(x, "text", None), str)
        ]
        if len(texts) != 1 or len(texts[0]) > MAX_ATLAS_RESPONSE:
            raise ValueError("unexpected_response_content")
        payload = json.loads(texts[0])
    if type(payload) is not dict:
        raise ValueError("unexpected_response_type")
    if len(json.dumps(payload, sort_keys=True, ensure_ascii=False)) > MAX_ATLAS_RESPONSE:
        raise ValueError("oversized_response")
    return payload

def normalized_schema(tool: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    data = tool.model_dump(by_alias=True)
    schema = data.get("inputSchema", data.get("input_schema"))
    if not isinstance(schema, dict):
        raise ValueError("invalid_input_schema")
    fields = schema.get("properties")
    required = schema.get("required", [])
    if not isinstance(fields, dict) or not isinstance(required, list):
        raise ValueError("invalid_input_schema")
    return tuple(sorted(fields)), tuple(sorted(required))

async def verify(upstream: str) -> tuple[bool, str]:
    from mcp import Client

    if upstream not in ATLAS_ORIGINS:
        return False, "blocked_atlas_origin"
    try:
        # Both clients run in the private Atlas network. No Internet requests.
        async with Client(upstream, raise_exceptions=True) as atlas, \
                   Client(SHADOW_ORIGIN, raise_exceptions=True) as shadow:
            original_list = await atlas.list_tools()
            proxy_list = await shadow.list_tools()
            original = {tool.name: tool for tool in original_list.tools}
            proxy = {tool.name: tool for tool in proxy_list.tools}
            if set(original) != set(ATLAS_TOOLS) or set(proxy) != set(ATLAS_TOOLS):
                return False, "tool_inventory_mismatch"
            for name in ATLAS_TOOLS:
                if normalized_schema(original[name]) != normalized_schema(proxy[name]):
                    return False, "tool_schema_mismatch_" + name
            for name, args in VECTORS:
                real = unwrap_tool_response(await atlas.call_tool(name, args))
                proxied = unwrap_tool_response(await shadow.call_tool(name, args))
                if real != proxied:
                    return False, "response_parity_mismatch_" + name
                if name == "get_evidence" and (
                    real.get("found") is not True or
                    "PC03" not in real.get("source_ids", []) or
                    not isinstance(real.get("sources"), list)
                ):
                    return False, "evidence_provenance_unverified"
            return True, "five_real_atlas_calls_equal"
    except Exception:
        # NEVER print URL, exception string, stack, source text or response.
        return False, "private_mcp_transport_unavailable"

def main() -> int:
    if os.environ.get("ALFRED_SHARED_MCP_ENABLED") != "1":
        print("shared_mcp_shadow_parity: blocked_disabled")
        return 2
    if os.environ.get("ALFRED_CAPTION_MCP_ENABLED") == "1":
        print("shared_mcp_shadow_parity: blocked_caption_enabled")
        return 2
    ok, status = asyncio.run(verify(os.environ.get("ALFRED_ATLAS_URL", "http://app:8765/mcp")))
    print("shared_mcp_shadow_parity: " + status)
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
