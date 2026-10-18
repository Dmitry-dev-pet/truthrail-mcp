"""Synthetic isolated upstream for gateway research; no real Atlas or secrets."""
from mcp.server import MCPServer

server = MCPServer("Alfred Gateway Synthetic Backend")

@server.tool()
async def dataset_info() -> dict:
    """Synthetic scope-protected Atlas read; never hits a real Atlas dataset."""
    return {"synthetic": True, "source": "fixture", "dataset": "gateway-test"}

@server.tool()
async def peer_echo(message: str) -> dict:
    """Synthetic write-shaped tool with no external side effects."""
    if len(message.encode("utf-8")) > 128:
        return {"available": False, "reason": "too-large"}
    return {"synthetic": True, "echo": message}

if __name__ == "__main__":
    server.run(
        transport="streamable-http", host="127.0.0.1", port=18765,
        stateless_http=True, json_response=True, max_request_body_size=4096,
    )
