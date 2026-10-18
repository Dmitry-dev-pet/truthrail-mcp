"""Strict exact-host security for the loopback-only Peer Chat MCP backend.

Only approved RFC1918 IP:port Host headers forwarded by private Agentgateway
routes may be added. Never accept wildcard, DNS name or public address from
an unreviewed environment variable. This is NOT external user authentication.
"""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
import os
import re

from mcp.server.transport_security import TransportSecuritySettings

OWNER_PORT = 8765
EXTERNAL_PORT = 8766
BACKEND_PORT = 18765
PRIVATE_NETS = tuple(IPv4Network(net) for net in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"
))
HOST_RE = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}:(?:8765|8766)\Z")


def trusted_host_headers(raw: str | None) -> tuple[str, ...]:
    if raw is None or raw.strip() == "":
        raise ValueError("missing-review-approved-gateway-Host-list")
    values = raw.split(",")
    if not 1 <= len(values) <= 2:
        raise ValueError("invalid-number-of-gateway-Hosts")
    allowed = []
    for item in values:
        host = item.strip()
        if not HOST_RE.fullmatch(host):
            raise ValueError("unsafe-gateway-Host-syntax")
        ip_text, port_text = host.rsplit(":", 1)
        ip = IPv4Address(ip_text)
        if str(ip) != ip_text or not any(ip in net for net in PRIVATE_NETS):
            raise ValueError("non-RFC1918-gateway-Host")
        if port_text not in (str(OWNER_PORT), str(EXTERNAL_PORT)):
            raise ValueError("unexpected-gateway-Host-port")
        if host in allowed:
            raise ValueError("duplicate-gateway-Host")
        allowed.append(host)
    return tuple(allowed)


def transport_security() -> TransportSecuritySettings:
    hosts = trusted_host_headers(os.environ.get("ALFRED_PEER_TRUSTED_HOSTS"))
    allowed = ["127.0.0.1:18765", *hosts]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed,
        allowed_origins=["http://" + host for host in allowed],
    )
