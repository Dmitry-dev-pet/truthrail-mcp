"""Proposed single private Alfred MCP endpoint (source-only; NOT deployed).

The existing Atlas HTTPS tunnel client remains pointed at the working Atlas
service until an independently approved migration. This optional server
is NOT the existing infra GitHub/OAuth gateway. Atlas tools stay read-only.
A separately enabled peer-chat experiment stores bounded inert text in memory.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

ATLAS_TOOLS = (
    "dataset_info", "search_rulers", "get_timeline",
    "compare_rulers", "get_evidence",
)
CAPTION_TOOL = "youtube_transcript"
PEER_CHAT_TOOLS = ("chat_create", "chat_send", "chat_wait", "chat_close")
ATLAS_ORIGINS = frozenset({
    "http://app:8765/mcp",             # existing private Atlas Docker network
    "http://127.0.0.1:8020/mcp",      # existing Atlas VPS loopback
})
# This Docker-network name represents an ADMISSION-GATED private Mac backhaul,
# not a deployed service or publicly exposed port.
CAPTION_ORIGINS = frozenset({"http://caption-backhaul:8619/mcp"})
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}\Z")
DEFAULT_CAPTION_PAGE = 12000
MAX_CAPTION_PAGE = 16000
MAX_ATLAS_RESPONSE = 131072
MAX_CAPTION_RESPONSE = 24576

Backend = Callable[[str, dict], Awaitable[dict]]


def video_id_for(value: object) -> str:
    """Parse a bare ID or recognized HTTPS YouTube URL before any backend call."""
    if type(value) is not str or not 1 <= len(value) <= 512:
        raise ValueError("invalid-video-url")
    if VIDEO_ID.fullmatch(value):
        return value
    try:
        v = urlsplit(value)
        host, port = (v.hostname or "").lower(), v.port
    except ValueError as exc:
        raise ValueError("invalid-video-url") from exc
    if (v.scheme != "https" or port is not None or v.username or v.password
            or host not in {
                "youtube.com", "www.youtube.com", "m.youtube.com",
                "youtu.be", "www.youtu.be",
                "youtube-nocookie.com", "www.youtube-nocookie.com",
            }):
        raise ValueError("invalid-video-url")
    if host in ("youtu.be", "www.youtu.be"):
        candidate = v.path[1:] if v.path.startswith("/") else ""
    elif host in ("youtube.com", "www.youtube.com", "m.youtube.com") and v.path == "/watch":
        ids = parse_qs(v.query, keep_blank_values=True).get("v", [])
        candidate = ids[0] if len(ids) == 1 else ""
    else:
        parts = v.path.strip("/").split("/")
        candidate = parts[1] if len(parts) == 2 and parts[0] in {"shorts", "live", "embed"} else ""
    if not VIDEO_ID.fullmatch(candidate):
        raise ValueError("invalid-video-url")
    return candidate


async def _call_mcp_backend(target: str, operation: str, arguments: dict, *, cap: int) -> dict:
    """New isolated session for a private, stateless HTTP MCP backend."""
    import anyio
    from mcp import Client

    try:
        with anyio.fail_after(30):
            async with Client(target, raise_exceptions=True) as client:
                result = await client.call_tool(operation, arguments)
        if result.is_error:
            return {"available": False, "reason": "backend-call-error"}
        structured = getattr(result, "structured_content", None)
        if structured is None:
            texts = [
                x.text for x in result.content
                if getattr(x, "type", None) == "text" and isinstance(getattr(x, "text", None), str)
            ]
            if len(texts) != 1 or len(texts[0]) > cap:
                return {"available": False, "reason": "backend-response-invalid"}
            structured = json.loads(texts[0])
        if type(structured) is not dict:
            return {"available": False, "reason": "backend-response-invalid"}
        if len(json.dumps(structured, ensure_ascii=False)) > cap:
            return {"available": False, "reason": "backend-response-too-large"}
        return structured
    except Exception:
        # Error bodies may contain remote paths, URLs, headers or credentials.
        return {"available": False, "reason": "backend-unavailable"}


def configure_backends(
    *,
    atlas_url: str,
    caption_url: str | None,
    caption_enabled: bool,
    _atlas_backend: Backend | None = None,
    _caption_backend: Backend | None = None,
) -> tuple[Backend, Backend | None]:
    if atlas_url not in ATLAS_ORIGINS:
        raise ValueError("atlas-origin-not-allowlisted")
    if caption_enabled:
        if caption_url not in CAPTION_ORIGINS:
            raise ValueError("caption-private-backhaul-not-allowlisted")
        if os.environ.get("ALFRED_MAC_BACKHAUL_REVIEWED") != "1":
            raise ValueError("caption-private-backhaul-not-reviewed")
    elif caption_url:
        raise ValueError("caption-origin-without-authorization")

    async def atlas_call(operation: str, args: dict) -> dict:
        return await _call_mcp_backend(atlas_url, operation, args, cap=MAX_ATLAS_RESPONSE)

    async def caption_call(operation: str, args: dict) -> dict:
        assert caption_url
        return await _call_mcp_backend(caption_url, operation, args, cap=MAX_CAPTION_RESPONSE)

    return _atlas_backend or atlas_call, (_caption_backend or caption_call) if caption_enabled else None


def make_server(
    *,
    atlas_url: str = "http://app:8765/mcp",
    caption_url: str | None = None,
    captions_enabled: bool = False,
    _atlas_backend: Backend | None = None,
    _caption_backend: Backend | None = None,
    peer_chat_enabled: bool = False,
):
    from mcp.server import MCPServer

    atlas, caption = configure_backends(
        atlas_url=atlas_url, caption_url=caption_url,
        caption_enabled=captions_enabled,
        _atlas_backend=_atlas_backend, _caption_backend=_caption_backend,
    )
    server = MCPServer("Alfred Shared Read-Only MCP")

    async def atlas_forward(name: str, args: dict) -> dict:
        if name not in ATLAS_TOOLS:
            return {"available": False, "reason": "unknown-atlas-tool"}
        try:
            value = await atlas(name, args)
            return value if type(value) is dict else {"available": False, "reason": "backend-response-invalid"}
        except Exception:
            return {"available": False, "reason": "backend-unavailable"}

    @server.tool()
    async def dataset_info() -> dict:
        """Show Historical Atlas canonical dataset scope and source limitations."""
        return await atlas_forward("dataset_info", {})

    @server.tool()
    async def search_rulers(text: str = "", year: int | None = None, limit: int = 20) -> dict:
        """Find canonical rulers by name and optional year from the existing Atlas."""
        return await atlas_forward("search_rulers", {"text": text, "year": year, "limit": limit})

    @server.tool()
    async def get_timeline(year: int, state_id: str = "", limit: int = 40, offset: int = 0) -> dict:
        """Read legal tenures and distinct historical power assessments."""
        return await atlas_forward("get_timeline", {
            "year": year, "state_id": state_id, "limit": limit, "offset": offset,
        })

    @server.tool()
    async def compare_rulers(person_ids: list[str]) -> dict:
        """Compare at most five canonical Historical Atlas identities."""
        if type(person_ids) is not list or not 1 <= len(person_ids) <= 5 or any(
            type(p) is not str or len(p) > 90 for p in person_ids
        ):
            return {"available": False, "reason": "invalid-atlas-person-ids"}
        return await atlas_forward("compare_rulers", {"person_ids": person_ids})

    @server.tool()
    async def get_evidence(record_id: str) -> dict:
        """Look up sourced Historical Atlas evidence by exact record identifier."""
        if type(record_id) is not str or len(record_id) > 100:
            return {"available": False, "reason": "invalid-atlas-record-id"}
        return await atlas_forward("get_evidence", {"record_id": record_id})

    if caption is not None:
        @server.tool()
        async def youtube_transcript(
            video_url: str,
            language: str = "ru",
            cursor: int = 0,
            max_chars: int = DEFAULT_CAPTION_PAGE,
        ) -> dict:
            """Read one explicitly requested public YouTube video's captions."""
            try:
                ident = video_id_for(video_url)
            except ValueError:
                return {"available": False, "reason": "invalid-video-url"}
            if type(language) is not str or not LANGUAGE.fullmatch(language):
                return {"available": False, "reason": "invalid-caption-language"}
            if type(cursor) is not int or not 0 <= cursor <= 10000:
                return {"available": False, "reason": "invalid-caption-cursor"}
            if type(max_chars) is not int or not 128 <= max_chars <= MAX_CAPTION_PAGE:
                return {"available": False, "reason": "invalid-caption-page-limit"}
            try:
                response = await caption("youtube_transcript", {
                    "video_url": f"https://www.youtube.com/watch?v={ident}",
                    "language": language, "cursor": cursor, "max_chars": max_chars,
                })
            except Exception:
                return {"available": False, "reason": "backend-unavailable"}
            if type(response) is not dict or response.get("available") is not True:
                return {"available": False, "reason": "caption-backend-unavailable"}
            if (response.get("video_id") != ident or response.get("language") != language
                    or response.get("cursor") != cursor
                    or type(response.get("text")) is not str
                    or len(response["text"]) > max_chars
                    or response.get("untrusted_source_text") is not True):
                return {"available": False, "reason": "caption-backend-response-invalid"}
            return response

    # Strictly opt-in.  One Mailbox instance belongs to this MCPServer process,
    # so all sessions entering via the existing tunnel see the same messages.
    # Do not scale this server to multiple replicas or share an unauthenticated
    # MCP port: room IDs are capability identifiers, not user authentication.
    if peer_chat_enabled:
        from shared_alfred_gateway.peer_chat import Mailbox, MAX_WAIT

        peer_mailbox = Mailbox()

        @server.tool()
        async def chat_create() -> dict:
            """Open one short-lived two-role room (do not publish its room ID)."""
            try:
                return peer_mailbox.create_room()
            except OverflowError:
                return {"status": "error", "reason": "room-limit"}

        @server.tool()
        async def chat_send(
            room_id: str, sender: str, message: str, reply_to: int | None = None
        ) -> dict:
            """Post a bounded inert text message as A or B; never execute it."""
            try:
                return peer_mailbox.send(room_id, sender, message, reply_to)
            except (ValueError, OverflowError) as exc:
                return {"status": "error", "reason": str(exc)}

        @server.tool()
        async def chat_wait(
            room_id: str, role: str, after_id: int = 0,
            wait_seconds: int = 15,
        ) -> dict:
            """Block <=20 seconds waiting for the other role's next message."""
            if type(wait_seconds) is not int or not 0 <= wait_seconds <= MAX_WAIT:
                return {"status": "error", "reason": "invalid-wait"}
            try:
                return await asyncio.to_thread(
                    peer_mailbox.wait, room_id, role, after_id, wait_seconds
                )
            except ValueError as exc:
                return {"status": "error", "reason": str(exc)}

        @server.tool()
        async def chat_close(room_id: str) -> dict:
            """Close an ephemeral peer-chat room and wake pending waits."""
            try:
                return peer_mailbox.close(room_id)
            except ValueError as exc:
                return {"status": "error", "reason": str(exc)}

    return server


def main() -> None:
    if os.environ.get("ALFRED_SHARED_MCP_ENABLED") != "1":
        raise SystemExit("shared-gateway-not-activated")
    host = os.environ.get("ALFRED_SHARED_BIND", "127.0.0.1")
    if host not in {"127.0.0.1", "0.0.0.0"}:
        raise SystemExit("unsafe-bind-host")
    port = int(os.environ.get("ALFRED_SHARED_PORT", "8766"))
    if not 1024 <= port <= 65535:
        raise SystemExit("invalid-shared-port")
    active = os.environ.get("ALFRED_CAPTION_MCP_ENABLED") == "1"
    gateway = make_server(
        atlas_url=os.environ.get("ALFRED_ATLAS_URL", "http://app:8765/mcp"),
        caption_url=os.environ.get("ALFRED_CAPTION_URL") if active else None,
        captions_enabled=active,
        peer_chat_enabled=os.environ.get("ALFRED_PEER_CHAT_MCP_ENABLED") == "1",
    )
    gateway.run(
        transport="streamable-http", host=host, port=port,
        stateless_http=True, json_response=True, max_request_body_size=16384,
    )


if __name__ == "__main__":
    main()
