"""Exact Host-list and production image source-invariant tests."""
import os
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from alfred_gateway_core.transport_security import trusted_host_headers, transport_security
from alfred_gateway_core.peer_backend import mailbox, server


class ProductionSecurityTests(unittest.TestCase):
    def test_fails_without_reviewed_allowed_hosts(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                transport_security()

    def test_exact_rfc1918_nics_and_dns_rebinding_still_enabled(self):
        allowed = "172.30.143.10:8765,172.30.144.10:8766"
        with patch.dict(os.environ, {"ALFRED_PEER_TRUSTED_HOSTS": allowed}):
            settings = transport_security()
        self.assertTrue(settings.enable_dns_rebinding_protection)
        self.assertEqual(settings.allowed_hosts, [
            "127.0.0.1:18765",
            "172.30.143.10:8765",
            "172.30.144.10:8766",
        ])
        self.assertEqual(settings.allowed_origins, [
            "http://127.0.0.1:18765",
            "http://172.30.143.10:8765",
            "http://172.30.144.10:8766",
        ])

    def test_forbids_unreviewed_domains_wildcards_public_ips(self):
        unsafe = [
            "", "0.0.0.0:8765", "*", "*:8765", "127.0.0.1:8765",
            "localhost:8765", "8.8.8.8:8765", "172.30.143.10:*",
            "172.30.143.10:9876", "172.30.143.10:8765,172.30.143.10:8765",
            "1.1.1.1:8766", "172.30.143.10:8765,8.8.8.8:8766",
            "172.30.143.10:8765,172.30.144.10:8766,10.0.0.3:8765",
            "172.30.143.10:8765/../../",
        ]
        for raw in unsafe:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                trusted_host_headers(raw)

    def test_backend_does_not_register_test_issuer_or_admin_tools(self):
        from inspect import iscoroutinefunction
        names = {tool.name for tool in server._tool_manager.list_tools()}
        self.assertEqual(names, {
            "chat_create", "chat_join", "chat_send",
            "chat_wait", "chat_status", "chat_close",
        })
        self.assertTrue(iscoroutinefunction(server._tool_manager.get_tool("chat_wait").fn))
        self.assertFalse(hasattr(mailbox, "exec"))
        self.assertFalse(hasattr(mailbox, "issue_token"))



class StrictOwnerAndExternalPolicyTests(unittest.TestCase):
    """One owner API key; a separate external JWT/limited scope identity."""

    @staticmethod
    def owner():
        return {
            "name": "native-owner-route",
            "gateways": ["native-owner"],
            "matches": [{"path": {"pathPrefix": "/mcp"}}],
            "policies": {
                "apiKey": {
                    "mode": "strict",
                    "location": {
                        "header": {"name": "x-alfred-native-owner-key"}
                    },
                    "keys": [{
                        "keyHash": "sha256:" + "a" * 64,
                        "metadata": {"role": "owner"},
                    }],
                },
                "mcpAuthorization": {"rules": ['apiKey.role == "owner"']},
            },
            "backends": [{
                "mcp": {"targets": [{
                    "name": "peer-chat",
                    "mcp": {"host": "http://127.0.0.1:18765/mcp"},
                }]}
            }],
        }

    @staticmethod
    def external():
        route = StrictOwnerAndExternalPolicyTests.owner()
        route["name"] = "cloudflare-external-route"
        route["gateways"] = ["cloudflare-external"]
        route["policies"] = {
            "mcpAuthentication": {
                "mode": "strict",
                "issuer": "https://external-ci.alfred.test",
                "audiences": ["http://172.30.144.10:8766/mcp"],
                "jwks": {"file": "/run/alfred-auth/external-jwks.json"},
                "jwtValidationOptions": {
                    "requiredClaims": ["exp", "nbf", "aud", "sub"]
                },
                "resourceMetadata": {
                    "resource": "http://172.30.144.10:8766/mcp",
                    "scopesSupported": [
                        "chat:join", "chat:send", "chat:wait", "chat:status",
                    ],
                    "bearerMethodsSupported": ["header"],
                },
            },
            "mcpAuthorization": {
                "rules": [
                    'mcp.tool.name == "chat_join" && "chat:join" in jwt.scopes',
                    'mcp.tool.name == "chat_send" && "chat:send" in jwt.scopes',
                    'mcp.tool.name == "chat_wait" && "chat:wait" in jwt.scopes',
                    'mcp.tool.name == "chat_status" && "chat:status" in jwt.scopes',
                ],
            },
        }
        return route

    def test_owner_only_does_not_need_an_owner_jwks_or_issuer(self):
        from alfred_gateway_core.validate_policy import (
            validate, validate_route,
        )
        targets = {"peer-chat": "http://127.0.0.1:18765/mcp"}
        owner = self.owner()
        validate_route(
            owner, key="native-owner", host="172.30.143.10:8765",
            allowed_targets=targets,
        )
        validate({
            "config": {"adminAddr": "127.0.0.1:15078"},
            "gateways": {
                "native-owner": {
                    "bindAddress": "172.30.143.10", "port": 8765,
                },
            },
            "routes": [owner],
        }, allowed_hosts=("172.30.143.10:8765",),
           target_inventory=targets, external_public_jwks=None)

    def test_wrong_owner_role_header_mode_hash_or_fallback_rejected(self):
        from alfred_gateway_core.validate_policy import InvalidPolicy, validate_route
        base = self.owner()
        candidates = []
        def changed(path, value):
            copy = deepcopy(base)
            item = copy
            for key in path[:-1]:
                item = item[key]
            item[path[-1]] = value
            candidates.append(copy)
        changed(("policies", "apiKey", "mode"), "permissive")
        changed(("policies", "apiKey", "mode"), "optional")
        changed(("policies", "apiKey", "location"),
                {"header": {"name": "authorization"}})
        changed(("policies", "apiKey", "location"),
                {"header": {"name": "x-alfred-native-owner-key",
                            "prefix": "Bearer "}})
        changed(("policies", "apiKey", "keys"), [])
        changed(("policies", "apiKey", "keys"),
                [base["policies"]["apiKey"]["keys"][0]] * 2)
        changed(("policies", "apiKey", "keys"),
                [{"key": "plaintext-test-key", "metadata": {"role": "owner"}}])
        changed(("policies", "apiKey", "keys"),
                [{"keyHash": "sha256:abc", "metadata": {"role": "owner"}}])
        changed(("policies", "apiKey", "keys"),
                [{"keyHash": "sha256:" + "A"*64,
                  "metadata": {"role": "owner"}}])
        changed(("policies", "apiKey", "keys"),
                [{"keyHash": "sha256:" + "a"*64,
                  "metadata": {"role": "admin"}}])
        changed(("policies", "mcpAuthorization"),
                {"rules": ['true']})
        changed(("policies", "mcpAuthorization"),
                {"rules": ['jwt.sub == "alfred-native-owner"']})
        changed(("policies", "mcpAuthentication"), {
            "mode": "permissive"
        })
        for candidate in candidates:
            with self.subTest(policy=candidate["policies"]), self.assertRaises(InvalidPolicy):
                validate_route(candidate, key="native-owner",
                               host="172.30.143.10:8765",
                               allowed_targets={"peer-chat": "http://127.0.0.1:18765/mcp"})

    def test_external_scopes_remain_strict_and_cannot_become_owner(self):
        from alfred_gateway_core.validate_policy import InvalidPolicy, validate_route
        base = self.external()
        targets = {"peer-chat": "http://127.0.0.1:18765/mcp"}
        validate_route(base, key="cloudflare-external",
                       host="172.30.144.10:8766",
                       allowed_targets=targets)
        invalids = []
        for key, value in (
            ("mode", "permissive"),
            ("issuer", "https://forged-owner.example"),
            ("audiences", ["http://172.30.143.10:8765/mcp"]),
            ("jwks", {"file": "/run/alfred-auth/owner-jwks.json"}),
        ):
            copy = deepcopy(base)
            copy["policies"]["mcpAuthentication"][key] = value
            invalids.append(copy)
        for expr in (
            'apiKey.role == "owner"',
            'mcp.tool.name == "chat_create" && "alfred:owner" in jwt.scopes',
            'mcp.tool.name == "chat_close" && "chat:close" in jwt.scopes',
        ):
            copy = deepcopy(base)
            copy["policies"]["mcpAuthorization"]["rules"].append(expr)
            invalids.append(copy)
        copy = deepcopy(base)
        copy["policies"]["apiKey"] = self.owner()["policies"]["apiKey"]
        invalids.append(copy)
        for candidate in invalids:
            with self.subTest(route=candidate["policies"]), self.assertRaises(InvalidPolicy):
                validate_route(candidate, key="cloudflare-external",
                               host="172.30.144.10:8766",
                               allowed_targets=targets)

    def test_shipped_entrypoint_enforces_owner_hash_not_owner_jwks(self):
        src = (Path(__file__).resolve().parents[1] /
               "release/agentgateway-peer/entrypoint.sh").read_text()
        self.assertIn("python -m alfred_gateway_core.validate_policy", src)
        self.assertIn("/usr/local/bin/agentgateway --validate-only", src)
        self.assertNotIn("owner-jwks.json", src)
        self.assertNotIn("permissive", src)
        self.assertNotIn("API_KEY=", src)


if __name__ == "__main__":
    unittest.main()
