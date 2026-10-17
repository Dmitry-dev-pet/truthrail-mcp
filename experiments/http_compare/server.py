"""Synthetic five-tool *persistent localhost HTTP* service; no external backend.

For CI only. Exactly one of the two SDKs, fixed loopback-only port and
stateless Streamable HTTP with JSON responses. No authentication claims.
"""
from __future__ import annotations

import sys

from experiments.sdk_comparison.benchmark import expected

PORTS = {"mcp2": 18765, "fastmcp4": 18766}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in PORTS:
        raise SystemExit("invalid_synthetic_http_mode")
    mode = sys.argv[1]
    if mode == "mcp2":
        from mcp.server import MCPServer
        mcp = MCPServer("SDK2 localhost HTTP synthetic Atlas")
        tool = mcp.tool()
    else:
        from fastmcp import FastMCP
        mcp = FastMCP("FastMCP4 localhost HTTP synthetic Atlas")
        tool = mcp.tool

    @tool
    async def dataset_info() -> dict:
        return expected("dataset_info", {})

    @tool
    async def search_rulers(text: str = "", year: int | None = None,
                            limit: int = 20) -> dict:
        return expected("search_rulers", {"text": text, "year": year, "limit": limit})

    @tool
    async def get_timeline(year: int, state_id: str = "",
                           limit: int = 40, offset: int = 0) -> dict:
        return expected("get_timeline", {
            "year": year, "state_id": state_id, "limit": limit, "offset": offset,
        })

    @tool
    async def compare_rulers(person_ids: list[str]) -> dict:
        return expected("compare_rulers", {"person_ids": person_ids})

    @tool
    async def get_evidence(record_id: str) -> dict:
        return expected("get_evidence", {"record_id": record_id})

    if mode == "mcp2":
        mcp.run(
            transport="streamable-http", host="127.0.0.1", port=PORTS[mode],
            stateless_http=True, json_response=True,
            max_request_body_size=16384,
        )
    else:
        mcp.run(
            transport="http", host="127.0.0.1", port=PORTS[mode],
            stateless_http=True, json_response=True,
        )


if __name__ == "__main__":
    main()
