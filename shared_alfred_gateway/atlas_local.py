"""Read-only single-process Atlas provider for the generic Truthrail MCP.

The Atlas project retains its own canonical SQLite query layer. This module
injects its five public read-only operations into the shared MCP dispatcher,
with no second Atlas HTTP client, new tunnel, extra process or private data.
Runtime activation requires a separately reviewed combined Docker image.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from shared_alfred_gateway.server import ATLAS_TOOLS, make_server

Backend = Callable[[str, dict], Awaitable[dict]]


def local_query_backend(query: Any) -> Backend:
    """Call only the five fixed AtlasQuery methods on the provided query object."""
    async def call(name: str, args: dict) -> dict:
        if name not in ATLAS_TOOLS or type(args) is not dict:
            return {"available": False, "reason": "unknown-atlas-tool"}
        if name == "dataset_info":
            result = query.dataset_info()
            if type(result) is not dict:
                return {"available": False, "reason": "backend-response-invalid"}
            return {
                **result,
                "archigos_file_configured": False,
                "archigos_data_loaded": False,
            }
        if name == "search_rulers":
            result = query.search_rulers(**args)
        elif name == "get_timeline":
            result = query.get_timeline(**args)
        elif name == "compare_rulers":
            result = query.compare_rulers(args["person_ids"])
        elif name == "get_evidence":
            result = query.get_evidence(args["record_id"])
        else:
            return {"available": False, "reason": "unknown-atlas-tool"}
        return result if type(result) is dict else {
            "available": False, "reason": "backend-response-invalid",
        }

    return call


def make_embedded_server(query: Any, *, peer_chat_enabled: bool = False):
    """One MCPServer: five Atlas tools, optional ephemeral peer chat, no Archigos."""
    return make_server(
        _atlas_backend=local_query_backend(query),
        captions_enabled=False,
        caption_url=None,
        peer_chat_enabled=peer_chat_enabled,
    )


def main() -> None:
    if (os.environ.get("ALFRED_SHARED_MCP_ENABLED") != "1"
            or os.environ.get("ALFRED_EMBEDDED_ATLAS_REVIEWED") != "1"):
        raise SystemExit("embedded-atlas-not-authorized")
    host = os.environ.get("ATLAS_MCP_BIND", "127.0.0.1")
    if host not in {"127.0.0.1", "0.0.0.0"}:
        raise SystemExit("unsafe-bind-host")
    port = int(os.environ.get("ATLAS_MCP_PORT", "8765"))
    if not 1024 <= port <= 65535:
        raise SystemExit("invalid-shared-port")
    db_path = os.environ.get("ATLAS_SQLITE_PATH")
    if not db_path:
        raise SystemExit("atlas-sqlite-path-missing")
    # The combined private image owns the exact AtlasQuery module and SQLite.
    from scripts.atlas_mcp_query import AtlasQuery
    make_embedded_server(
        AtlasQuery(Path(db_path)),
        peer_chat_enabled=os.environ.get("ALFRED_PEER_CHAT_MCP_ENABLED") == "1",
    ).run(
        transport="streamable-http", host=host, port=port,
        stateless_http=True, json_response=True, max_request_body_size=16384,
    )


if __name__ == "__main__":
    main()
