"""Operator-side fail-closed safety validation for Agentgateway release configuration.

Run before starting gateway AND before every config/policy hot activation.
This checks fixed trust domains and approved target manifest; Agentgateway
separately validates its own schema and cryptographic JWT signatures.
No secret bearer, URL fetching, runtime role header or shell execution.
"""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
import json
from pathlib import Path
import re
import sys
from typing import Any

import yaml

from .transport_security import trusted_host_headers

PRIVATE = tuple(IPv4Network(x) for x in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"
))
SCOPE_RULE = re.compile(
    r'^mcp\.tool\.name == "([a-z][a-z0-9_]{1,39})" && "([a-z][a-z0-9:_-]{1,80})" in jwt\.scopes$'
)
OWNER_RULE = 'jwt.sub == "alfred-native-owner" && "alfred:owner" in jwt.scopes'
TARGET_NAME = re.compile(r"[a-z][a-z0-9_-]{1,55}\Z")


class InvalidPolicy(ValueError):
    pass


class NoDuplicateLoader(yaml.SafeLoader):
    pass


def unique_map(loader: yaml.Loader, node: yaml.Node) -> dict:
    output = {}
    for k, v in node.value:
        key = loader.construct_object(k)
        if not isinstance(key, str) or key in output:
            raise InvalidPolicy("invalid-or-duplicate-config-key")
        output[key] = loader.construct_object(v)
    return output


NoDuplicateLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_map,
)


def require(test: bool, reason: str) -> None:
    if not test:
        raise InvalidPolicy(reason)


def load_doc(path: Path, *, max_bytes: int = 32768) -> dict:
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise InvalidPolicy("unavailable-or-too-large-config")
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=NoDuplicateLoader)
    except (yaml.YAMLError, UnicodeError, OSError) as exc:
        raise InvalidPolicy("malformed-config") from exc
    require(type(data) is dict, "config-must-be-object")
    return data


