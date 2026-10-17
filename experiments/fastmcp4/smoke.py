"""Synthetic, source-reviewed FastMCP 4 smoke for an isolated Mac runner.

This program uses only in-process synthetic MCP servers: NO Atlas VPS calls,
credentials, listener, SSH, Docker, Cloudflare, AGY, model inference or Mac files.
The single-line JSON receipt contains measurements and boolean checks only.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import platform
import resource
import statistics
import time

from fastmcp import Client, FastMCP
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import ProxyClient

NAMES = (
    "dataset_info", "search_rulers", "get_timeline",
    "compare_rulers", "get_evidence",
)
PERSON_ID = "person-e88112738c1054f5"
CASE_ARGS = {
    "dataset_info": {},
    "search_rulers": {"text": "Сталин", "year": 1953, "limit": 3},
    "get_timeline": {"year": 1953, "limit": 3, "offset": 0},
    "compare_rulers": {"person_ids": [PERSON_ID]},
    "get_evidence": {"record_id": "power-p03"},
}
REQUIRED_ARGS = {
    "dataset_info": set(),
    "search_rulers": set(),
    "get_timeline": {"year"},
    "compare_rulers": {"person_ids"},
    "get_evidence": {"record_id"},
}
SOURCE_URL = "https://www.archontology.org/nations/ussr/cpsu/secretariat.php"


def make_backend() -> FastMCP:
    atlas = FastMCP("Synthetic Atlas: no real data")

    @atlas.tool
    async def dataset_info() -> dict:
        return {"dataset": "synthetic-only", "canonical_persons": 83}

    @atlas.tool
    async def search_rulers(text: str = "", year: int | None = None, limit: int = 20) -> dict:
        return {"query": text, "year": year, "limit": limit, "results": [
            {"person_id": PERSON_ID, "name": "Иосиф Сталин"},
        ]}

    @atlas.tool
    async def get_timeline(year: int, state_id: str = "", limit: int = 40, offset: int = 0) -> dict:
        return {"year": year, "state_id": state_id, "limit": limit, "offset": offset,
                "entries": [{"entry_id": "power-p03", "source_ids": ["PC03"]}]}

    @atlas.tool
    async def compare_rulers(person_ids: list[str]) -> dict:
        return {"persons": [
            {"person_id": ident, "roles_and_assessments": [{"source_ids": ["PC03"]}]}
            for ident in person_ids
        ]}

    @atlas.tool
    async def get_evidence(record_id: str) -> dict:
        return {"found": True, "record_id": record_id, "source_ids": ["PC03"],
                "sources": [{"id": "PC03", "url": SOURCE_URL}],
                "verification": "imported_not_rechecked"}

    return atlas


def shape(tool: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Compare parameter and required-name sets across direct and mounted tools."""
    data = tool.model_dump(by_alias=True)
    schema = data.get("inputSchema") or data.get("input_schema")
    if not isinstance(schema, dict):
        raise AssertionError("tool_input_schema_unavailable")
    return (tuple(sorted(schema.get("properties", {}))),
            tuple(sorted(schema.get("required", []))))


def raw_result(result: object) -> str:
    """Compare normalized result objects without exposing their contents."""
    if getattr(result, "is_error", False):
        raise AssertionError("tool_returned_error")
    data = getattr(result, "data", None)
    if data is None:
        data = getattr(result, "structured_content", None)
    if data is None:
        content = getattr(result, "content", ())
        text_blocks = [x.text for x in content if getattr(x, "type", "") == "text"]
        if len(text_blocks) != 1:
            raise AssertionError("tool_content_unavailable")
        data = json.loads(text_blocks[0])
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def max_rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Darwin returns bytes; Linux returns KiB.
    denom = 1024 * 1024 if platform.system() == "Darwin" else 1024
    return round(value / denom, 1)


async def verify() -> dict:
    started = time.perf_counter()
    upstream = make_backend()
    # The critical trust boundary: NEVER forward client Authorization headers.
    proxy_client = ProxyClient(upstream, forward_incoming_headers=False,
                               sampling_handler=None, log_handler=None)
    proxy = create_proxy(proxy_client, name="Atlas Proxy")
    gateway = FastMCP("Alfred Shared Synthetic Probe")
    gateway.mount(proxy)  # No namespace: preserve exactly the old Atlas tool names.

    times = []
    async with Client(upstream) as direct, Client(gateway) as routed:
        original = {t.name: t for t in await direct.list_tools()}
        exposed = {t.name: t for t in await routed.list_tools()}
        if set(original) != set(NAMES) or set(exposed) != set(NAMES):
            raise AssertionError("tool_set_or_names_changed")
        for name in NAMES:
            before, after = shape(original[name]), shape(exposed[name])
            if before != after or set(before[1]) != REQUIRED_ARGS[name]:
                raise AssertionError("tool_schema_drift_" + name)
        for name, args in CASE_ARGS.items():
            t0 = time.perf_counter()
            a = raw_result(await direct.call_tool(name, args))
            b = raw_result(await routed.call_tool(name, args))
            times.append(round((time.perf_counter() - t0) * 1000, 2))
            if a != b:
                raise AssertionError("tool_result_drift_" + name)
            if name == "get_evidence" and ("PC03" not in b or SOURCE_URL not in b):
                raise AssertionError("evidence_provenance_missing")
        try:
            await routed.call_tool("arbitrary_shell", {})
        except Exception:
            pass  # Unknown tool must fail closed.
        else:
            raise AssertionError("unknown_tool_accepted")

    elapsed = time.perf_counter() - started
    return {
        "kind": "alfred_fastmcp4_synthetic_mac_v1",
        "verified": True,
        "verification": {"status": "verified"},
        "synthetic": True,
        "real_atlas_tested": False,
        "openai_tunnel_changed": False,
        "client_auth_forwarding": False,
        "fastmcp": importlib.metadata.version("fastmcp"),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "five_tool_names_and_schemas": True,
        "five_response_parity": True,
        "pc03_provenance_preserved": True,
        "unknown_tool_denied": True,
        "max_rss_mib": max_rss_mib(),
        "elapsed_s": round(elapsed, 2),
        "call_pair_latency_ms_median": round(statistics.median(times), 2),
    }


def main() -> int:
    try:
        summary = asyncio.run(asyncio.wait_for(verify(), timeout=90))
        if summary["elapsed_s"] > 90 or summary["max_rss_mib"] > 2048:
            raise AssertionError("resource_ceiling_exceeded")
    except Exception as e:
        # Only known fixed codes; never emit exception messages/stack traces.
        label = str(e) if isinstance(e, AssertionError) else "internal_test_failure"
        if not label.startswith((
            "tool_", "evidence_", "unknown_", "resource_", "internal_", "tool_set_"
        )):
            label = "internal_test_failure"
        print(json.dumps({"kind": "alfred_fastmcp4_synthetic_mac_v1",
                          "verified": False, "reason": label}, separators=(",", ":")))
        return 2
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
