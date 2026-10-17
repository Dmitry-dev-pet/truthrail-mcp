"""Stable MCP tools, registered once; caller identity is supplied by a *trusted* edge.

The getter is deliberately not a tool argument. Production must integrate
the native tunnel identity and OAuth/Bearer validation per HTTP request
before this code is enabled. No installation/reload endpoint is exposed.
"""
from __future__ import annotations

from typing import Callable
from .identity import Denied, Principal
from .runtime import AccessPlane


def register_access_tools(server, plane: AccessPlane,
                          trusted_principal: Callable[[], Principal]) -> None:
    @server.tool()
    async def capabilities_list() -> dict:
        """List authorized versioned capabilities (stable interface)."""
        try:
            return plane.list(trusted_principal())
        except Denied:
            return {"available": False, "reason": "forbidden"}

    @server.tool()
    async def capabilities_read(capability: str, action: str, parameters: dict) -> dict:
        """Invoke a read action with server-side scope/resource policy."""
        try:
            return await plane.invoke(trusted_principal(), capability, action, "read", parameters)
        except Denied as exc:
            return {"available": False, "reason": str(exc)}

    @server.tool()
    async def capabilities_write(capability: str, action: str, parameters: dict) -> dict:
        """Invoke a write action; can require native ChatGPT user approval."""
        try:
            return await plane.invoke(trusted_principal(), capability, action, "write", parameters)
        except Denied as exc:
            return {"available": False, "reason": str(exc)}

    @server.tool()
    async def capabilities_wait(capability: str, action: str, parameters: dict) -> dict:
        """Wait at most 20s for a scoped peer message via server-side policy."""
        try:
            return await plane.invoke(trusted_principal(), capability, action, "wait", parameters)
        except Denied as exc:
            return {"available": False, "reason": str(exc)}
