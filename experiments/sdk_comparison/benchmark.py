"""Matched in-process MCP SDK comparison, with synthetic Historical Atlas data only.

Run in SEPARATE fresh virtual environments using the SAME Python interpreter:
    python benchmark.py mcp2
    python benchmark.py fastmcp4

No real Atlas data, network listeners, URL requests, credentials, files,
Docker operations or model inference. Compare two *in-process five-tool server*
interfaces, NOT their production HTTP transport or the earlier FastMCP proxy.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import platform
import resource
import statistics
import sys
import time

NAMES = (
    "dataset_info", "search_rulers", "get_timeline",
    "compare_rulers", "get_evidence",
)
CASE_ARGS = {
    "dataset_info": {},
    "search_rulers": {"text": "Сталин", "year": 1953, "limit": 3},
    "get_timeline": {"year": 1953, "limit": 3, "offset": 0},
    "compare_rulers": {"person_ids": ["person-e88112738c1054f5"]},
    "get_evidence": {"record_id": "power-p03"},
}
REQUIRED = {
    "dataset_info": set(),
    "search_rulers": set(),
    "get_timeline": {"year"},
    "compare_rulers": {"person_ids"},
    "get_evidence": {"record_id"},
}
SOURCE_URL = "https://www.archontology.org/nations/ussr/cpsu/secretariat.php"
ITERATIONS = 10
MAX_RUNTIME_S = 90


def expected(name: str, args: dict) -> dict:
    """Stable same output fixture for both SDK implementations."""
    if name == "dataset_info":
        return {"dataset": "synthetic-only", "canonical_persons": 83}
    if name == "search_rulers":
        return {
            "query": args.get("text", ""), "year": args.get("year"),
            "limit": args.get("limit", 20),
            "results": [{"person_id": "person-e88112738c1054f5",
                         "name": "Иосиф Сталин"}],
        }
    if name == "get_timeline":
        return {
            "year": args["year"], "state_id": args.get("state_id", ""),
            "limit": args.get("limit", 40), "offset": args.get("offset", 0),
            "entries": [{"entry_id": "power-p03", "source_ids": ["PC03"]}],
        }
    if name == "compare_rulers":
        return {
            "persons": [
                {"person_id": ident,
                 "roles_and_assessments": [{"source_ids": ["PC03"]}]}
                for ident in args["person_ids"]
            ],
        }
    if name == "get_evidence":
        return {
            "found": True, "record_id": args["record_id"],
            "source_ids": ["PC03"],
            "sources": [{"id": "PC03", "url": SOURCE_URL}],
            "verification": "imported_not_rechecked",
        }
    raise AssertionError("unknown_tool_fixture")


def normalize(value: object) -> str:
    """JSON-stable response parity without dumping source contents into receipts."""
    if getattr(value, "is_error", False):
        raise AssertionError("unexpected_tool_error")
    output = getattr(value, "data", None)
    if output is None:
        output = getattr(value, "structured_content", None)
    if output is None:
        texts = [
            part.text for part in getattr(value, "content", ())
            if getattr(part, "type", None) == "text"
        ]
        if len(texts) != 1:
            raise AssertionError("tool_content_unavailable")
        output = json.loads(texts[0])
    if not isinstance(output, dict):
        raise AssertionError("tool_result_not_dict")
    return json.dumps(output, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def schema(tool: object) -> tuple[set[str], set[str]]:
    model = tool.model_dump(by_alias=True)
    obj = model.get("inputSchema") or model.get("input_schema")
    if not isinstance(obj, dict):
        raise AssertionError("schema_unavailable")
    return set(obj.get("properties", {})), set(obj.get("required", []))


def peak_rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if platform.system() == "Darwin" else 1024
    return round(value / divisor, 1)


async def run(mode: str) -> dict:
    if mode == "mcp2":
        from mcp import Client
        from mcp.server import MCPServer
        server = MCPServer("SDK2 Matched Synthetic Atlas")
        register = server.tool()
        version = importlib.metadata.version("mcp")
    elif mode == "fastmcp4":
        from fastmcp import Client, FastMCP
        server = FastMCP("FastMCP4 Matched Synthetic Atlas")
        register = server.tool
        version = importlib.metadata.version("fastmcp")
    else:
        raise AssertionError("unrecognized_sdk")

    @register
    async def dataset_info() -> dict:
        return expected("dataset_info", {})

    @register
    async def search_rulers(text: str = "", year: int | None = None,
                            limit: int = 20) -> dict:
        return expected("search_rulers", {"text": text, "year": year, "limit": limit})

    @register
    async def get_timeline(year: int, state_id: str = "",
                           limit: int = 40, offset: int = 0) -> dict:
        return expected("get_timeline", {
            "year": year, "state_id": state_id, "limit": limit, "offset": offset,
        })

    @register
    async def compare_rulers(person_ids: list[str]) -> dict:
        return expected("compare_rulers", {"person_ids": person_ids})

    @register
    async def get_evidence(record_id: str) -> dict:
        return expected("get_evidence", {"record_id": record_id})

    started = time.perf_counter()
    client = Client(server, raise_exceptions=True) if mode == "mcp2" else Client(server)
    durations = []
    async with client:
        tools_result = await client.list_tools()
        listed = tools_result.tools if mode == "mcp2" else tools_result
        registered = {tool.name: tool for tool in listed}
        if set(registered) != set(NAMES):
            raise AssertionError("tool_names_changed")
        for name in NAMES:
            actual_props, actual_required = schema(registered[name])
            if not REQUIRED[name].issubset(actual_props) or actual_required != REQUIRED[name]:
                raise AssertionError("tool_schema_changed")
        # Same five requests and same fixture for a warmup and 10 measured cycles.
        for iteration in range(ITERATIONS + 1):
            for name, args in CASE_ARGS.items():
                before = time.perf_counter()
                data = normalize(await client.call_tool(name, args))
                if data != json.dumps(
                    expected(name, args), sort_keys=True, ensure_ascii=False,
                    separators=(",", ":"),
                ):
                    raise AssertionError("fixture_result_mismatch")
                if iteration > 0:
                    durations.append((time.perf_counter() - before) * 1000)
        try:
            unknown = await client.call_tool("arbitrary_shell", {})
        except Exception:
            unknown_denied = True
        else:
            unknown_denied = bool(getattr(unknown, "is_error", False))
        if not unknown_denied:
            raise AssertionError("unknown_tool_accepted")

    elapsed = time.perf_counter() - started
    if elapsed > MAX_RUNTIME_S:
        raise AssertionError("runtime_ceiling_exceeded")
    return {
        "kind": "truthrail_mcp_sdk_matched_synthetic_v1",
        "verified": True,
        "synthetic": True,
        "real_atlas_tested": False,
        "transport": "in_process",
        "sdk": mode,
        "sdk_version": version,
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "five_tool_names_schemas_results": True,
        "unknown_tool_denied": True,
        "calls_measured": len(durations),
        "peak_rss_mib": peak_rss_mib(),
        "median_call_ms": round(statistics.median(durations), 3),
        "total_elapsed_s": round(elapsed, 3),
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode not in {"mcp2", "fastmcp4"}:
        print('{"verified":false,"reason":"invalid_mode"}')
        return 2
    try:
        result = asyncio.run(asyncio.wait_for(run(mode), timeout=MAX_RUNTIME_S))
    except Exception:
        # Do not leak exception strings, paths, environment values or tool output.
        print(json.dumps({"kind": "truthrail_mcp_sdk_matched_synthetic_v1",
                          "sdk": mode, "verified": False,
                          "reason": "synthetic_benchmark_failed"},
                         separators=(",", ":")))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