def validate_public_jwks(path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise InvalidPolicy("missing-or-invalid-public-jwks") from exc
    keys = data.get("keys") if isinstance(data, dict) else None
    require(type(keys) is list and 1 <= len(keys) <= 8, "invalid-jwks-size")
    seen = set()
    for key in keys:
        require(type(key) is dict, "invalid-jwk-object")
        require(key.get("kty") == "RSA" and
                isinstance(key.get("n"), str) and
                isinstance(key.get("e"), str) and
                isinstance(key.get("kid"), str), "invalid-jwk-public-fields")
        require("d" not in key and "p" not in key and
                "q" not in key and "dp" not in key and "dq" not in key,
                "private-jwk-key-forbidden")
        require(key["kid"] not in seen, "duplicate-jwk-kid")
        seen.add(key["kid"])


def ipv4_private(raw: str) -> bool:
    try:
        addr = IPv4Address(raw)
        return str(addr) == raw and any(addr in net for net in PRIVATE)
    except (ValueError, TypeError):
        return False


def approved_targets(path: Path) -> dict[str, str]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise InvalidPolicy("missing-reviewed-target-inventory") from exc
    require(type(obj) is dict and set(obj) == {"schema", "targets"}
            and obj["schema"] == 1, "invalid-target-inventory")
    items = obj["targets"]
    require(type(items) is list and 1 <= len(items) <= 16,
            "unbounded-reviewed-targets")
    allowed = {}
    for item in items:
        require(type(item) is dict and set(item) == {"name", "host"},
                "invalid-target-entry")
        name, host = item["name"], item["host"]
        require(type(name) is str and TARGET_NAME.fullmatch(name) is not None,
                "invalid-target-name")
        require(type(host) is str and len(host) < 160,
                "invalid-target-URL")
        # At present the only mandatory backend is a single localhost
        # Peer Chat target. Other approved backends can be fixed private
        # RFC1918 TCP origins; no DNS, Internet or arbitrary URLs.
        from urllib.parse import urlsplit
        try:
            url = urlsplit(host)
            raw_host = url.hostname or ""
            port = url.port
        except ValueError:
            raise InvalidPolicy("invalid-target-URL")
        require(url.scheme == "http" and
                url.username is None and url.password is None and
                url.path == "/mcp" and not url.query and not url.fragment,
                "unexpected-upstream-URL")
        require((raw_host == "127.0.0.1" and port == 18765) or
                (ipv4_private(raw_host) and port in range(1024, 65536)),
                "unapproved-upstream-origin")
        require(name not in allowed, "duplicate-target-name")
        allowed[name] = host
    require(allowed.get("peer-chat") == "http://127.0.0.1:18765/mcp",
            "missing-private-Peer-Chat")
    return allowed


def validate_route(route: Any, *, key: str, host: str,
                   allowed_targets: dict[str, str]) -> None:
    require(type(route) is dict and set(route) == {
        "name", "gateways", "matches", "policies", "backends"
    }, "unexpected-route-fields")
    require(route["name"] == key + "-route" and
            route["gateways"] == [key], "wrong-route-binding")
    require(route["matches"] == [{"path": {"pathPrefix": "/mcp"}}],
            "unsafe-path-match")
    policy = route["policies"]
    require(type(policy) is dict and set(policy) == {
        "mcpAuthentication", "mcpAuthorization"
    }, "missing-auth-policy")
    auth = policy["mcpAuthentication"]
    require(type(auth) is dict and
            set(auth) == {"mode", "issuer", "audiences", "jwks",
                          "jwtValidationOptions", "resourceMetadata"},
            "invalid-mcp-authentication")
    require(auth["mode"] == "strict", "non-strict-mcp-authentication")
    require(type(auth["issuer"]) is str and
            auth["issuer"].startswith("https://") and
            ".invalid" not in auth["issuer"] and
            ".example" not in auth["issuer"] and
            len(auth["issuer"]) < 180, "unreviewed-issuer")
    aud = "http://" + host + "/mcp"
    require(auth["audiences"] == [aud], "incorrect-JWT-audience")
    jwks_file = ("/run/alfred-auth/owner-jwks.json" if key == "native-owner"
                 else "/run/alfred-auth/external-jwks.json")
    require(auth["jwks"] == {"file": jwks_file}, "unreviewed-JWKS-source")
    require(auth["jwtValidationOptions"] == {
        "requiredClaims": ["exp", "nbf", "aud", "sub"]
    }, "missing-required-JWT-claims")
    md = auth["resourceMetadata"]
    require(type(md) is dict and md.get("resource") == aud and
            md.get("bearerMethodsSupported") == ["header"] and
            set(md) == {"resource", "scopesSupported", "bearerMethodsSupported"},
            "unsafe-MCP-resource-metadata")
    scopes = md["scopesSupported"]
    require(type(scopes) is list and scopes and len(scopes) <= 48 and
            all(type(x) is str for x in scopes), "invalid-resource-scopes")

    az = policy["mcpAuthorization"]
    require(type(az) is dict and set(az) == {"rules"}, "invalid-authorization")
    rules = az["rules"]
    require(type(rules) is list and 1 <= len(rules) <= 64 and
            all(type(x) is str for x in rules), "invalid-authorization-rules")
    if key == "native-owner":
        require(rules == [OWNER_RULE] and scopes == ["alfred:owner"],
                "owner-policy-exceeds-approved-contract")
    else:
        found = set()
        for expr in rules:
            match = SCOPE_RULE.fullmatch(expr)
            require(match is not None, "unreviewed-external-CEL-expression")
            tool, scope = match.groups()
            require(not scope.startswith(("infra:", "alfred:")) and
                    ":" in scope and tool not in found and
                    scope in scopes, "scope-escalation-or-tool-duplicate")
            found.add(tool)
        require("chat_create" not in found and "chat_close" not in found and
                "owner_marker" not in found, "external-owner-tool-exposure")

    backend = route["backends"]
    require(type(backend) is list and len(backend) == 1 and
            type(backend[0]) is dict and set(backend[0]) == {"mcp"},
            "unreviewed-backend-config")
    mcp = backend[0]["mcp"]
    require(type(mcp) is dict and set(mcp) == {"targets"},
            "unreviewed-MCP-backend-config")
    targets = mcp["targets"]
    require(type(targets) is list and 1 <= len(targets) <= 16,
            "invalid-target-count")
    actual = {}
    for target in targets:
        require(type(target) is dict and set(target) == {"name", "mcp"},
                "unreviewed-target-configuration")
        name = target["name"]
        require(type(name) is str and name not in actual and
                name in allowed_targets, "unapproved-target")
        require(target["mcp"] == {"host": allowed_targets[name]},
                "unapproved-MCP-target-URL")
        actual[name] = target["mcp"]["host"]
    require("peer-chat" in actual, "missing-peer-chat-target")


def validate(config: dict, *, allowed_hosts: tuple[str, ...],
             target_inventory: dict[str, str],
             owner_public_jwks: Path,
             external_public_jwks: Path | None) -> None:
    require(set(config) == {"config", "gateways", "routes"},
            "unexpected-root-config-key")
    root_settings = config["config"]
    require(type(root_settings) is dict and
            root_settings == {"adminAddr": "127.0.0.1:15078"},
            "unreviewed-admin-listener")
    gateways = config["gateways"]
    require(type(gateways) is dict and
            "native-owner" in gateways and
            set(gateways) <= {"native-owner", "cloudflare-external"},
            "invalid-gateway-definition")
    require(1 <= len(allowed_hosts) <= 2, "invalid-private-interface-count")
    require(len(gateways) == len(allowed_hosts),
            "unapproved-extra-listener")
    expected = {"native-owner": ("8765", allowed_hosts[0])}
    if len(allowed_hosts) == 2:
        expected["cloudflare-external"] = ("8766", allowed_hosts[1])
    for name, (port, host) in expected.items():
        ip, exact_port = host.rsplit(":", 1)
        require(exact_port == port and
                gateways.get(name) == {
                    "bindAddress": ip, "port": int(port)
                }, "listener-binding-bypass")
    require(type(config["routes"]) is list and
            len(config["routes"]) == len(gateways), "invalid-route-count")
    seen = set()
    for route in config["routes"]:
        require(type(route) is dict and
                route.get("name") in {x + "-route" for x in gateways},
                "foreign-route-name")
        key = route["name"].removesuffix("-route")
        require(key not in seen, "duplicate-route")
        host = expected[key][1]
        validate_route(route, key=key, host=host,
                       allowed_targets=target_inventory)
        seen.add(key)
    require(seen == set(gateways), "missing-trust-domain")
    validate_public_jwks(owner_public_jwks)
    if "cloudflare-external" in gateways:
        require(external_public_jwks is not None,
                "missing-external-public-jwks")
        validate_public_jwks(external_public_jwks)


def check_files(config: Path, inventory: Path, owner_jwks: Path,
                external_jwks: Path | None, hosts: str) -> None:
    validate(
        load_doc(config), allowed_hosts=trusted_host_headers(hosts),
        target_inventory=approved_targets(inventory),
        owner_public_jwks=owner_jwks, external_public_jwks=external_jwks
    )


if __name__ == "__main__":
    if len(sys.argv) != 6:
        raise SystemExit("usage: validate_policy.py config.yaml targets.json owner-jwks.json optional-external-jwks.json HostList")
    try:
        check_files(
            Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]),
            None if sys.argv[4] == "-" else Path(sys.argv[4]), sys.argv[5],
        )
    except (InvalidPolicy, ValueError, yaml.YAMLError) as exc:
        # Error type/category only, never echo content from a potentially
        # credential-bearing operator file or original exception details.
        print("blocked-unapproved-policy:" + type(exc).__name__, file=sys.stderr)
        sys.exit(47)
    print("reviewed-policy-structure=accepted; no-token-material-processed=true")
